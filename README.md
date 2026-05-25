# storopt — Stochastic Dispatch Optimizer for Renewable + Battery Storage

> **Branch: `advisor-report`** — this branch contains the full optimizer **plus** executed backtests, generated PDF reports, and the multi-day rolling evaluation pipeline used to produce results for the advisor review. For the clean library-only version, see [**`main`**](../../tree/main).

A modular **two-stage stochastic MILP** for jointly bidding renewable generation and battery storage on EPEX-style day-ahead / intraday markets. The optimizer produces a non-anticipative day-ahead position and scenario-dependent intraday adjustments that maximize expected daily profit subject to full battery physical constraints (SOC, power limits, degradation, cycle cap).

---

## The optimization problem

Two-stage stochastic program for a single trading day, 24 hourly periods:

| Stage | Decision | When | Anticipative? |
|---|---|---|---|
| 1 | Day-ahead net position `q_da[t]` | Before scenarios resolve | No |
| 2 | Intraday trade `q_id[s,t]`, charge `p_ch[s,t]`, discharge `p_dis[s,t]`, SOC `soc[s,t]` | After scenario realizes | Yes (per scenario) |

**Objective:** Maximize *E*[profit] − degradation cost, optionally with a CVaR risk penalty.

**Scenarios:** Either KNN historical-analog days (the substantive method) or a single deterministic mean scenario (the naive baseline used to compute the Value of Stochastic Solution).

**Primary case study:** DK1 (Denmark), Horns Rev 1 (HR1) offshore wind, Energinet day-ahead/intraday prices, ENTSO-E generation.

---

## What's in this branch (vs. `main`)

In addition to everything in `main`, this branch includes:

- **`report/_executed_multi_day_knn.ipynb` / `.pdf`** — an executed multi-day backtest notebook with all cells run, results plotted, and rendered to PDF. This is the artifact submitted for the advisor review.
- **`report/run_30_day.py` / `run_30_day_knn.py`** — driver scripts for the 30-day rolling backtests (deterministic and KNN).
- **`report/build_*.py`, `render_advisor_pdf.py`, `html_to_pdf.py`, `patch_mathjax.py`** — the report-generation toolchain that turns raw backtest output into the final PDF (handles MathJax, page breaks, advisor-style formatting).
- **Extended test suite** (`tests/stochastic_stress.py`, `tests/build_stress_test_report.py`) — randomized stress tests that stretch the MILP across parameter regimes and emit a per-case results PDF.

If you only want to read or run the optimizer itself, the `main` branch is leaner. If you want to see the optimizer **applied** with quantitative results, this branch is the one.

---

## What's novel here

- **Strict KNN leakage guard.** Target-day actual DA / intraday / generation / weather observations *never* enter the query vector. Only NWP forecast weather (issued before market close) is allowed. Feature standardization uses history-only statistics — the target day is held out of the fit. This makes VSS and EVPI metrics defensible.
- **Stochastic-vs-deterministic head-to-head built in.** `deterministic.py` collapses the scenario bundle to its mean and delegates to the same MILP, so VSS = profit(stochastic) − profit(deterministic-then-realize) is a one-line evaluation, not a re-implementation.
- **Adapter-based ingestion, no synthetic data.** Energinet, ENTSO-E (REST + FMS bulk fallback), and Open-Meteo each implement a thin `DataAdapter` ABC. `build_panel()` uses inner joins only — no forward-fill, no gap fabrication.

---

## Package layout

```
src/storopt/
  run.py                  PUBLIC API: run_day(), run_backtest()
  cli.py                  CLI: storopt run-day, storopt run-backtest

  config/                 Pydantic config schema + loader
  ingestion/              DataAdapter ABC, panel builder, cache
    adapters/             energinet, entsoe, open_meteo
  scenarios/              ScenarioGenerator ABC: knn, naive, registry
  optimizer/              Optimizer ABC: milp (Pyomo), deterministic, registry
  evaluation/             Backtest engine + metrics (VSS, EVPI, CRPS)

configs/
  default.yaml            Complete working config (DK1, HR1, KNN, HiGHS solver)
  horns_rev1.yaml         HR1 EIC + coordinates override

report/                   << additions on this branch >>
  _executed_multi_day_knn.ipynb       Executed multi-day backtest notebook
  _executed_multi_day_knn.pdf         Rendered advisor-review PDF
  run_30_day.py / run_30_day_knn.py   30-day rolling drivers
  build_*.py, render_advisor_pdf.py   Report-generation toolchain

tests/                                Extended stress + integration tests
```

---

## Running it

**Single day:**
```bash
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario naive
```

**Reproduce the 30-day backtest (this branch):**
```bash
python report/run_30_day_knn.py     # KNN stochastic
python report/run_30_day.py         # deterministic baseline
```

**Regenerate the advisor PDF:**
```bash
python report/render_advisor_pdf.py
```

---

## Environment

| Variable | Required | Description |
|---|---|---|
| `ENTSOE_SECURITY_TOKEN` | Yes | ENTSO-E Transparency Platform REST API token ([register](https://transparency.entsoe.eu/)) |
| `ENTSOE_FMS_USER` | No | FMS bulk-download user (fallback for long ranges) |
| `ENTSOE_FMS_PASS` | No | FMS bulk-download password |

Copy `.env.example` → `.env` and fill in. Solver: HiGHS by default; Gurobi/CPLEX if available.

---

## Documentation

- [`PIPELINE.md`](PIPELINE.md) — data flow and module responsibilities
- [`DOCUMENTATION.md`](DOCUMENTATION.md) — long-form design notes and method derivations
- [`report/_executed_multi_day_knn.pdf`](report/_executed_multi_day_knn.pdf) — executed advisor-review report
