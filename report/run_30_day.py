"""
30-day rolling backtest driver for the storopt advisor report.

Runs naive, KNN, and SARIMAX scenario generation + two-stage stochastic MILP
for every target date in [START, END], computing real VSS and EVPI per
method per day. Designed for parallel execution on 4 CPUs with resume-safe
per-day artifact caching.

Outputs:
  report/artifacts/<YYYY-MM-DD>/<method>_{bundle,result,metrics}.pkl
  report/artifacts/daily_summary.parquet
  report/artifacts/run.log
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import sys
import time
import traceback
from datetime import date, timedelta
from multiprocessing import Pool, get_context
from pathlib import Path

# Pin BLAS threads BEFORE numpy/scipy import so each worker stays on one core.
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[var] = "1"

import numpy as np
import pandas as pd

from storopt.config.loader import load_config
from storopt.evaluation.metrics import compute_throughput_mwh, compute_vss_evpi
from storopt.ingestion.panel import build_panel
from storopt.optimizer.milp import StochasticMILP
from storopt.scenarios.registry import get_generator

# ── Configuration ────────────────────────────────────────────────────────────
START      = date(2025, 1, 15)
END        = date(2025, 2, 13)        # 30 days inclusive
HIST_DAYS  = 100
METHODS    = ("naive", "knn", "sarimax")
N_SCEN     = {"naive": 1, "knn": 5, "sarimax": 5}
CFG_PATH   = "configs/horns_rev1_40mw.yaml"
# Use all available cores by default. Override with STOROPT_WORKERS env var if set.
N_WORKERS  = int(os.environ.get("STOROPT_WORKERS", os.cpu_count() or 4))

REPO       = Path(__file__).resolve().parents[1]
ARTIFACT   = REPO / "report" / "artifacts"
PANEL_PKL  = ARTIFACT / "_panel.pkl"
SUMMARY_PARQUET = ARTIFACT / "daily_summary.parquet"
LOG_PATH   = ARTIFACT / "run.log"
ARTIFACT.mkdir(parents=True, exist_ok=True)


# ── Worker ────────────────────────────────────────────────────────────────────

def _day_dir(target_date: date) -> Path:
    p = ARTIFACT / target_date.isoformat()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _is_done(target_date: date) -> bool:
    d = _day_dir(target_date)
    return all((d / f"{m}_metrics.json").exists() for m in METHODS)


def _run_method(method: str, panel: pd.DataFrame, target_date: date, cfg) -> dict:
    n_scen = N_SCEN[method]
    bundle_path = _day_dir(target_date) / f"{method}_bundle.pkl"

    # Reuse cached bundle if available (saves ~30-60 min for SARIMAX).
    # The result is always re-solved because BESS sizing may differ from cache time.
    if bundle_path.exists():
        with open(bundle_path, "rb") as f:
            bundle = pickle.load(f)
        # Sanity-check the cached bundle matches our target_date and n_scen
        if bundle.target_date == target_date and bundle.n_scenarios == n_scen:
            t_fit = 0.0
            t_gen = 0.0
        else:
            bundle = None
    else:
        bundle = None

    if bundle is None:
        t0 = time.perf_counter()
        gen = get_generator(method)
        gen.fit(panel)
        t_fit = time.perf_counter() - t0

        t1 = time.perf_counter()
        bundle = gen.generate(target_date, n_scen)
        t_gen = time.perf_counter() - t1

    t2 = time.perf_counter()
    result = StochasticMILP().solve(bundle, cfg)
    t_solve = time.perf_counter() - t2

    t3 = time.perf_counter()
    ve = compute_vss_evpi(bundle, cfg, stochastic_result=result, compute_evpi=True)
    t_vss = time.perf_counter() - t3

    # Per-scenario realisations vs. realised reference (naive only has 1; KNN/SARIMAX have S)
    metrics = {
        "method": method,
        "target_date": target_date.isoformat(),
        "n_scenarios": n_scen,
        "expected_profit_eur": float(result.expected_profit),
        "scenario_profits_eur": [float(x) for x in result.scenario_profits],
        "probabilities": [float(p) for p in bundle.probabilities],
        "throughput_mwh": float(compute_throughput_mwh(result, cfg.market.dt_hours)),
        "solve_status": str(result.solve_status),
        "z_rp_eur": float(ve["z_rp"]),
        "z_eev_eur": float(ve["z_eev"]) if not (np.isnan(ve["z_eev"]) if isinstance(ve["z_eev"], float) else False) else None,
        "z_ws_eur": float(ve["z_ws"]) if not (np.isnan(ve["z_ws"]) if isinstance(ve["z_ws"], float) else False) else None,
        "vss_eur": float(ve["vss_eur"]) if not (np.isnan(ve["vss_eur"]) if isinstance(ve["vss_eur"], float) else False) else None,
        "evpi_eur": float(ve["evpi_eur"]) if not (np.isnan(ve["evpi_eur"]) if isinstance(ve["evpi_eur"], float) else False) else None,
        "t_fit_s": t_fit, "t_gen_s": t_gen, "t_solve_s": t_solve, "t_vss_s": t_vss,
    }
    return bundle, result, metrics


def _worker(args) -> dict:
    target_date_iso, panel_pkl_path = args
    target_date = date.fromisoformat(target_date_iso)
    panel = pd.read_pickle(panel_pkl_path)

    cfg = load_config(CFG_PATH, **{
        "ingestion.history_days": HIST_DAYS,
        "ingestion.cache_dir": str(REPO / "data" / "cache"),
        "solver.verbose": False,
    })

    history_start = target_date - timedelta(days=HIST_DAYS)
    panel_window = panel[
        (panel["delivery_ts_utc"].dt.date >= history_start)
        & (panel["delivery_ts_utc"].dt.date <= target_date)
    ].copy()

    summary = {"target_date": target_date_iso}
    d = _day_dir(target_date)

    for method in METHODS:
        metrics_path = d / f"{method}_metrics.json"
        error_path   = d / f"{method}_error.txt"
        if metrics_path.exists():
            summary[method] = "cached"
            continue
        if error_path.exists():
            # Treat prior errors as terminal: delete the error file by hand to retry.
            summary[method] = "skipped (prior error)"
            continue

        try:
            t0 = time.perf_counter()
            bundle, result, metrics = _run_method(method, panel_window, target_date, cfg)
            with open(d / f"{method}_bundle.pkl", "wb") as f:
                pickle.dump(bundle, f)
            with open(d / f"{method}_result.pkl", "wb") as f:
                pickle.dump(result, f)
            with open(metrics_path, "w") as f:
                json.dump(metrics, f, indent=2)
            summary[method] = f"OK {time.perf_counter() - t0:.0f}s"
        except Exception as e:
            err = f"ERR {type(e).__name__}: {e}"
            summary[method] = err
            with open(d / f"{method}_error.txt", "w") as f:
                f.write(traceback.format_exc())

    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(processName)s] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, mode="a"), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("driver")
    log.info(f"=== run_30_day starting {START} → {END} ({N_WORKERS} workers, methods={METHODS}) ===")

    # Build full panel once (shared across all workers via pickle)
    cfg = load_config(CFG_PATH, **{
        "ingestion.history_days": HIST_DAYS,
        "ingestion.cache_dir": str(REPO / "data" / "cache"),
    })
    if not PANEL_PKL.exists():
        log.info("Building full panel...")
        fetch_start = START - timedelta(days=HIST_DAYS)
        panel = build_panel(fetch_start, END, cfg.ingestion)
        panel.to_pickle(PANEL_PKL)
        log.info(f"Panel: {len(panel)} rows × {len(panel.columns)} cols")
    else:
        log.info(f"Panel pickle already exists at {PANEL_PKL}")

    target_dates = []
    d = START
    while d <= END:
        target_dates.append(d.isoformat())
        d += timedelta(days=1)

    pending = [t for t in target_dates if not _is_done(date.fromisoformat(t))]
    log.info(f"{len(target_dates)} target dates, {len(pending)} pending, {len(target_dates) - len(pending)} cached")

    if pending:
        ctx = get_context("spawn")
        t_start = time.perf_counter()
        with ctx.Pool(processes=N_WORKERS) as pool:
            for i, summary in enumerate(pool.imap_unordered(
                _worker, [(t, str(PANEL_PKL)) for t in pending]
            )):
                log.info(f"[{i + 1}/{len(pending)}] {summary['target_date']} | "
                         f"naive={summary.get('naive')} | knn={summary.get('knn')} | sarimax={summary.get('sarimax')}")
        log.info(f"All days finished in {(time.perf_counter() - t_start) / 60:.1f} min")

    # Aggregate metrics across days
    rows = []
    for td_iso in target_dates:
        d_path = ARTIFACT / td_iso
        for method in METHODS:
            mp = d_path / f"{method}_metrics.json"
            if mp.exists():
                rows.append(json.load(open(mp)))
    df = pd.DataFrame(rows)
    df.to_parquet(SUMMARY_PARQUET, index=False)
    df.to_csv(SUMMARY_PARQUET.with_suffix(".csv"), index=False)
    log.info(f"Wrote {SUMMARY_PARQUET}  ({len(df)} rows)")
    log.info("=== run_30_day finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
