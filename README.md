# storopt — Stochastic Dispatch Optimizer for Renewable + Battery Storage

A modular **two-stage stochastic MILP** for jointly bidding renewable generation and battery storage on EPEX-style day-ahead / intraday markets. The optimizer produces a non-anticipative day-ahead position and scenario-dependent intraday adjustments that maximize expected daily profit subject to full battery physical constraints (SOC, power limits, degradation, cycle cap).

> **Note:** This `main` branch is the clean modular rewrite. For the version with executed backtests and PDF reports, see the [**`advisor-report`**](../../tree/advisor-report) branch.

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

## What's novel here

- **Strict KNN leakage guard.** The target-day actual DA / intraday / generation / weather observations *never* enter the query vector. Only NWP forecast weather (issued before market close) is allowed. Feature standardization uses history-only statistics — the target day is held out of the fit. This makes VSS and EVPI metrics defensible.
- **Stochastic-vs-deterministic head-to-head built in.** `deterministic.py` collapses the scenario bundle to its mean and delegates to the same MILP, so VSS = profit(stochastic) − profit(deterministic-then-realize) is a one-line evaluation, not a re-implementation.
- **Adapter-based ingestion, no synthetic data.** Energinet, ENTSO-E (REST + FMS bulk fallback), and Open-Meteo each implement a thin `DataAdapter` ABC. `build_panel()` uses inner joins only — no forward-fill, no gap fabrication. If a source has a hole, the panel shrinks.

---

## Package layout

```
src/storopt/
  run.py                  PUBLIC API: run_day(), run_backtest()
  cli.py                  CLI: storopt run-day, storopt run-backtest

  config/
    schema.py             Pydantic root: RunConfig + IngestionConfig, BESSConfig,
                          MarketConfig, ScenarioConfig, OptimizerConfig, SolverConfig
    loader.py             load_config(path, **overrides) → RunConfig

  ingestion/
    base.py               DataAdapter ABC: fetch(start, end) → DataFrame
    panel.py              build_panel(start, end, config) → inner-joined hourly UTC
    cache.py              Parquet cache keyed by adapter + date range
    adapters/
      energinet.py        DK1 DA + ID prices (public EDS API)
      entsoe.py           ENTSO-E plant generation (REST + FMS bulk fallback)
      open_meteo.py       NWP weather at plant coordinates

  scenarios/
    base.py               ScenarioGenerator ABC
    knn.py                KNN nearest-neighbour (historical analog day, leakage-guarded)
    naive.py              Mean-of-history single deterministic scenario
    registry.py           "knn" / "naive" → class

  optimizer/
    base.py               Optimizer ABC
    milp.py               Two-stage stochastic MILP (Pyomo)
    deterministic.py      Collapses scenarios to mean; delegates to milp.py
    registry.py           "milp" / "deterministic" → class

  evaluation/
    engine.py             Rolling-window backtest loop
    metrics.py            VSS, EVPI, CRPS, throughput

configs/
  default.yaml            Complete working config (DK1, HR1, KNN, HiGHS solver)
  horns_rev1.yaml         HR1 EIC + coordinates override
```

---

## Running it

**Single day (Python):**
```python
from storopt import run_day
from storopt.config.loader import load_config
from datetime import date

config = load_config("configs/horns_rev1.yaml")
result = run_day(date(2025, 1, 15), config)

# Swap scenario method without editing the config:
result = run_day(date(2025, 1, 15), config, scenario_method="naive")
```

**Single day (CLI):**
```bash
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario naive
```

**Rolling backtest:**
```bash
storopt run-backtest \
  --start 2025-01-01 --end 2025-03-31 \
  --config configs/horns_rev1.yaml \
  --output-dir ./results/q1_2025
```

---

## Extending

Adding a new scenario method (e.g. SARIMA):
1. Create `storopt/scenarios/sarima.py` implementing the `ScenarioGenerator` ABC (`fit(history_df)`, `generate(date, n) → ScenarioBundle`).
2. Register in `scenarios/registry.py`: `"sarima": SARIMAScenarioGenerator`.
3. Use via `--scenario sarima`. Nothing else changes.

Adding a new optimizer or data source follows the same ABC + registry pattern.

---

## Environment

| Variable | Required | Description |
|---|---|---|
| `ENTSOE_SECURITY_TOKEN` | Yes | ENTSO-E Transparency Platform REST API token ([register](https://transparency.entsoe.eu/)) |
| `ENTSOE_FMS_USER` | No | FMS bulk-download user (fallback for long ranges) |
| `ENTSOE_FMS_PASS` | No | FMS bulk-download password |

Copy `.env.example` → `.env` and fill in.

Solver: HiGHS (open-source) by default — no commercial license needed. Pyomo can also dispatch to Gurobi/CPLEX if available.

---

## Documentation

- [`PIPELINE.md`](PIPELINE.md) — data-flow diagram and module responsibilities
- [`DOCUMENTATION.md`](DOCUMENTATION.md) — long-form design notes and method derivations
