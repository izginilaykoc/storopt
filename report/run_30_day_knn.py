"""
Local KNN-only 30-day rolling driver.

Thin wrapper over run_30_day.py: forces METHODS = ('knn',) and writes to
report/artifacts_knn/ so the local KNN run does not collide with the remote
3-method run's artifacts.

Runtime expectation: < 10 minutes on a 4-core laptop (KNN is fast — no SARIMAX
fitting). All other settings (40 MW BESS, 100-day history window, real VSS/EVPI
with the EEV q_id bound fix, target dates 2025-01-15..2025-02-13) match the
full advisor pipeline.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Override module-level constants BEFORE importing run_30_day
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("STOROPT_WORKERS", "4")

# Import and reconfigure
sys.path.insert(0, str(Path(__file__).parent))
import run_30_day  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
run_30_day.METHODS = ("knn",)
run_30_day.N_SCEN = {"knn": 5}
run_30_day.ARTIFACT = REPO / "report" / "artifacts_knn"
run_30_day.PANEL_PKL = run_30_day.ARTIFACT / "_panel.pkl"
run_30_day.SUMMARY_PARQUET = run_30_day.ARTIFACT / "daily_summary.parquet"
run_30_day.LOG_PATH = run_30_day.ARTIFACT / "run.log"
run_30_day.ARTIFACT.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    sys.exit(run_30_day.main())
