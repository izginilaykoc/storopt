"""
Two-stage stochastic MILP for day-ahead + intraday battery dispatch.

Ported from bess_opt/baseline_model.py (build_baseline_model / solve_model /
extract_results). The formulation is unchanged; the interface is adapted to
accept RunConfig + ScenarioBundle directly.

Formulation summary
-------------------
Sets: T = {1..n_periods}, S = {1..n_scenarios}

Stage-1 (non-anticipative):
    q_da[t]          DA net position [MW]

Stage-2 (scenario-dependent):
    q_id[s, t]       Intraday net trade [MW]
    p_ch[s, t]       Charge power [MW]
    p_dis[s, t]      Discharge power [MW]
    soc[s, t]        State of charge [MWh]
    delta[s, t]      Binary: 1 = charging (mutual exclusivity)

Objective (maximize):
    E[profit] - degradation - optional CVaR penalty

Key constraints:
    C1  Energy balance: q_da[t] + q_id[s,t] = G[s,t] + p_dis[s,t] - p_ch[s,t]
    C2  SOC dynamics:   soc[s,t] = soc[s,t-1] + eta_ch*p_ch[s,t-1]*dt - p_dis[s,t-1]/eta_dis*dt
    C3  Initial SOC:    soc[s,1] = soc_init
    C4  Terminal SOC:   soc[s,T] + eta_ch*p_ch[s,T]*dt - p_dis[s,T]/eta_dis*dt = soc_init
    C5  Mutual excl.:   p_ch <= P_max_ch * delta; p_dis <= P_max_dis * (1 - delta)
    C6  Cycle cap:      sum(p_ch + p_dis)*dt <= 2 * max_cycles_per_day * E_max  (optional)
    CVaR constraints when cvar_weight > 0
"""

from __future__ import annotations

import time

import numpy as np
import pyomo.environ as pyo

from storopt.config.schema import RunConfig
from storopt.optimizer.base import Optimizer
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle


def _build(bundle: ScenarioBundle, cfg: RunConfig) -> pyo.ConcreteModel:
    bess = cfg.bess
    market = cfg.market
    opt_cfg = cfg.optimizer

    S_n = bundle.n_scenarios
    T_n = market.n_periods

    m = pyo.ConcreteModel("StoroptMILP")
    m.T = pyo.RangeSet(1, T_n)
    m.S = pyo.RangeSet(1, S_n)

    # --- Parameters ---
    m.prob = pyo.Param(m.S, initialize={s + 1: float(bundle.probabilities[s]) for s in range(S_n)})
    m.da_price = pyo.Param(
        m.S, m.T,
        initialize={(s + 1, t + 1): float(bundle.da_prices[s, t]) for s in range(S_n) for t in range(T_n)},
    )
    m.id_price = pyo.Param(
        m.S, m.T,
        initialize={(s + 1, t + 1): float(bundle.id_prices[s, t]) for s in range(S_n) for t in range(T_n)},
    )
    m.res_gen = pyo.Param(
        m.S, m.T,
        initialize={(s + 1, t + 1): float(bundle.res_generation[s, t]) for s in range(S_n) for t in range(T_n)},
    )

    m.dt = pyo.Param(initialize=market.dt_hours)
    m.P_max_ch = pyo.Param(initialize=bess.power_charge_mw)
    m.P_max_dis = pyo.Param(initialize=bess.power_discharge_mw)
    m.soc_min = pyo.Param(initialize=bess.soc_min_mwh)
    m.soc_max = pyo.Param(initialize=bess.soc_max_mwh)
    m.soc_init = pyo.Param(initialize=bess.soc_init_mwh)
    m.eta_ch = pyo.Param(initialize=bess.eta_charge)
    m.eta_dis = pyo.Param(initialize=bess.eta_discharge)
    m.c_deg = pyo.Param(initialize=bess.deg_cost_eur_per_mwh)

    # --- Variable bounds ---
    max_gen = float(bundle.res_generation.max()) + bess.power_discharge_mw
    max_load = float(bundle.res_generation.max()) + bess.power_charge_mw

    id_lb, id_ub = -max_load, max_gen
    if market.id_position_cap_mw is not None:
        cap = float(market.id_position_cap_mw)
        id_lb = max(id_lb, -cap)
        id_ub = min(id_ub, cap)

    # --- First-stage ---
    m.q_da = pyo.Var(m.T, within=pyo.Reals, bounds=(-max_load, max_gen))

    # --- Second-stage ---
    m.q_id = pyo.Var(m.S, m.T, within=pyo.Reals, bounds=(id_lb, id_ub))
    m.p_ch = pyo.Var(m.S, m.T, within=pyo.NonNegativeReals, bounds=(0, bess.power_charge_mw))
    m.p_dis = pyo.Var(m.S, m.T, within=pyo.NonNegativeReals, bounds=(0, bess.power_discharge_mw))
    m.soc = pyo.Var(m.S, m.T, bounds=(bess.soc_min_mwh, bess.soc_max_mwh))
    m.delta = pyo.Var(m.S, m.T, within=pyo.Binary)

    # --- Optional linear ID penalty ---
    use_id_pen = (
        market.id_linear_penalty_eur_per_mwh is not None
        and market.id_linear_penalty_eur_per_mwh > 0
    )
    if use_id_pen:
        ub = max(abs(id_lb), abs(id_ub))
        m.q_id_pos = pyo.Var(m.S, m.T, within=pyo.NonNegativeReals, bounds=(0, ub))
        m.q_id_neg = pyo.Var(m.S, m.T, within=pyo.NonNegativeReals, bounds=(0, ub))
        m.id_decomposition = pyo.Constraint(
            m.S, m.T, rule=lambda m, s, t: m.q_id[s, t] == m.q_id_pos[s, t] - m.q_id_neg[s, t]
        )
        m.c_id_lin = pyo.Param(initialize=float(market.id_linear_penalty_eur_per_mwh))

    # --- Optional CVaR ---
    use_cvar = opt_cfg.cvar_enabled and opt_cfg.cvar_weight > 0.0
    if use_cvar:
        m.cvar_alpha = pyo.Param(initialize=opt_cfg.cvar_alpha)
        m.cvar_weight = pyo.Param(initialize=opt_cfg.cvar_weight)
        m.eta = pyo.Var(within=pyo.Reals)
        m.cvar_short = pyo.Var(m.S, within=pyo.NonNegativeReals)

    # --- Scenario profit expression ---
    def _profit(m, s):
        expr = (
            sum(m.da_price[s, t] * m.q_da[t] * m.dt for t in m.T)
            + sum(m.id_price[s, t] * m.q_id[s, t] * m.dt for t in m.T)
            - sum(m.c_deg * (m.p_ch[s, t] + m.p_dis[s, t]) * m.dt for t in m.T)
        )
        if use_id_pen:
            expr -= sum(m.c_id_lin * (m.q_id_pos[s, t] + m.q_id_neg[s, t]) * m.dt for t in m.T)
        return expr

    if use_cvar:
        m.cvar_shortfall = pyo.Constraint(
            m.S, rule=lambda m, s: m.cvar_short[s] >= m.eta - _profit(m, s)
        )

    # --- Objective ---
    def _obj(m):
        exp_profit = sum(m.prob[s] * _profit(m, s) for s in m.S)
        if not use_cvar:
            return exp_profit
        cvar_term = m.eta - (1.0 / m.cvar_alpha) * sum(m.prob[s] * m.cvar_short[s] for s in m.S)
        return (1.0 - m.cvar_weight) * exp_profit + m.cvar_weight * cvar_term

    m.obj = pyo.Objective(rule=_obj, sense=pyo.maximize)

    # --- Constraints ---
    m.energy_balance = pyo.Constraint(
        m.S, m.T,
        rule=lambda m, s, t: (
            m.q_da[t] + m.q_id[s, t] == m.res_gen[s, t] + m.p_dis[s, t] - m.p_ch[s, t]
        ),
    )

    def _soc_dyn(m, s, t):
        if t == 1:
            return pyo.Constraint.Skip
        return m.soc[s, t] == (
            m.soc[s, t - 1]
            + m.eta_ch * m.p_ch[s, t - 1] * m.dt
            - (1.0 / m.eta_dis) * m.p_dis[s, t - 1] * m.dt
        )

    m.soc_dynamics = pyo.Constraint(m.S, m.T, rule=_soc_dyn)
    m.soc_initial = pyo.Constraint(m.S, rule=lambda m, s: m.soc[s, 1] == m.soc_init)

    T_last = m.T.last()
    m.soc_terminal = pyo.Constraint(
        m.S,
        rule=lambda m, s: (
            m.soc[s, T_last]
            + m.eta_ch * m.p_ch[s, T_last] * m.dt
            - (1.0 / m.eta_dis) * m.p_dis[s, T_last] * m.dt
            == m.soc_init
        ),
    )

    m.charge_limit = pyo.Constraint(
        m.S, m.T, rule=lambda m, s, t: m.p_ch[s, t] <= m.P_max_ch * m.delta[s, t]
    )
    m.discharge_limit = pyo.Constraint(
        m.S, m.T, rule=lambda m, s, t: m.p_dis[s, t] <= m.P_max_dis * (1 - m.delta[s, t])
    )

    if bess.max_cycles_per_day is not None:
        cap_mwh = 2.0 * float(bess.max_cycles_per_day) * bess.energy_capacity_mwh
        m.max_cycle_throughput_mwh = pyo.Param(initialize=cap_mwh)
        m.daily_throughput_cap = pyo.Constraint(
            m.S,
            rule=lambda m, s: sum((m.p_ch[s, t] + m.p_dis[s, t]) * m.dt for t in m.T)
            <= m.max_cycle_throughput_mwh,
        )

    return m


def _solve(model: pyo.ConcreteModel, cfg: RunConfig) -> tuple[str, float]:
    slv = cfg.solver
    if slv.name == "gurobi":
        opt = pyo.SolverFactory("gurobi")
        opt.options["TimeLimit"] = slv.time_limit_s
        opt.options["MIPGap"] = slv.mip_gap
        if slv.threads is not None:
            opt.options["Threads"] = slv.threads
    else:
        opt = pyo.SolverFactory("appsi_highs")
        opt.options["time_limit"] = float(slv.time_limit_s)
        opt.options["mip_rel_gap"] = slv.mip_gap
        if slv.threads is not None:
            opt.options["threads"] = slv.threads

    t0 = time.perf_counter()
    result = opt.solve(model, tee=slv.verbose)
    elapsed = time.perf_counter() - t0
    status = str(result.solver.termination_condition)
    return status, elapsed


def _extract(model: pyo.ConcreteModel, bundle: ScenarioBundle, cfg: RunConfig) -> OptimizationResult:
    S_n = bundle.n_scenarios
    T_n = cfg.market.n_periods
    dt = cfg.market.dt_hours

    da_bids = np.array([pyo.value(model.q_da[t]) for t in model.T])
    id_trades = np.array([[pyo.value(model.q_id[s, t]) for t in model.T] for s in model.S])
    charge = np.array([[pyo.value(model.p_ch[s, t]) for t in model.T] for s in model.S])
    discharge = np.array([[pyo.value(model.p_dis[s, t]) for t in model.T] for s in model.S])
    soc = np.array([[pyo.value(model.soc[s, t]) for t in model.T] for s in model.S])

    da_price_arr = bundle.da_prices
    id_price_arr = bundle.id_prices
    deg = cfg.bess.deg_cost_eur_per_mwh

    scenario_profits = np.array([
        np.sum(da_price_arr[s_i] * da_bids * dt)
        + np.sum(id_price_arr[s_i] * id_trades[s_i] * dt)
        - np.sum(deg * (charge[s_i] + discharge[s_i]) * dt)
        for s_i in range(S_n)
    ])
    expected_profit = float(np.dot(bundle.probabilities, scenario_profits))

    extras: dict = {}
    if hasattr(model, "eta"):
        alpha = float(pyo.value(model.cvar_alpha))
        shortfalls = np.array([pyo.value(model.cvar_short[s]) for s in model.S])
        probs = bundle.probabilities
        extras["cvar_eta"] = float(pyo.value(model.eta))
        extras["cvar_alpha"] = alpha
        extras["cvar_value_eur"] = extras["cvar_eta"] - (1.0 / alpha) * float(np.dot(probs, shortfalls))

    per_scen_throughput = np.array([(charge[i] + discharge[i]).sum() * dt for i in range(S_n)])
    extras["per_scenario_throughput_mwh"] = per_scen_throughput
    if hasattr(model, "max_cycle_throughput_mwh"):
        extras["cycle_cap_mwh"] = float(pyo.value(model.max_cycle_throughput_mwh))

    return OptimizationResult(
        da_bids=da_bids,
        id_trades=id_trades,
        charge=charge,
        discharge=discharge,
        soc=soc,
        scenario_profits=scenario_profits,
        probabilities=bundle.probabilities,
        expected_profit=expected_profit,
        solve_status="",  # filled by caller
        solve_time_s=0.0,  # filled by caller
        extras=extras,
    )


class StochasticMILP(Optimizer):
    """Two-stage stochastic MILP solved via Pyomo with HiGHS or Gurobi."""

    def solve(self, bundle: ScenarioBundle, config: RunConfig) -> OptimizationResult:
        bundle.validate()
        model = _build(bundle, config)
        status, elapsed = _solve(model, config)
        result = _extract(model, bundle, config)
        result.solve_status = status
        result.solve_time_s = elapsed
        return result
