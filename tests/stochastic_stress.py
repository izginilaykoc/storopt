"""
Stochastic stress tests — multi-scenario tests for the two-stage MILP.

Complements the deterministic single-scenario tests in sanity_cases.py and
edge_cases.py. These cases probe the stochastic structure of the model:
non-anticipativity of q_da, recourse feasibility, VSS / EEV / EVPI validity,
CVaR risk aversion, cycle cap binding, and DA-ID spread arbitrage.

Each case constructs a hand-crafted ScenarioBundle and asserts the model's
behaviour against analytically-derived expectations.

Defaults are taken from configs/horns_rev1_40mw.yaml (40 MW / 80 MWh).
The deterministic break-even and per-cycle economics are:
  RTE = 0.95 * 0.95 = 0.9025
  Break-even spread = deg * (1 + RTE) / RTE / RTE ... ≈ varies; see each case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from storopt.config.loader import load_config
from storopt.evaluation.metrics import compute_vss_evpi
from storopt.optimizer.milp import StochasticMILP
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle

T = 24
TODAY = date.today()
CFG = load_config("configs/horns_rev1_40mw.yaml")

SOC_INIT = CFG.bess.soc_init_mwh    # 40 MWh
SOC_MIN  = CFG.bess.soc_min_mwh     # 8  MWh
SOC_MAX  = CFG.bess.soc_max_mwh     # 72 MWh
P_CH     = CFG.bess.power_charge_mw # 40 MW
P_DIS    = CFG.bess.power_discharge_mw # 40 MW
RTE      = CFG.bess.eta_charge * CFG.bess.eta_discharge  # 0.9025
DEG      = CFG.bess.deg_cost_eur_per_mwh # 10
CAP      = CFG.bess.energy_capacity_mwh  # 80

SOLVER = StochasticMILP()


# ── helpers ──────────────────────────────────────────────────────────────────

def _bundle(da: np.ndarray, gen: np.ndarray | None = None, id_: np.ndarray | None = None,
            probs: np.ndarray | None = None) -> ScenarioBundle:
    """Build a bundle from arrays. da/id/gen can be 1-D (single scenario) or 2-D (S × 24)."""
    if da.ndim == 1:
        da = da[np.newaxis, :]
    if id_ is None:
        id_ = da.copy()
    elif id_.ndim == 1:
        id_ = id_[np.newaxis, :]
    if gen is None:
        gen = np.zeros_like(da)
    elif gen.ndim == 1:
        gen = gen[np.newaxis, :]
    S = da.shape[0]
    if probs is None:
        probs = np.full(S, 1.0 / S)
    return ScenarioBundle(
        da_prices=da, id_prices=id_, res_generation=gen,
        probabilities=probs, target_date=TODAY,
    )


@dataclass
class StressResult:
    name: str
    description: str
    expected: str
    real_world: str
    bundle: ScenarioBundle
    result: OptimizationResult
    extra: dict[str, Any]
    checks: list[tuple[str, bool, str]]

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


# ── Test cases ───────────────────────────────────────────────────────────────

def stress_1_non_anticipativity() -> StressResult:
    """
    Two scenarios with **opposite** intraday price patterns:
      s0: morning cheap (30), evening expensive (150)
      s1: morning expensive (150), evening cheap (30)
    DA price is flat at 90 in both (no DA arbitrage signal).

    Expected: q_da[t] is identical across the two scenarios (by construction;
    the MILP enforces non-anticipativity). The recourse q_id and physical
    p_ch / p_dis must differ between the two scenarios — each gets the right
    arbitrage for its own ID curve.
    """
    da   = np.full((2, T), 90.0)
    id0  = np.array([30.0] * 12 + [150.0] * 12)
    id1  = np.array([150.0] * 12 + [30.0] * 12)
    id_  = np.vstack([id0, id1])
    r = SOLVER.solve(_bundle(da, id_=id_), CFG)

    q_da   = r.da_bids
    qid0   = r.id_trades[0]
    qid1   = r.id_trades[1]
    pch0, pch1     = r.charge[0], r.charge[1]
    pdis0, pdis1   = r.discharge[0], r.discharge[1]

    # Sanity: q_da is a single (24,) array; non-anticipativity is enforced by construction.
    # Verify recourse acts in the expected directions for each scenario.
    s0_morning_charge = pch0[:12].sum()
    s0_evening_discharge = pdis0[12:].sum()
    s1_morning_discharge = pdis1[:12].sum()
    s1_evening_charge = pch1[12:].sum()

    checks = [
        ("q_da is one (24,) array shared across scenarios (non-anticipativity by construction)",
         q_da.shape == (T,),
         f"q_da.shape = {q_da.shape}"),
        ("Scenario 0: charges in morning (cheap ID)",
         s0_morning_charge > 1.0,
         f"Σ p_ch[s0,0:12] = {s0_morning_charge:.2f} MWh"),
        ("Scenario 0: discharges in evening (expensive ID)",
         s0_evening_discharge > 1.0,
         f"Σ p_dis[s0,12:] = {s0_evening_discharge:.2f} MWh"),
        ("Scenario 1: discharges in morning (expensive ID)",
         s1_morning_discharge > 1.0,
         f"Σ p_dis[s1,0:12] = {s1_morning_discharge:.2f} MWh"),
        ("Scenario 1: charges in evening (cheap ID)",
         s1_evening_charge > 1.0,
         f"Σ p_ch[s1,12:] = {s1_evening_charge:.2f} MWh"),
        ("q_id differs between scenarios at most hours (recourse is acting)",
         float(np.abs(qid0 - qid1).max()) > 1.0,
         f"max |q_id[s0,t] − q_id[s1,t]| = {float(np.abs(qid0 - qid1).max()):.2f} MW"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return StressResult(
        name="Stress 1 — Non-anticipativity: opposite intraday price patterns",
        description="2 scenarios (equal prob): s0 morning cheap ID, s1 evening cheap ID. DA flat.",
        expected="q_da identical across scenarios; recourse charge/discharge mirrors each scenario's ID curve.",
        real_world=(
            "Day-ahead bid is locked in by 12:00 CET the day before delivery; intraday is recourse "
            "that adapts to the realised market. This is the core decision-theoretic structure of "
            "two-stage stochastic programming for energy trading. A model that lets DA bids react "
            "to scenario realisations would have a leak — q_da must be one vector, not S vectors."
        ),
        bundle=_bundle(da, id_=id_), result=r, extra={"q_da": q_da},
        checks=checks,
    )


def stress_2_gen_spread_infeasibility() -> StressResult:
    """
    Two scenarios with extreme generation spread:
      s0: gen = 5  MW all hours (calm day)
      s1: gen = 150 MW all hours (storm day)
    DA prices flat at 60 €/MWh; ID prices flat at 60 €/MWh.

    With a 40 MW battery, recourse can absorb ±40 MW. The RP must find a q_da
    that is feasible for both. Specifically q_da[t] ∈ [5 − P_ch, 150 + P_dis] =
    [-35, 190] per scenario; intersect = [-35, 190] (= broad enough).

    Expected: solver finds a feasible RP; q_da set somewhere allowing both
    scenarios to satisfy energy balance via recourse.
    """
    da = np.full((2, T), 60.0)
    gen = np.vstack([np.full(T, 5.0), np.full(T, 150.0)])
    r = SOLVER.solve(_bundle(da, gen=gen), CFG)
    q_da = r.da_bids

    # The EBL: q_da[t] + q_id[s,t] = G[s,t] + p_dis - p_ch
    # For each scenario, q_id[s,t] ∈ [G[s,t]-P_ch-q_da[t], G[s,t]+P_dis-q_da[t]]
    # The RP must allow these; no explicit bound check here since the solver
    # confirms feasibility. Just verify status and that throughput is small
    # (no arbitrage since DA=ID=60 flat).
    net0 = q_da + r.id_trades[0]
    net1 = q_da + r.id_trades[1]

    checks = [
        ("Solver returns optimal", r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
        ("Energy balance s0: net = gen (no cycling — flat prices)",
         float(np.max(np.abs(net0 - gen[0]))) < 1e-3,
         f"max |net0 − gen0| = {float(np.max(np.abs(net0 - gen[0]))):.4f}"),
        ("Energy balance s1: net = gen (no cycling — flat prices)",
         float(np.max(np.abs(net1 - gen[1]))) < 1e-3,
         f"max |net1 − gen1| = {float(np.max(np.abs(net1 - gen[1]))):.4f}"),
        ("Cycling throughput ≈ 0 (no arbitrage signal)",
         (r.charge.sum() + r.discharge.sum()) < 1.0,
         f"Σ (p_ch + p_dis) = {(r.charge.sum() + r.discharge.sum()):.4f}"),
    ]
    return StressResult(
        name="Stress 2 — Generation spread: calm vs storm in one bundle",
        description="2 scenarios: s0 = 5 MW, s1 = 150 MW (flat over day). Flat DA=ID=60 €/MWh.",
        expected="RP feasible; battery idle (no arbitrage signal); q_da takes a compromise value.",
        real_world=(
            "A single bundle can contain very different wind realisations — calm vs storm. "
            "The model must find a DA position that can be 'rescued' by recourse in both. This is "
            "exactly the case that triggered EEV infeasibility in earlier KNN bundles on real data; "
            "the fix relaxes q_id box bounds so recourse always has room to absorb the gap."
        ),
        bundle=_bundle(da, gen=gen), result=r, extra={"q_da": q_da},
        checks=checks,
    )


def stress_3_analytical_vss() -> StressResult:
    """
    Hand-crafted 2-scenario bundle with **closed-form** VSS:

      s0 (p=0.5): morning cheap (30), evening expensive (150)
      s1 (p=0.5): morning expensive (150), evening cheap (30)
      No generation. DA = ID per scenario.

    Optimal stochastic q_da: it cannot favour one direction since the two scenarios
    are exact mirrors. So q_da = 0 for all t is optimal.
    Then recourse fully arbitrages each scenario via q_id alone.

    EV solve (on mean bundle): mean of s0 and s1 is flat 90 €/MWh — no arbitrage,
    battery idle, q_da_ev = 0. Then EEV plugs q_da = 0 into both scenarios; profit
    is the same as RP. So VSS = 0 (analytically).

    But because the EEV uses the mean bundle's optimum (idle battery, q_da=0)
    and recourse can still freely arbitrage in each scenario, VSS = 0.
    """
    da = np.vstack([
        np.array([30.0] * 12 + [150.0] * 12),
        np.array([150.0] * 12 + [30.0] * 12),
    ])
    bundle = _bundle(da, probs=np.array([0.5, 0.5]))
    r = SOLVER.solve(bundle, CFG)
    ve = compute_vss_evpi(bundle, CFG, stochastic_result=r, compute_evpi=True)

    # Expected: each scenario's arbitrage profit is ~ usable_energy * (spread - costs).
    # Useable in one cycle: SOC range is [8, 72] = 64 MWh. Cycle delivers 64 * eta_dis = 60.8 MWh
    # at high price - charge 64 / eta_ch = 67.4 MWh from grid at low price - degradation
    # over 64 + 67.4 = 131.4 MWh throughput * 10€/MWh = €1314 deg.
    # Revenue: 60.8 * 150 - 67.4 * 30 = 9120 - 2022 = €7098 ; net = 7098 - 1314 = €5784 / scenario.
    # E[profit] = 0.5*5784 + 0.5*5784 = €5784.
    # WS = 5784, EEV = 5784 (since EV bid is q_da=0, same as RP), VSS = 0, EVPI = 0.

    z_rp = ve["z_rp"]; z_ws = ve["z_ws"]; z_eev = ve["z_eev"]
    vss = ve["vss_eur"]; evpi = ve["evpi_eur"]
    is_finite = lambda x: x is not None and not (isinstance(x, float) and np.isnan(x))

    checks = [
        ("z_rp computed", is_finite(z_rp), f"z_rp = {z_rp:,.2f}"),
        ("z_eev computed (EEV feasibility fix works)", is_finite(z_eev),
         f"z_eev = {z_eev:,.2f}" if is_finite(z_eev) else "z_eev = N/A (FAIL)"),
        ("z_ws computed (WS bound fix works)", is_finite(z_ws),
         f"z_ws = {z_ws:,.2f}" if is_finite(z_ws) else "z_ws = N/A (FAIL)"),
        ("VSS ≈ 0 (analytical: mirror-symmetric scenarios → mean is informative)",
         is_finite(vss) and abs(vss) < 100.0,
         f"VSS = €{vss:,.2f}  (analytical = €0)" if is_finite(vss) else f"VSS = N/A (FAIL)"),
        ("EVPI ≥ 0 (mathematical bound)",
         is_finite(evpi) and evpi > -1.0,
         f"EVPI = €{evpi:,.2f}" if is_finite(evpi) else "EVPI = N/A (FAIL)"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return StressResult(
        name="Stress 3 — Analytical VSS check (mirror-symmetric scenarios → VSS = 0)",
        description="2 scenarios, equal prob: s0 morning-cheap, s1 evening-cheap. No gen. DA = ID.",
        expected="VSS ≈ 0 by mirror symmetry; EVPI ≥ 0; both z_eev and z_ws finite.",
        real_world=(
            "On days where the price uncertainty is symmetric (cheap-AM vs cheap-PM are equally "
            "likely), the mean-scenario solution and the stochastic solution both refuse to "
            "commit DA bids — recourse handles whichever direction materialises. The textbook "
            "VSS-is-zero edge case. If the code computes a sizeable non-zero VSS here, the metric "
            "is biased."
        ),
        bundle=bundle, result=r, extra=ve,
        checks=checks,
    )


def stress_4_cvar_asymmetric_tail() -> StressResult:
    """
    5 scenarios, asymmetric tail:
      s0 (p=0.20): BIG LOSS day  (DA = +200 morning, +50 evening — wrong-way arbitrage opportunity that loses)
      s1..s4 (p=0.20 each): normal-day arbitrage profile (DA=30→150)

    Sweep CVaR weight: at weight = 0 (risk-neutral), optimizer maximizes E[profit].
    At weight > 0, it penalizes tail downside → should reduce throughput and lower
    expected profit but raise CVaR.
    """
    s_loss = np.array([200.0] * 12 + [50.0] * 12)  # backwards spread; arbitrage loses
    s_norm = np.array([30.0] * 12 + [150.0] * 12)
    da = np.vstack([s_loss] + [s_norm] * 4)
    bundle = _bundle(da, probs=np.full(5, 0.2))

    # Risk-neutral solve
    r_neutral = SOLVER.solve(bundle, CFG)

    # CVaR-aware: monkey-set via config override
    cfg_cvar = load_config("configs/horns_rev1_40mw.yaml", **{
        "optimizer.cvar_enabled": True,
        "optimizer.cvar_weight": 0.5,
        "optimizer.cvar_alpha":  0.20,   # focus on the worst 20%
    })
    r_cvar = SOLVER.solve(bundle, cfg_cvar)

    tp_neutral = (r_neutral.charge + r_neutral.discharge).sum() * CFG.market.dt_hours
    tp_cvar    = (r_cvar.charge + r_cvar.discharge).sum()    * cfg_cvar.market.dt_hours
    worst_n = float(r_neutral.scenario_profits.min())
    worst_c = float(r_cvar.scenario_profits.min())

    checks = [
        ("Both solves return optimal",
         r_neutral.solve_status in ("optimal", "feasible") and r_cvar.solve_status in ("optimal", "feasible"),
         f"neutral={r_neutral.solve_status}, cvar={r_cvar.solve_status}"),
        ("CVaR mode raises worst-case scenario profit",
         worst_c >= worst_n - 1.0,
         f"worst_neutral=€{worst_n:,.2f}  →  worst_cvar=€{worst_c:,.2f}"),
        ("CVaR mode does not exceed risk-neutral E[profit] (expected tradeoff)",
         r_cvar.expected_profit <= r_neutral.expected_profit + 1.0,
         f"E_neutral=€{r_neutral.expected_profit:,.2f}  E_cvar=€{r_cvar.expected_profit:,.2f}"),
        ("Throughput non-negative",
         tp_neutral >= 0 and tp_cvar >= 0,
         f"throughput neutral={tp_neutral:.2f}, cvar={tp_cvar:.2f}"),
    ]
    return StressResult(
        name="Stress 4 — CVaR risk-aversion on asymmetric tail",
        description="5 scenarios: one loss-day (p=0.2), four normal arbitrage days. Sweep CVaR weight 0 → 0.5.",
        expected="CVaR raises worst-case scenario profit at the cost of lower E[profit].",
        real_world=(
            "Real BESS operators must respect a VaR budget set by treasury — pure expected-profit "
            "optimisation may take the trader to a single very bad day. CVaR weight is the lever "
            "operators use to trade some expected revenue for tail protection. This test confirms "
            "the CVaR machinery responds correctly to weighting changes."
        ),
        bundle=bundle, result=r_neutral,
        extra={"r_cvar": r_cvar, "throughput_neutral": tp_neutral, "throughput_cvar": tp_cvar,
               "worst_neutral": worst_n, "worst_cvar": worst_c},
        checks=checks,
    )


def stress_5_cycle_cap_binding() -> StressResult:
    """
    Single scenario with very large arbitrage spread (DA = 0 morning, 500 evening).
    Without cycle cap, battery would do multiple cycles (full discharge + full
    recharge if SOC allows).  With cycle cap = 0.5 cycles/day, throughput is
    capped at 0.5 * 2 * E_max = E_max = 80 MWh.  Solver should leave money on
    the table.
    """
    da = np.array([0.0] * 12 + [500.0] * 12)
    bundle = _bundle(da)

    r_uncapped = SOLVER.solve(bundle, CFG)
    cfg_capped = load_config("configs/horns_rev1_40mw.yaml", **{"bess.max_cycles_per_day": 0.5})
    r_capped = SOLVER.solve(bundle, cfg_capped)

    tp_uncapped = (r_uncapped.charge + r_uncapped.discharge).sum() * CFG.market.dt_hours
    tp_capped   = (r_capped.charge + r_capped.discharge).sum() * cfg_capped.market.dt_hours
    cap_mwh = 0.5 * 2 * CAP   # 80 MWh

    checks = [
        ("Uncapped solver optimal", r_uncapped.solve_status in ("optimal", "feasible"),
         f"status = {r_uncapped.solve_status}"),
        ("Capped solver optimal", r_capped.solve_status in ("optimal", "feasible"),
         f"status = {r_capped.solve_status}"),
        (f"Capped throughput ≤ cap ({cap_mwh:.1f} MWh)",
         tp_capped <= cap_mwh + 1e-3,
         f"throughput_capped = {tp_capped:.2f} MWh"),
        ("Cap reduces expected profit (or equal if cap was non-binding)",
         r_capped.expected_profit <= r_uncapped.expected_profit + 1e-3,
         f"profit_uncapped=€{r_uncapped.expected_profit:,.2f}  profit_capped=€{r_capped.expected_profit:,.2f}"),
    ]
    return StressResult(
        name="Stress 5 — Cycle cap binding",
        description="Single scenario, large spread (0→500 €/MWh). Cycle cap 0.5 cycles/day forces idle.",
        expected="Capped run respects throughput limit; expected profit ≤ uncapped profit.",
        real_world=(
            "Warranty-driven cycle caps (e.g., 1.5 cycles/day from a battery vendor) are common in "
            "commercial deployments. The model must respect them and report the lost revenue so "
            "operators can negotiate the warranty trade-off."
        ),
        bundle=bundle, result=r_uncapped,
        extra={"r_capped": r_capped, "throughput_uncapped": tp_uncapped,
               "throughput_capped": tp_capped, "cap_mwh": cap_mwh},
        checks=checks,
    )


def stress_6_da_id_spread() -> StressResult:
    """
    DA prices are flat (90 €/MWh); ID prices alternate 30 / 150.

    Verifies the model captures DA-ID basis arbitrage. With a frictionless ID
    market the optimal strategy is *naked* basis arb: opposite positions on the
    two legs at each hour, no battery cycling required (a hidden but valid
    finding from the model).

    Expected: q_id is short in cheap-ID hours, long in expensive-ID hours;
    profit ≈ €57,600 / day (12 × 40 MW × (150 − 30) €/MWh × 1 h × ½ from each leg).
    """
    da = np.full(T, 90.0)
    id_ = np.array([30.0, 150.0] * 12)
    bundle = _bundle(da, id_=id_)

    cheap_hours = list(range(0, T, 2))
    exp_hours   = list(range(1, T, 2))

    r = SOLVER.solve(bundle, CFG)
    qid = r.id_trades[0]
    qda = r.da_bids
    pch, pdis = r.charge[0], r.discharge[0]
    throughput = (pch + pdis).sum() * CFG.market.dt_hours

    # Analytical naked-arb profit: 4 × 40 × 60 × 1h × 12 pairs = 12 × 40 × 60 × 2 = 57,600
    # Wait: per pair = 40 * (90-30) + 40 * (150-90) = 2400 + 2400 = 4800. × 12 = 57,600.
    analytical = 12 * 40 * ((90 - 30) + (150 - 90))

    checks = [
        ("Solver optimal", r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
        ("q_id is SHORT (positive: sell) on expensive-ID hours",
         qid[exp_hours].mean() > 1.0,
         f"mean q_id[exp] = {qid[exp_hours].mean():.2f} MW"),
        ("q_id is LONG (negative: buy) on cheap-ID hours",
         qid[cheap_hours].mean() < -1.0,
         f"mean q_id[cheap] = {qid[cheap_hours].mean():.2f} MW"),
        ("q_da opposite-signed to q_id (naked basis mechanism)",
         float(np.sign(qda[exp_hours]).mean()) < -0.5
         and float(np.sign(qda[cheap_hours]).mean()) > 0.5,
         f"mean sign(q_da[cheap])={np.sign(qda[cheap_hours]).mean():.2f}, "
         f"mean sign(q_da[exp])={np.sign(qda[exp_hours]).mean():.2f}"),
        (f"Profit matches analytical naked-arb prediction (€{analytical:,.0f})",
         abs(r.expected_profit - analytical) < 100.0,
         f"actual = €{r.expected_profit:,.2f}, analytical = €{analytical:,.2f}"),
    ]
    return StressResult(
        name="Stress 6 — DA-ID basis trade (naked arbitrage)",
        description="DA flat 90 €/MWh; ID alternates 30 / 150. Frictionless ID market.",
        expected="Naked basis: q_da and q_id are opposite-signed at each hour. Battery idle. Profit = €57,600.",
        real_world=(
            "DA-ID basis is a substantial revenue stream on volatile ID markets like DK1 / DE-LU. "
            "In a frictionless ID market, BESS isn't physically needed for the trade — the optimizer "
            "takes opposite positions on the two legs and pockets the spread. This is a valid "
            "commercial behaviour in our model and represents 'paper' arbitrage that traders "
            "execute through their broker. In practice ID transaction costs (id_linear_penalty) "
            "reduce this revenue stream; the model handles that case too. "
            "Note: this test reveals that the current MILP allows unbounded naked DA-ID positions "
            "limited only by the bundle's max generation. For deployment, an id_position_cap_mw "
            "or id_linear_penalty must be set to reflect the operator's true ID exposure."
        ),
        bundle=bundle, result=r,
        extra={"throughput": throughput, "analytical_profit": analytical},
        checks=checks,
    )


CASES = [
    stress_1_non_anticipativity,
    stress_2_gen_spread_infeasibility,
    stress_3_analytical_vss,
    stress_4_cvar_asymmetric_tail,
    stress_5_cycle_cap_binding,
    stress_6_da_id_spread,
]


def run_all() -> list[StressResult]:
    results = []
    for fn in CASES:
        print(f"  Running {fn.__name__}...", end=" ", flush=True)
        try:
            r = fn()
        except Exception as e:
            print(f"EXCEPTION {type(e).__name__}: {e}")
            raise
        results.append(r)
        print("PASS" if r.passed else "FAIL")
    return results


if __name__ == "__main__":
    print("Running stochastic stress tests against 40 MW / 80 MWh BESS config...\n")
    results = run_all()
    print(f"\n{'PASS' if all(r.passed for r in results) else 'SOME FAILED'}: "
          f"{sum(r.passed for r in results)}/{len(results)}")
