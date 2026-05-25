"""
Evaluation metrics: VSS, EVPI, throughput, CRPS, drawdown.

VSS  = z_RP - z_EEV   (value of using stochastic model over deterministic)
EVPI = z_WS - z_RP    (value of having perfect foresight over stochastic)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from storopt.config.schema import RunConfig
from storopt.optimizer.milp import StochasticMILP, _build, _solve, _extract
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle


def _mean_bundle(bundle: ScenarioBundle) -> ScenarioBundle:
    w = bundle.probabilities
    return ScenarioBundle(
        da_prices=np.average(bundle.da_prices, axis=0, weights=w)[np.newaxis, :],
        id_prices=np.average(bundle.id_prices, axis=0, weights=w)[np.newaxis, :],
        res_generation=np.average(bundle.res_generation, axis=0, weights=w)[np.newaxis, :],
        probabilities=np.array([1.0]),
        target_date=bundle.target_date,
    )


def _single_bundles(bundle: ScenarioBundle) -> list[ScenarioBundle]:
    return [
        ScenarioBundle(
            da_prices=bundle.da_prices[i : i + 1],
            id_prices=bundle.id_prices[i : i + 1],
            res_generation=bundle.res_generation[i : i + 1],
            probabilities=np.array([1.0]),
            target_date=bundle.target_date,
        )
        for i in range(bundle.n_scenarios)
    ]


def _solve_fixed_da(bundle: ScenarioBundle, config: RunConfig, da_bids: np.ndarray) -> OptimizationResult:
    """Solve with first-stage DA bids fixed (for EEV computation).

    Relaxes q_da bounds before fixing so that EV bids computed on the mean bundle
    (which may have tighter generation-derived bounds) do not cause infeasibility
    when evaluated against the full multi-scenario bundle.
    """
    import time
    model = _build(bundle, config)
    for t in model.T:
        model.q_da[t].setlb(None)
        model.q_da[t].setub(None)
        model.q_da[t].fix(float(da_bids[t - 1]))
    status, elapsed = _solve(model, config)
    result = _extract(model, bundle, config)
    result.solve_status = status
    result.solve_time_s = elapsed
    return result


def compute_vss_evpi(
    bundle: ScenarioBundle,
    config: RunConfig,
    *,
    stochastic_result: OptimizationResult | None = None,
    compute_evpi: bool = True,
) -> dict[str, Any]:
    """
    Compute VSS and (optionally) EVPI for a scenario bundle.

    Parameters
    ----------
    bundle:
        The S-scenario bundle used by the stochastic model.
    config:
        Run configuration (solver, BESS, market settings).
    stochastic_result:
        Pre-computed RP result to avoid re-solving. If None, solves RP fresh.
    compute_evpi:
        If False, skips the expensive wait-and-see solves (one per scenario).

    Returns
    -------
    dict with z_rp, z_eev, z_ws (or nan), vss_eur, evpi_eur (or nan).
    """
    solver = StochasticMILP()

    # Recourse Problem (stochastic)
    z_rp_result = stochastic_result or solver.solve(bundle, config)
    z_rp = z_rp_result.expected_profit

    # EV problem: solve on mean scenario, get DA plan, evaluate against all scenarios
    mean_b = _mean_bundle(bundle)
    ev_result = solver.solve(mean_b, config)
    try:
        eev_result = _solve_fixed_da(bundle, config, ev_result.da_bids)
        z_eev = eev_result.expected_profit
    except RuntimeError:
        z_eev = float("nan")

    # Wait-and-see (one solve per scenario)
    if compute_evpi:
        ws_profits = [solver.solve(sb, config).expected_profit for sb in _single_bundles(bundle)]
        z_ws = float(np.dot(bundle.probabilities, ws_profits))
    else:
        z_ws = float("nan")

    vss = (z_rp - z_eev) if not (z_eev != z_eev) else float("nan")  # nan-safe
    return {
        "z_rp": z_rp,
        "z_eev": z_eev,
        "z_ws": z_ws,
        "vss_eur": vss,
        "evpi_eur": (z_ws - z_rp) if compute_evpi else float("nan"),
    }


def compute_throughput_mwh(result: OptimizationResult, dt_hours: float) -> float:
    return float((result.charge + result.discharge).sum() * dt_hours / result.n_scenarios)


def compute_crps(
    forecasted_quantiles: np.ndarray,
    actual: float,
    quantile_levels: np.ndarray,
) -> float:
    """
    Continuous Ranked Probability Score (empirical quantile form).

    Parameters
    ----------
    forecasted_quantiles:
        Array of forecast quantiles at quantile_levels.
    actual:
        Realized value.
    quantile_levels:
        Quantile levels in [0, 1].
    """
    n = len(quantile_levels)
    score = 0.0
    for i, (q, alpha) in enumerate(zip(forecasted_quantiles, quantile_levels)):
        indicator = float(actual < q)
        score += (q - actual) * (alpha - indicator)
    return 2.0 * score / n


def compute_max_drawdown(profits: pd.Series) -> float:
    if profits.empty:
        return float("nan")
    cumulative = profits.cumsum()
    running_peak = cumulative.cummax()
    return float((cumulative - running_peak).min())


def summarize_backtest(daily_summary: pd.DataFrame) -> dict[str, float]:
    if daily_summary.empty:
        return {}
    ep = pd.to_numeric(daily_summary["expected_profit_eur"], errors="coerce")
    tp = pd.to_numeric(daily_summary.get("throughput_mwh", pd.Series()), errors="coerce")
    vss = pd.to_numeric(daily_summary.get("vss_eur", pd.Series()), errors="coerce")
    return {
        "n_days": len(daily_summary),
        "total_expected_profit_eur": float(ep.sum()),
        "avg_expected_profit_eur": float(ep.mean()),
        "total_throughput_mwh": float(tp.sum()) if not tp.empty else float("nan"),
        "avg_vss_eur": float(vss.mean()) if not vss.empty else float("nan"),
        "max_drawdown_eur": compute_max_drawdown(ep.fillna(0.0)),
    }
