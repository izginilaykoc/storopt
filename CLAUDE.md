# storopt — Storage Dispatch Optimizer

## What this project is

A modular stochastic dispatch optimizer for renewable + battery storage assets trading on EPEX Spot.
It produces day-ahead bid positions and intraday adjustments that maximize expected daily profit
subject to battery physical constraints (SOC, power limits, degradation, cycle cap).

This is a clean rewrite of `BESS-Optimization-develop_fevzi` with full modularity at every layer.
The intellectual content (MILP formulation, KNN scenario generation, VSS/EVPI metrics) is preserved;
the structure is replaced.

Old repo location (do not modify): `../BESS-Optimization-develop_fevzi/`

---

## Core problem

Two-stage stochastic MILP for one trading day:

- **Stage 1** (non-anticipative): day-ahead net position `q_da[t]`
- **Stage 2** (scenario-dependent): intraday trade `q_id[s,t]`, charge `p_ch[s,t]`, discharge `p_dis[s,t]`, SOC `soc[s,t]`
- **Objective**: max expected profit minus degradation cost, optionally minus CVaR penalty
- **Scenarios**: historical analog-day (KNN) or deterministic mean (naive)

Primary market: DK1 (Denmark), Energinet DA/ID prices, 24 hourly periods.
Primary plant: Horns Rev 1 (HR1) offshore wind, ENTSO-E generation data.

---

## Package layout

```
src/storopt/
  run.py              PUBLIC API: run_day(), run_backtest()
  cli.py              CLI: storopt run-day, storopt run-backtest

  config/
    schema.py         RunConfig (Pydantic root): IngestionConfig, BESSConfig,
                      MarketConfig, ScenarioConfig, OptimizerConfig, SolverConfig, BacktestConfig
    loader.py         load_config(path, **overrides) → RunConfig

  ingestion/
    base.py           DataAdapter ABC: fetch(start, end) → pd.DataFrame
    panel.py          build_panel(start, end, config) → pd.DataFrame (inner-joined, hourly UTC)
    cache.py          Parquet cache keyed by adapter + date range
    adapters/
      energinet.py    DK1 DA + ID prices  (public EDS API, no token required)
      entsoe.py       ENTSO-E plant generation (REST API; FMS bulk fallback)
      open_meteo.py   NWP weather at plant coordinates (historical forecast API)

  scenarios/
    base.py           ScenarioGenerator ABC: fit(history_df), generate(date, n) → ScenarioBundle
    types.py          ScenarioBundle dataclass
    registry.py       REGISTRY dict + get_generator(method, **params)
    knn.py            KNN nearest-neighbour (historical analog-day, leakage guards)
    naive.py          Naive: mean of history → single deterministic scenario

  optimizer/
    base.py           Optimizer ABC: solve(bundle, config) → OptimizationResult
    types.py          BESSConfig, MarketConfig, SolverConfig, OptimizationResult
    registry.py       REGISTRY dict + get_optimizer(method)
    milp.py           Two-stage stochastic MILP (Pyomo)
    deterministic.py  Collapses ScenarioBundle to mean scenario; delegates to milp.py

  evaluation/
    engine.py         run_backtest() rolling-window loop
    metrics.py        VSS, EVPI, CRPS, throughput

configs/
  default.yaml        Complete working config (DK1, HR1, KNN, HiGHS solver)
  horns_rev1.yaml     HR1 plant EIC + coordinates override
```

---

## How to run

### Single day — Python

```python
from storopt import run_day
from storopt.config.loader import load_config
from datetime import date

config = load_config("configs/horns_rev1.yaml")
result = run_day(date(2025, 1, 15), config)

# Swap scenario method without touching the config file:
result = run_day(date(2025, 1, 15), config, scenario_method="naive")
```

### Single day — CLI

```bash
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario naive
```

### Rolling backtest

```bash
storopt run-backtest \
  --start 2025-01-01 --end 2025-03-31 \
  --config configs/horns_rev1.yaml \
  --output-dir ./results/q1_2025
```

---

## Extending the system

### Add a new scenario method (e.g. SARIMA)

1. Create `storopt/scenarios/sarima.py`; implement `ScenarioGenerator` (`fit(history_df)`, `generate(date, n) → ScenarioBundle`).
2. Register in `storopt/scenarios/registry.py`: `"sarima": SARIMAScenarioGenerator`
3. Use via `--scenario sarima` or `config.scenarios.method = "sarima"` — nothing else changes.

### Add a new optimizer

Same pattern: implement `Optimizer` ABC (`solve(bundle, config) → OptimizationResult`), register in `optimizer/registry.py`.

### Add a new data source (e.g. Germany / SMARD)

1. Create `storopt/ingestion/adapters/smard.py`; implement `DataAdapter` (`fetch(start, end) → DataFrame`).
2. Update `ingestion/panel.py` to select it based on `config.ingestion.market`.

---

## Key invariants — do not break

- **KNN leakage guard**: target-day actual DA/ID/generation/weather observations must **never** appear
  in the query vector. Only NWP forecast (not realized) weather is allowed for the target day.
  Feature standardization uses history-only statistics (target day excluded from fit).
- **No synthetic data**: all prices, generation, and weather must come from real external sources.
- **Inner join only** in `build_panel()`: no forward-fill or gap fabrication.
- **Terminal SOC constraint**: SOC at end of day must return to initial SOC.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ENTSOE_SECURITY_TOKEN` | Yes | ENTSO-E Transparency Platform REST API token |
| `ENTSOE_FMS_USER` | No | FMS bulk download username (fallback for long date ranges) |
| `ENTSOE_FMS_PASS` | No | FMS bulk download password |

---

## Source reference (old repo at `../BESS-Optimization-develop_fevzi/`)

| New file | Ported from |
|---|---|
| `optimizer/milp.py` | `bess_opt/baseline_model.py` |
| `scenarios/knn.py` | `bess_opt/forecasting/nearest_neighbour.py` |
| `evaluation/metrics.py` | `bess_opt/backtest/metrics.py` |
| `ingestion/adapters/energinet.py` | `efias_ingestion/sources/energinet_aggregate.py` |
| `ingestion/adapters/entsoe.py` | `efias_ingestion/sources/entsoe_generation.py` + `entsoe_file_library.py` |
| `ingestion/adapters/open_meteo.py` | `bess_opt/data/open_meteo_historical_forecast.py` |
| `ingestion/panel.py` | `bess_opt/data/dk1_hr1_panel.py` |
| `evaluation/engine.py` | `bess_opt/backtest/engine.py` |
