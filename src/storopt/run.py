"""
Public API for storopt.

    from storopt import run_day, run_backtest
    from storopt.config.loader import load_config
    from datetime import date

    config = load_config("configs/horns_rev1.yaml")
    result = run_day(date(2025, 1, 15), config)
    result = run_day(date(2025, 1, 15), config, scenario_method="naive")
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from storopt.config.loader import load_config
from storopt.config.schema import RunConfig
from storopt.ingestion.panel import build_panel
from storopt.optimizer.registry import get_optimizer
from storopt.scenarios.registry import get_generator

if TYPE_CHECKING:
    from storopt.optimizer.types import OptimizationResult


def run_day(
    target_date: date,
    config: RunConfig | str | Path,
    *,
    scenario_method: str | None = None,
    n_scenarios: int | None = None,
    solver: str | None = None,
) -> "OptimizationResult":
    """
    Fetch history, generate scenarios, and solve the dispatch MILP for one day.

    Parameters
    ----------
    target_date:
        The trading day to optimize. History up to (not including) this date
        is used to fit the scenario generator.
    config:
        A RunConfig object, or a path to a YAML file that will be loaded with
        load_config(). YAML files are merged onto configs/default.yaml.
    scenario_method:
        Override config.scenarios.method ("knn" or "naive").
    n_scenarios:
        Override config.scenarios.n_scenarios.
    solver:
        Override config.solver.name ("highs" or "gurobi").

    Returns
    -------
    OptimizationResult with dispatch, SOC, per-scenario profits, solve metadata.
    """
    cfg = _resolve_config(config, scenario_method=scenario_method, n_scenarios=n_scenarios, solver=solver)

    history_start = target_date - timedelta(days=cfg.ingestion.history_days)

    # Fetch [history_start, target_date] inclusive — target_date weather is needed for KNN query
    panel = build_panel(history_start, target_date, cfg.ingestion)

    if len(panel[panel["delivery_ts_utc"].dt.date < target_date]) == 0:
        raise ValueError(
            f"No history rows found before {target_date}. "
            f"Fetched from {history_start}; check data availability."
        )

    generator = get_generator(cfg.scenarios.method, **cfg.scenarios.params)
    # Pass the full panel (including target_date rows) so KNN can read target-day NWP weather
    # for its query vector. Leakage guards inside the generator ensure target-day actual
    # DA/ID/generation are never used as features or candidates.
    generator.fit(panel)

    bundle = generator.generate(target_date, cfg.scenarios.n_scenarios)

    optimizer = get_optimizer(cfg.optimizer.method)
    result = optimizer.solve(bundle, cfg)
    return result


def run_backtest(
    start: date,
    end: date,
    config: RunConfig | str | Path,
    *,
    scenario_method: str | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """
    Rolling backtest from start to end (inclusive).

    For each day in [start, end], calls run_day() with history up to (not incl.)
    that day. Aggregates daily results into a summary DataFrame.

    Parameters
    ----------
    start, end:
        Inclusive date range for the backtest.
    config:
        RunConfig or path to YAML.
    scenario_method:
        Override scenario method for all days.
    output_dir:
        If given, writes daily_summary.parquet, dispatch.parquet, and
        scenario_summary.parquet to this directory.

    Returns
    -------
    pd.DataFrame with one row per target_date and columns for profit,
    solve_status, solve_time, VSS/EVPI (if enabled), forecast method.
    """
    from storopt.evaluation.engine import rolling_backtest

    cfg = _resolve_config(config, scenario_method=scenario_method)
    return rolling_backtest(start, end, cfg, output_dir=output_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_config(
    config: RunConfig | str | Path,
    *,
    scenario_method: str | None = None,
    n_scenarios: int | None = None,
    solver: str | None = None,
) -> RunConfig:
    if isinstance(config, RunConfig):
        cfg = config
    else:
        cfg = load_config(config)

    # Apply keyword overrides by building a fresh config via dict manipulation
    if scenario_method is not None or n_scenarios is not None or solver is not None:
        raw = cfg.model_dump()
        if scenario_method is not None:
            raw["scenarios"]["method"] = scenario_method
        if n_scenarios is not None:
            raw["scenarios"]["n_scenarios"] = n_scenarios
        if solver is not None:
            raw["solver"]["name"] = solver
        cfg = RunConfig.model_validate(raw)

    return cfg
