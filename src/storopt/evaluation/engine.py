"""
Rolling-window backtest engine.

For each target_date in [start, end]:
  1. Build panel covering [target_date - history_days, target_date]
  2. Fit scenario generator on history (dates < target_date)
  3. Generate scenarios for target_date
  4. Solve MILP
  5. Optionally compute VSS/EVPI
  6. Append daily summary row

Output: daily_summary DataFrame (one row per day).
"""

from __future__ import annotations

import traceback
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from storopt.config.schema import RunConfig
from storopt.evaluation.metrics import compute_throughput_mwh, compute_vss_evpi
from storopt.ingestion.panel import build_panel
from storopt.optimizer.registry import get_optimizer
from storopt.scenarios.registry import get_generator


def rolling_backtest(
    start: date,
    end: date,
    config: RunConfig,
    *,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """
    Run a rolling daily backtest and return the daily summary.

    Parameters
    ----------
    start, end:
        Inclusive date range.
    config:
        Full RunConfig.
    output_dir:
        If given, writes daily_summary.parquet to this directory on completion.

    Returns
    -------
    pd.DataFrame with columns:
        target_date, scenario_method, n_scenarios, expected_profit_eur,
        throughput_mwh, solve_status, solve_time_s, vss_eur, evpi_eur, error
    """
    rows: list[dict] = []
    target_date = start

    while target_date <= end:
        row = _run_one_day(target_date, config)
        rows.append(row)
        target_date += timedelta(days=1)

    summary = pd.DataFrame(rows)

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        summary.to_parquet(out / "daily_summary.parquet", index=False)
        summary.to_csv(out / "daily_summary.csv", index=False)

    return summary


def _run_one_day(target_date: date, config: RunConfig) -> dict:
    row: dict = {
        "target_date": target_date.isoformat(),
        "scenario_method": config.scenarios.method,
        "n_scenarios": config.scenarios.n_scenarios,
        "expected_profit_eur": float("nan"),
        "throughput_mwh": float("nan"),
        "solve_status": "not_run",
        "solve_time_s": float("nan"),
        "vss_eur": float("nan"),
        "evpi_eur": float("nan"),
        "error": None,
    }
    try:
        history_start = target_date - timedelta(days=config.ingestion.history_days)
        panel = build_panel(history_start, target_date, config.ingestion)

        generator = get_generator(config.scenarios.method, **config.scenarios.params)
        # Full panel passed so KNN can read target-day NWP weather for its query vector.
        # Leakage guards inside the generator exclude target_date from the candidate pool.
        generator.fit(panel)
        bundle = generator.generate(target_date, config.scenarios.n_scenarios)

        optimizer = get_optimizer(config.optimizer.method)
        result = optimizer.solve(bundle, config)

        row["expected_profit_eur"] = result.expected_profit
        row["throughput_mwh"] = compute_throughput_mwh(result, config.market.dt_hours)
        row["solve_status"] = result.solve_status
        row["solve_time_s"] = result.solve_time_s

        if config.backtest.vss_enabled or config.backtest.evpi_enabled:
            vss_evpi = compute_vss_evpi(
                bundle,
                config,
                stochastic_result=result,
                compute_evpi=config.backtest.evpi_enabled,
            )
            row["vss_eur"] = vss_evpi["vss_eur"]
            row["evpi_eur"] = vss_evpi["evpi_eur"]

    except Exception:
        row["error"] = traceback.format_exc()
        row["solve_status"] = "error"

    return row
