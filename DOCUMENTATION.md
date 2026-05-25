# storopt — Technical Documentation

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Installation and Setup](#3-installation-and-setup)
4. [Configuration System](#4-configuration-system)
5. [Data Ingestion Layer](#5-data-ingestion-layer)
6. [Scenario Generation Layer](#6-scenario-generation-layer)
7. [Optimization Layer](#7-optimization-layer)
8. [Evaluation Layer](#8-evaluation-layer)
9. [Public API](#9-public-api)
10. [CLI Reference](#10-cli-reference)
11. [End-to-End Data Flow](#11-end-to-end-data-flow)
12. [Key Invariants](#12-key-invariants)
13. [Extending the System](#13-extending-the-system)
14. [Environment Variables](#14-environment-variables)

---

## 1. Project Overview

**storopt** is a modular stochastic dispatch optimizer for renewable + battery storage assets
trading on EPEX Spot. It answers the daily question: *given battery physical constraints and
uncertainty in future prices and renewable output, what day-ahead bid position maximizes
expected intraday profit?*

### Core optimization problem

The problem is formulated as a **two-stage stochastic Mixed-Integer Linear Program (MILP)**:

- **Stage 1 (non-anticipative):** Day-ahead net position `q_da[t]` — committed before any
  scenario is revealed.
- **Stage 2 (recourse, scenario-dependent):** Intraday trade `q_id[s,t]`, battery charge
  `p_ch[s,t]`, discharge `p_dis[s,t]`, and state-of-charge `soc[s,t]` — adapted to each
  realized price/generation path.

The objective is to maximize probability-weighted expected daily profit minus degradation cost,
subject to battery physical constraints, energy balance, and an optional CVaR risk penalty.

### Design philosophy

Every layer (data source, scenario generator, optimizer, solver) is independently swappable
via a single string in the config or function call. Adding a new scenario method (e.g. SARIMA)
requires writing one class and adding one line to the registry — no other file needs to change.

The system is deliberately scope-limited: Denmark DK1 market, 24 hourly periods, Horns Rev 1
wind plant (but the architecture supports other markets and plants without restructuring).

---

## 2. Repository Layout

```
storopt/
├── pyproject.toml                  Package metadata, dependencies, entry points
├── .env.example                    Environment variable template
├── CLAUDE.md                       Project documentation for AI-assisted development
├── DOCUMENTATION.md                This file
│
├── configs/
│   ├── default.yaml                Complete default config (DK1 + HR1 + HiGHS)
│   └── horns_rev1.yaml             HR1 case override (plant EIC, coordinates)
│
└── src/
    └── storopt/
        ├── __init__.py             Re-exports run_day, run_backtest
        ├── run.py                  PUBLIC API: run_day(), run_backtest()
        ├── cli.py                  CLI: storopt run-day, storopt run-backtest
        │
        ├── config/
        │   ├── schema.py           RunConfig (Pydantic): 7 validated sub-configs
        │   └── loader.py           load_config(): YAML merge + dot-notation overrides
        │
        ├── ingestion/
        │   ├── base.py             DataAdapter ABC
        │   ├── cache.py            Parquet range-covering cache
        │   ├── panel.py            build_panel(): fetch + inner join all sources
        │   └── adapters/
        │       ├── energinet.py    DK1 DA + ID prices (Energinet EDS, public)
        │       ├── entsoe.py       ENTSO-E plant generation (pre-fetched parquet)
        │       └── open_meteo.py   NWP weather (Open-Meteo Historical-Forecast API)
        │
        ├── scenarios/
        │   ├── base.py             ScenarioGenerator ABC
        │   ├── types.py            ScenarioBundle dataclass
        │   ├── registry.py         REGISTRY dict + get_generator()
        │   ├── knn.py              KNN historical analog-day generator
        │   └── naive.py            Naive: per-hour history mean → 1 scenario
        │
        ├── optimizer/
        │   ├── base.py             Optimizer ABC
        │   ├── types.py            OptimizationResult dataclass
        │   ├── registry.py         REGISTRY dict + get_optimizer()
        │   ├── milp.py             StochasticMILP (Pyomo + HiGHS/Gurobi)
        │   └── deterministic.py    DeterministicOptimizer (mean-scenario EEV)
        │
        └── evaluation/
            ├── engine.py           rolling_backtest(): daily loop
            └── metrics.py          VSS, EVPI, CRPS, throughput, drawdown
```

---

## 3. Installation and Setup

### Prerequisites

- Python ≥ 3.11
- HiGHS solver (bundled via `highspy` pip package — no separate install required)
- Gurobi ≥ 10 (optional; requires a separate licence and `gurobipy` installation)

### Install

```bash
# From the repo root
pip install -e .
```

This installs the `storopt` package in editable mode and creates the `storopt` CLI command.

### Environment variables

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

See [Section 14](#14-environment-variables) for the full list.

### Verify installation

```python
from storopt.config.loader import load_config
cfg = load_config("configs/horns_rev1.yaml")
print(cfg.scenarios.method)   # knn
print(cfg.solver.name)        # highs
```

---

## 4. Configuration System

### 4.1 Overview

Configuration is a two-level system:
1. **YAML files** — human-readable config stored in `configs/`.
2. **Pydantic models** — validated in-memory representation returned by `load_config()`.

All YAML keys mirror the Pydantic field names exactly. The default configuration
(`configs/default.yaml`) is always loaded first; a case-specific YAML (e.g.
`configs/horns_rev1.yaml`) is deep-merged on top, overriding only the keys it defines.

### 4.2 Config schema (`config/schema.py`)

The root model is `RunConfig`, which composes seven validated sub-configs:

```python
class RunConfig(BaseModel):
    ingestion: IngestionConfig
    bess:      BESSConfig
    market:    MarketConfig
    scenarios: ScenarioConfig
    optimizer: OptimizerConfig
    solver:    SolverConfig
    backtest:  BacktestConfig
```

#### `IngestionConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `area` | `str` | `"DK1"` | Market area for Energinet API |
| `entsoe_area` | `str` | `"10YDK-1--------W"` | ENTSO-E bidding zone EIC |
| `plant_eic` | `str` | `""` | ENTSO-E registered resource EIC |
| `generation_file` | `str` | `""` | Path to pre-fetched generation parquet file or directory |
| `weather_lat` | `float` | `0.0` | Plant latitude for Open-Meteo |
| `weather_lon` | `float` | `0.0` | Plant longitude for Open-Meteo |
| `cache_dir` | `str` | `"./data/cache"` | Parquet cache directory |
| `history_days` | `int` | `730` | Calendar days of history before target date (min 30) |

#### `BESSConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `power_charge_mw` | `float` | `1.0` | Max charge power [MW] |
| `power_discharge_mw` | `float` | `1.0` | Max discharge power [MW] |
| `energy_capacity_mwh` | `float` | `2.0` | Nominal energy capacity [MWh] |
| `soc_min_frac` | `float` | `0.10` | Minimum SOC fraction (0–1) |
| `soc_max_frac` | `float` | `0.90` | Maximum SOC fraction (0–1) |
| `soc_init_frac` | `float` | `0.50` | Initial (and terminal) SOC fraction |
| `eta_charge` | `float` | `0.95` | One-way charging efficiency |
| `eta_discharge` | `float` | `0.95` | One-way discharging efficiency |
| `deg_cost_eur_per_mwh` | `float` | `10.0` | Throughput degradation cost [€/MWh] |
| `max_cycles_per_day` | `float\|None` | `None` | EFC/day cap; `None` = unconstrained |

`BESSConfig` has computed properties:
- `soc_min_mwh` = `soc_min_frac × energy_capacity_mwh`
- `soc_max_mwh` = `soc_max_frac × energy_capacity_mwh`
- `soc_init_mwh` = `soc_init_frac × energy_capacity_mwh`
- `rte` = `eta_charge × eta_discharge` (round-trip efficiency)
- `usable_energy_mwh` = `(soc_max_frac − soc_min_frac) × energy_capacity_mwh`

A Pydantic validator ensures `soc_min_frac < soc_max_frac` and `soc_min_frac ≤ soc_init_frac ≤ soc_max_frac`.

#### `MarketConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `n_periods` | `int` | `24` | Periods per trading day |
| `dt_hours` | `float` | `1.0` | Duration of one period [h] |
| `da_price_floor_eur` | `float` | `-500.0` | DA price floor [€/MWh] |
| `da_price_ceil_eur` | `float` | `4000.0` | DA price ceiling [€/MWh] |
| `timezone` | `str` | `"Europe/Copenhagen"` | Local market timezone |
| `id_position_cap_mw` | `float\|None` | `None` | Hard cap on `\|q_id[s,t]\|` [MW] |
| `id_linear_penalty_eur_per_mwh` | `float\|None` | `None` | Linear penalty on intraday trade volume |

#### `ScenarioConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `method` | `Literal["knn","naive"]` | `"knn"` | Scenario generation method |
| `n_scenarios` | `int` | `20` | Number of scenarios to generate (1–500) |
| `params` | `dict` | `{}` | Method-specific parameters passed to the generator |

#### `OptimizerConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `method` | `Literal["stochastic_milp","deterministic"]` | `"stochastic_milp"` | Optimizer type |
| `cvar_enabled` | `bool` | `False` | Enable CVaR risk term in objective |
| `cvar_alpha` | `float` | `0.95` | CVaR confidence level (0, 1) |
| `cvar_weight` | `float` | `0.0` | CVaR objective weight (0 = risk-neutral) |

#### `SolverConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `Literal["highs","gurobi"]` | `"highs"` | Solver backend |
| `time_limit_s` | `int` | `300` | Solver time limit [s] |
| `mip_gap` | `float` | `0.001` | Relative MIP optimality gap |
| `threads` | `int\|None` | `None` | Solver threads; `None` = all available |
| `verbose` | `bool` | `False` | Print solver output |

#### `BacktestConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `vss_enabled` | `bool` | `True` | Compute VSS each day (1 extra solve) |
| `evpi_enabled` | `bool` | `False` | Compute EVPI each day (S extra solves) |

### 4.3 `load_config()` (`config/loader.py`)

```python
def load_config(path: str | Path | None = None, **overrides: Any) -> RunConfig
```

**Loading order:**

1. Load `configs/default.yaml` as the base.
2. If `path` is given, deep-merge the case YAML on top (only keys present in the case YAML are overridden; all others retain defaults).
3. Apply any `**overrides` using dot-notation or double-underscore notation.

**Examples:**

```python
# Load case config (inherits all defaults)
cfg = load_config("configs/horns_rev1.yaml")

# Override scenario method at load time
cfg = load_config("configs/horns_rev1.yaml", **{"scenarios.method": "naive"})

# Double-underscore notation also works
cfg = load_config("configs/horns_rev1.yaml", scenarios__method="naive")

# Load pure defaults (no case override)
cfg = load_config()
```

**Deep-merge semantics:** nested dicts are merged recursively. A case YAML that only sets
`ingestion.plant_eic` will not wipe out other `ingestion` fields.

---

## 5. Data Ingestion Layer

### 5.1 `DataAdapter` ABC (`ingestion/base.py`)

All data sources implement the same two-line interface:

```python
class DataAdapter(ABC):
    def fetch(self, start: date, end: date) -> pd.DataFrame: ...
```

The returned DataFrame always contains:
- `delivery_ts_utc` — UTC-timezone `datetime64[ns, UTC]`, one row per hour
- One or more value columns specific to the adapter

Adapters handle caching internally; callers just call `fetch()`.

### 5.2 Parquet cache (`ingestion/cache.py`)

`DataCache` is a simple file-based range-covering Parquet cache.

```
./data/cache/
    energinet_da_2023-01-15_2025-01-15.parquet
    energinet_id_2023-01-15_2025-01-15.parquet
    open_meteo_2023-01-15_2025-01-15.parquet
    entsoe_gen_2023-01-15_2025-01-15.parquet
    open_meteo_raw/
        om_55.5297N_7.9061E_2023-01-15_2024-01-15_<hash>.json
        ...
```

**Cache hit logic:** on `get(start, end)`, any existing file whose covered date range
fully contains `[start, end]` is a hit. The file is read, filtered to the exact requested
range, and returned. On miss, the adapter fetches live data and calls `put()` to write a
new file named `{adapter_name}_{start}_{end}.parquet`.

This is a simple append-only cache: old files accumulate. There is no eviction or
deduplication. A future improvement would be a range-union strategy.

### 5.3 Energinet adapter (`ingestion/adapters/energinet.py`)

Two adapters for the Danish TSO's public EDS REST API (no token required):

**`EnerginetDAAdapter`** — Day-ahead prices from `Elspotprices`:
- Dataset: `https://api.energidataservice.dk/dataset/Elspotprices`
- Filter: `PriceArea = DK1`, `timezone = UTC`
- Output column: `da_eur_mwh` (from `SpotPriceEUR`)

**`EnerginetIDAdapter`** — Intraday/imbalance price proxy from `RegulatingBalancePowerdata`:
- Dataset: `https://api.energidataservice.dk/dataset/RegulatingBalancePowerdata`
- Filter: `PriceArea = DK1`, `timezone = UTC`
- Output column: `id_eur_mwh` (from `ImbalancePriceEUR`)

Both adapters: check cache first, fetch on miss, deduplicate on `delivery_ts_utc`,
sort, save to cache, return. Retry logic: 2 retries with 2s/4s backoff.

**Note on the ID proxy:** The `RegulatingBalancePowerdata` dataset gives the imbalance
settlement price, which is used here as a proxy for intraday market prices. This is a
known approximation — actual intraday (continuous) prices require EPEX SPOT data, which
is not publicly available.

### 5.4 ENTSO-E generation adapter (`ingestion/adapters/entsoe.py`)

`EntsoeGenerationAdapter` reads pre-fetched parquet files (ENTSO-E does not provide a
public REST API for historical generation data in all cases).

**Two modes:**
1. **Single file:** `generation_file = "/path/to/hr1_generation.parquet"`
2. **Directory of monthly files:** `generation_file = "/path/to/hr1_monthly/"` — files
   named `*.parquet`; the adapter loads only the months that overlap `[start, end]`.

**Column detection:** the adapter automatically detects the generation column by looking
for `generation_mw`, `ActualGenerationOutput`, or `quantity` in the source file.

**Resolution handling:** if the source has sub-hourly data (e.g. 15-minute intervals),
timestamps are floored to the hour and grouped by `mean()` before returning.

**Output column:** `hr1_generation_mw`

### 5.5 Open-Meteo adapter (`ingestion/adapters/open_meteo.py`)

`OpenMeteoAdapter` fetches archived **NWP forecasts** (not observations) from the
Open-Meteo Historical-Forecast API. This is the leakage-safe data source for the KNN
query vector: because these are archived forecasts, what was "forecast" for a future
target date looks the same in backtest as it would in live operation.

**API endpoint:** `https://historical-forecast-api.open-meteo.com/v1/forecast`

**Default variables (wind-offshore profile for HR1):**

| Variable | Description |
|---|---|
| `temperature_2m` | Air temperature at 2 m [°C] |
| `surface_pressure` | Surface pressure [hPa] |
| `cloud_cover` | Cloud cover [%] |
| `wind_speed_10m` | Wind speed at 10 m [m/s] |
| `wind_speed_80m` | Wind speed at 80 m [m/s] |
| `wind_speed_100m` | Wind speed at 100 m [m/s] |
| `wind_speed_120m` | Wind speed at 120 m [m/s] |
| `wind_direction_80m` | Wind direction at 80 m [°] |
| `wind_gusts_10m` | Wind gusts at 10 m [m/s] |

All output columns are prefixed with `weather_` (e.g. `weather_wind_speed_80m`).

**Chunking:** the API has a 366-day limit per request. For longer ranges, the adapter
splits into 366-day chunks, fetches and caches each chunk separately as raw JSON, then
assembles a combined DataFrame and saves to the Parquet cache.

**Raw JSON cache:** individual chunk JSON files are stored in `{cache_dir}/open_meteo_raw/`
with filenames that encode coordinates, date range, and a variable hash. These survive
across process restarts, so re-running the same date range never re-fetches.

### 5.6 Panel builder (`ingestion/panel.py`)

```python
def build_panel(start: date, end: date, config: IngestionConfig) -> pd.DataFrame
```

Orchestrates all four adapters and inner-joins their output on `delivery_ts_utc`.

**Steps:**
1. Fetch DA prices via `EnerginetDAAdapter`
2. Fetch ID prices via `EnerginetIDAdapter`
3. Fetch NWP weather via `OpenMeteoAdapter`
4. Fetch generation via `EntsoeGenerationAdapter` (requires `config.generation_file`)
5. Ensure all `delivery_ts_utc` columns are UTC-aware
6. Floor all timestamps to the hour (handle any sub-hourly data)
7. Inner join all four DataFrames on `delivery_ts_utc`
8. Sort, deduplicate on timestamp, reset index

**Output schema:**

| Column | Type | Source |
|---|---|---|
| `delivery_ts_utc` | `datetime64[ns, UTC]` | Join key |
| `da_eur_mwh` | `float64` | Energinet Elspotprices |
| `id_eur_mwh` | `float64` | Energinet RegulatingBalancePowerdata |
| `hr1_generation_mw` | `float64` | ENTSO-E generation |
| `weather_wind_speed_80m` | `float64` | Open-Meteo |
| `weather_wind_speed_100m` | `float64` | Open-Meteo |
| `weather_wind_direction_80m` | `float64` | Open-Meteo |
| `weather_temperature_2m` | `float64` | Open-Meteo |
| `weather_surface_pressure` | `float64` | Open-Meteo |
| ... | `float64` | (one column per weather variable) |

**Critical rule:** any UTC hour missing from any source is dropped from the panel. There
is no forward-fill, backward-fill, or interpolation. Gaps in the data produce gaps in the
panel, and the scenario generators will exclude days with incomplete 24-hour coverage.

---

## 6. Scenario Generation Layer

### 6.1 `ScenarioGenerator` ABC (`scenarios/base.py`)

All generators implement two methods:

```python
class ScenarioGenerator(ABC):
    def fit(self, history: pd.DataFrame) -> None: ...
    def generate(self, target_date: date, n_scenarios: int) -> ScenarioBundle: ...
```

**`fit(history)`** receives the full canonical panel (including target-date weather rows
for the KNN query vector). Each generator is responsible for its own leakage guards.

**`generate(target_date, n_scenarios)`** returns a `ScenarioBundle` with shape `(S, T)`
arrays.

### 6.2 `ScenarioBundle` (`scenarios/types.py`)

```python
@dataclass
class ScenarioBundle:
    da_prices:      np.ndarray   # (S, T) — DA price per period [€/MWh]
    id_prices:      np.ndarray   # (S, T) — ID proxy price per period [€/MWh]
    res_generation: np.ndarray   # (S, T) — renewable generation [MW]
    probabilities:  np.ndarray   # (S,)   — scenario weights, must sum to 1
    target_date:    date
    generation_method: str = ""
    scenario_labels:   list[str] = field(default_factory=list)
```

Properties: `n_scenarios` (= S), `n_periods` (= T).

`validate()` asserts consistent shapes, no NaN values, and probabilities summing to 1.0
within tolerance 1e-6.

**Coherence invariant:** each row `s` represents one complete historical day. `da_prices[s,:]`,
`id_prices[s,:]`, and `res_generation[s,:]` all originate from the same source day. There
is no mixing of columns from different days.

### 6.3 Registry (`scenarios/registry.py`)

```python
REGISTRY = {
    "knn":   KNNScenarioGenerator,
    "naive": NaiveScenarioGenerator,
}

def get_generator(method: str, **params: Any) -> ScenarioGenerator
```

`get_generator()` looks up the class in `REGISTRY` and instantiates it with the given
`**params`. Raises `ValueError` with a list of available methods if the key is not found.

### 6.4 KNN generator (`scenarios/knn.py`)

The `KNNScenarioGenerator` implements historical analog-day scenario selection. The
algorithm finds the `n_scenarios` past days that were most similar to the target date
(as judged by features available *before* the decision point), then uses those days'
actual DA/ID/generation paths as stochastic scenarios.

#### KNNConfig — tunable parameters

All fields can be set via `config.scenarios.params`:

| Parameter | Default | Description |
|---|---|---|
| `probability_mode` | `"softmax"` | How to assign probabilities: `softmax`, `uniform`, `inverse_distance` |
| `softmax_temperature` | `1.0` | Temperature for softmax probability assignment |
| `information_cutoff_days` | `2` | Lag between target date and last usable lagged actuals |
| `distance_mode` | `"weighted_euclidean"` | Only `"weighted_euclidean"` is currently implemented |
| `feature_weights` | `{}` | Per-feature multipliers for distance computation |
| `standardize_features` | `True` | Z-score all features before distance computation |
| `diversity_min_gap_days` | `0` | Minimum calendar gap between selected neighbor dates |
| `enabled_feature_groups` | `("calendar","weather_forecast_target_day","lagged_da","lagged_id_spread","lagged_generation")` | Which feature groups to include |
| `weather_summary_stats` | `("mean","min","max","std")` | Summary statistics for per-day weather features |
| `peak_hours_utc` | `range(8,20)` | Peak hour definition for DA spread features |
| `da_column` | `"da_eur_mwh"` | DA price column name |
| `id_column` | `"id_eur_mwh"` | ID price column name |
| `gen_column` | `"hr1_generation_mw"` | Generation column name |
| `plant_capacity_mw` | `160.0` | Plant nameplate capacity for capacity-factor features |

#### Feature groups

**`calendar`** — day-of-week, month, weekend flag, and cyclical day-of-year encoding:
- `calendar_month`, `calendar_day_of_week`, `calendar_is_weekend`
- `calendar_doy_sin`, `calendar_doy_cos` (sin/cos encoding of day of year)

**`weather_forecast_target_day`** — mean, min, max, std of each weather variable over
the 24 target-day NWP forecast hours. These are leakage-safe because Open-Meteo provides
archived forecasts, not realized observations.

**`lagged_da`** — mean, min, max, std of DA prices over:
- lag-1 day (day `target_date - info_cutoff_days`)
- lag-7 window (7 days ending at the lag-1 day)

**`lagged_id_spread`** — same window summaries but for `id_eur_mwh - da_eur_mwh`.

**`lagged_generation`** — same window summaries plus `capacity_factor_mean` for generation.

**`regime_features`** (optional, not in default `enabled_feature_groups`) — peak/offpeak
price spreads, DA volatility, generation ramp features, wind CV.

#### Algorithm steps (inside `generate()`)

1. **Eligibility filter:** candidate days must have 24 complete rows with no NaN in DA,
   ID, and generation, AND must fall on or before `target_date - info_cutoff_days` AND
   strictly before `target_date`.

2. **Query vector construction:** build feature vectors for the target date and for each
   candidate day using only leakage-safe features.

3. **Standardization:** z-score features using candidate-pool statistics only (target
   date excluded from mean/std computation).

4. **Distance computation:** weighted Euclidean distance between target and each candidate
   after standardization.

5. **Selection:** take the `n_scenarios` closest candidates. If `diversity_min_gap_days > 0`,
   use a greedy diversity filter that enforces minimum calendar gaps between selected days
   (falling back to closest-first if not enough diverse candidates exist).

6. **Probability assignment:**
   - `softmax`: `p[i] ∝ exp(-d[i] / (median(d) × temperature))`
   - `inverse_distance`: `p[i] ∝ 1 / d[i]`
   - `uniform`: `p[i] = 1 / k`

7. **Path extraction:** for each selected day, read the 24-hour rows for DA, ID, and
   generation and assemble into `(S, T)` arrays.

#### Diagnostics

After `generate()`, `KNNScenarioGenerator.diagnostics()` returns a dict with:
- Feature column names and their values for the target query
- Feature scaling statistics
- Selected neighbor dates, distances, and probabilities
- Leakage guard status
- Candidate pool size

Persist diagnostics to disk with `persist_diagnostics(diag, out_dir, target_date=...)`.

### 6.5 Naive generator (`scenarios/naive.py`)

The `NaiveScenarioGenerator` collapses the full history into a single deterministic
scenario representing the per-hour mean across all history days. It always returns
`n_scenarios=1` regardless of the requested count.

**Use cases:** sanity check baseline, EEV computation in VSS analysis.

**Leakage guard:** `generate()` filters the panel to `date < target_date` before computing
per-hour means, preventing target-day actuals from entering the mean.

---

## 7. Optimization Layer

### 7.1 `Optimizer` ABC (`optimizer/base.py`)

```python
class Optimizer(ABC):
    def solve(self, bundle: ScenarioBundle, config: RunConfig) -> OptimizationResult: ...
```

### 7.2 `OptimizationResult` (`optimizer/types.py`)

```python
@dataclass
class OptimizationResult:
    da_bids:          np.ndarray   # (T,)    — first-stage DA net position [MW]
    id_trades:        np.ndarray   # (S, T)  — second-stage ID net trade [MW]
    charge:           np.ndarray   # (S, T)  — charge power [MW]
    discharge:        np.ndarray   # (S, T)  — discharge power [MW]
    soc:              np.ndarray   # (S, T)  — state of charge [MWh]
    scenario_profits: np.ndarray   # (S,)    — profit per scenario [€]
    probabilities:    np.ndarray   # (S,)    — scenario weights
    expected_profit:  float        # probability-weighted expected profit [€]
    solve_status:     str          # Pyomo termination condition string
    solve_time_s:     float        # wall-clock solver time [s]
    extras:           dict         # optional: cvar_value_eur, per_scenario_throughput_mwh, ...
```

Properties: `n_scenarios`, `n_periods`.

### 7.3 Registry (`optimizer/registry.py`)

```python
REGISTRY = {
    "stochastic_milp": StochasticMILP,
    "deterministic":   DeterministicOptimizer,
}

def get_optimizer(method: str) -> Optimizer
```

### 7.4 Stochastic MILP (`optimizer/milp.py`)

The main optimizer. Builds and solves a Pyomo `ConcreteModel` with HiGHS or Gurobi.

#### Sets

| Set | Description |
|---|---|
| `T = {1, …, n_periods}` | Time periods (1-indexed in Pyomo) |
| `S = {1, …, n_scenarios}` | Scenarios (1-indexed in Pyomo) |

#### Parameters (indexed)

| Parameter | Index | Description |
|---|---|---|
| `prob[s]` | S | Scenario probabilities |
| `da_price[s,t]` | S×T | DA price [€/MWh] |
| `id_price[s,t]` | S×T | ID price [€/MWh] |
| `res_gen[s,t]` | S×T | Renewable generation [MW] |

#### Scalar parameters

`dt`, `P_max_ch`, `P_max_dis`, `soc_min`, `soc_max`, `soc_init`, `eta_ch`, `eta_dis`, `c_deg`

#### Decision variables

**Stage 1 (non-anticipative):**

| Variable | Index | Domain | Bounds | Description |
|---|---|---|---|---|
| `q_da[t]` | T | Reals | `[-max_load, max_gen]` | DA net position [MW] |

**Stage 2 (recourse):**

| Variable | Index | Domain | Bounds | Description |
|---|---|---|---|---|
| `q_id[s,t]` | S×T | Reals | `[id_lb, id_ub]` | ID net trade [MW] |
| `p_ch[s,t]` | S×T | NonNeg | `[0, P_max_ch]` | Charge power [MW] |
| `p_dis[s,t]` | S×T | NonNeg | `[0, P_max_dis]` | Discharge power [MW] |
| `soc[s,t]` | S×T | Reals | `[soc_min, soc_max]` | State of charge [MWh] |
| `delta[s,t]` | S×T | Binary | `{0, 1}` | 1 = charging, 0 = discharging |

**Optional (CVaR):**

| Variable | Domain | Description |
|---|---|---|
| `eta` | Reals | CVaR auxiliary VaR variable |
| `cvar_short[s]` | NonNeg | CVaR shortfall per scenario |

#### Objective function

**Risk-neutral (default, `cvar_weight = 0`):**

```
max  Σ_s  prob[s] × profit(s)
```

where:

```
profit(s) = Σ_t da_price[s,t] × q_da[t] × dt
          + Σ_t id_price[s,t] × q_id[s,t] × dt
          - Σ_t c_deg × (p_ch[s,t] + p_dis[s,t]) × dt
          [ - Σ_t c_id_lin × (q_id_pos[s,t] + q_id_neg[s,t]) × dt  if id_linear_penalty > 0 ]
```

**Risk-averse (`cvar_weight > 0`):**

```
max  (1 - λ) × E[profit] + λ × CVaR_α(profit)
```

where `CVaR_α = eta - (1/α) × Σ_s prob[s] × cvar_short[s]` and
`cvar_short[s] ≥ eta - profit(s)`.

#### Constraints

**C1 — Energy balance** (for all `s, t`):
```
q_da[t] + q_id[s,t] = res_gen[s,t] + p_dis[s,t] - p_ch[s,t]
```
The total scheduled position (DA + ID adjustment) must equal renewable generation
plus battery net output.

**C2 — SOC dynamics** (for all `s, t > 1`):
```
soc[s,t] = soc[s,t-1] + eta_ch × p_ch[s,t-1] × dt - (1/eta_dis) × p_dis[s,t-1] × dt
```
State of charge evolves by accounting for charging losses (× eta_ch) and discharging
losses (÷ eta_dis).

**C3 — Initial SOC** (for all `s`):
```
soc[s,1] = soc_init
```

**C4 — Terminal SOC** (for all `s`):
```
soc[s,T] + eta_ch × p_ch[s,T] × dt - (1/eta_dis) × p_dis[s,T] × dt = soc_init
```
The battery must return to its initial SOC at the end of the day (circular daily scheduling).

**C5 — Mutual exclusivity** (for all `s, t`):
```
p_ch[s,t]  ≤ P_max_ch  × delta[s,t]
p_dis[s,t] ≤ P_max_dis × (1 - delta[s,t])
```
The binary variable `delta` prevents simultaneous charging and discharging.

**C6 — Cycle cap** (optional, for all `s`):
```
Σ_t (p_ch[s,t] + p_dis[s,t]) × dt ≤ 2 × max_cycles_per_day × energy_capacity_mwh
```

**C7 — ID position cap** (optional): if `market.id_position_cap_mw` is set, `q_id`
variable bounds are tightened symmetrically.

**C8 — ID linear penalty** (optional): if `market.id_linear_penalty_eur_per_mwh > 0`,
auxiliary variables `q_id_pos[s,t]`, `q_id_neg[s,t]` are introduced with `q_id =
q_id_pos - q_id_neg`, and a linear penalty is charged per MWh of intraday volume.

#### Variable bounds computation

The DA and ID position bounds are derived dynamically from the scenario bundle:
```
max_gen  = max(res_generation) + P_max_dis
max_load = max(res_generation) + P_max_ch
```
These represent the maximum possible net injection and net withdrawal.

#### Solver interface

**HiGHS (default):**
```python
opt = pyo.SolverFactory("appsi_highs")
opt.options["time_limit"] = slv.time_limit_s
opt.options["mip_rel_gap"] = slv.mip_gap
```

**Gurobi:**
```python
opt = pyo.SolverFactory("gurobi")
opt.options["TimeLimit"] = slv.time_limit_s
opt.options["MIPGap"] = slv.mip_gap
```

### 7.5 Deterministic optimizer (`optimizer/deterministic.py`)

`DeterministicOptimizer` collapses the `ScenarioBundle` to its probability-weighted mean,
creates a single-scenario bundle, and delegates to `StochasticMILP`.

```python
mean_da[t]  = Σ_s prob[s] × da_prices[s,t]
mean_id[t]  = Σ_s prob[s] × id_prices[s,t]
mean_gen[t] = Σ_s prob[s] × res_generation[s,t]
```

The returned result has shape `(1, T)` arrays (one "scenario" = the mean). This is the
**EV (Expected Value) solution** — the first step in VSS computation.

---

## 8. Evaluation Layer

### 8.1 Rolling backtest engine (`evaluation/engine.py`)

```python
def rolling_backtest(
    start: date,
    end: date,
    config: RunConfig,
    *,
    output_dir: str | Path | None = None,
) -> pd.DataFrame
```

Iterates `target_date` from `start` to `end` inclusive, calling `_run_one_day()` for each.

**`_run_one_day(target_date, config)`** performs:
1. Fetch panel for `[target_date - history_days, target_date]`
2. Instantiate and fit the scenario generator
3. Generate scenario bundle
4. Solve MILP
5. Optionally compute VSS / EVPI
6. Return a dict row (errors captured as traceback strings in the `error` column)

**Output DataFrame schema (one row per day):**

| Column | Type | Description |
|---|---|---|
| `target_date` | `str` | ISO date string |
| `scenario_method` | `str` | Scenario generation method used |
| `n_scenarios` | `int` | Number of scenarios |
| `expected_profit_eur` | `float` | Probability-weighted expected profit |
| `throughput_mwh` | `float` | Mean daily battery throughput across scenarios [MWh] |
| `solve_status` | `str` | Solver termination condition |
| `solve_time_s` | `float` | Wall-clock solver time [s] |
| `vss_eur` | `float` | Value of Stochastic Solution [€] (or NaN if disabled) |
| `evpi_eur` | `float` | Expected Value of Perfect Information [€] (or NaN if disabled) |
| `error` | `str\|None` | Full traceback if the day failed, else `None` |

If `output_dir` is given, writes:
- `daily_summary.parquet`
- `daily_summary.csv`

### 8.2 Metrics (`evaluation/metrics.py`)

#### VSS and EVPI (`compute_vss_evpi`)

```python
def compute_vss_evpi(
    bundle: ScenarioBundle,
    config: RunConfig,
    *,
    stochastic_result: OptimizationResult | None = None,
    compute_evpi: bool = True,
) -> dict[str, Any]
```

Computes three quantities:

| Quantity | Method | Description |
|---|---|---|
| `z_RP` | Solve stochastic MILP with S scenarios | Recourse Problem — optimal stochastic value |
| `z_EV` | Solve deterministic MILP on mean scenario | Expected Value solution |
| `z_EEV` | Fix DA bids from z_EV, re-solve full S scenarios | Expected value of z_EV solution |
| `z_WS` | Solve S single-scenario MILPs, weight by probability | Wait-and-See (perfect foresight) |

From these:
```
VSS  = z_RP - z_EEV    (how much the stochastic model is worth vs. mean-based planning)
EVPI = z_WS - z_RP     (how much perfect foresight would be worth vs. stochastic)
```

`EVPI` computation is expensive (S additional MILP solves) and controlled by
`config.backtest.evpi_enabled`.

Returns dict: `{z_rp, z_eev, z_ws, vss_eur, evpi_eur}`.

#### `compute_throughput_mwh`

```python
def compute_throughput_mwh(result: OptimizationResult, dt_hours: float) -> float
```

Mean daily battery throughput: total `(charge + discharge)` energy summed across all
time periods and scenarios, divided by number of scenarios.

```
throughput_mwh = Σ_{s,t} (p_ch[s,t] + p_dis[s,t]) × dt / S
```

#### `compute_crps`

Continuous Ranked Probability Score (empirical quantile form):

```python
def compute_crps(
    forecasted_quantiles: np.ndarray,
    actual: float,
    quantile_levels: np.ndarray,
) -> float
```

Lower is better. Measures the sharpness and calibration of a probabilistic forecast
expressed as a set of quantiles.

#### `compute_max_drawdown`

Peak-to-trough drawdown on the cumulative profit series:

```python
def compute_max_drawdown(profits: pd.Series) -> float
```

Returns the maximum reduction from any cumulative high to a subsequent cumulative low.

#### `summarize_backtest`

```python
def summarize_backtest(daily_summary: pd.DataFrame) -> dict[str, float]
```

Returns aggregate statistics: `n_days`, `total_expected_profit_eur`, `avg_expected_profit_eur`,
`total_throughput_mwh`, `avg_vss_eur`, `max_drawdown_eur`.

---

## 9. Public API

### `run_day()`

```python
from storopt import run_day
from storopt.config.loader import load_config
from datetime import date

def run_day(
    target_date: date,
    config: RunConfig | str | Path,
    *,
    scenario_method: str | None = None,
    n_scenarios: int | None = None,
    solver: str | None = None,
) -> OptimizationResult
```

**Parameters:**

| Parameter | Description |
|---|---|
| `target_date` | Trading day to optimize |
| `config` | `RunConfig` object, or path string/`Path` to a YAML file |
| `scenario_method` | Override `config.scenarios.method` (e.g. `"naive"`) |
| `n_scenarios` | Override `config.scenarios.n_scenarios` |
| `solver` | Override `config.solver.name` (e.g. `"gurobi"`) |

**What it does:**
1. Resolve config (load YAML if needed, apply overrides)
2. Fetch panel for `[target_date - history_days, target_date]`
3. Validate that history rows before `target_date` are present
4. Instantiate and fit the scenario generator on the full panel
5. Generate scenario bundle for `target_date`
6. Solve MILP
7. Return `OptimizationResult`

**Examples:**

```python
config = load_config("configs/horns_rev1.yaml")

# Default KNN, 20 scenarios
result = run_day(date(2025, 1, 15), config)

# Override to naive scenario method
result = run_day(date(2025, 1, 15), config, scenario_method="naive")

# Use Gurobi solver
result = run_day(date(2025, 1, 15), config, solver="gurobi")

# Print results
print(f"Expected profit: €{result.expected_profit:,.2f}")
print(f"Solve status:    {result.solve_status}")
print(f"DA bids [MW]:    {result.da_bids}")   # shape (24,)
print(f"SOC [MWh]:       {result.soc[0,:]}")  # shape (24,) for scenario 0
```

### `run_backtest()`

```python
from storopt import run_backtest

def run_backtest(
    start: date,
    end: date,
    config: RunConfig | str | Path,
    *,
    scenario_method: str | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame
```

**Parameters:**

| Parameter | Description |
|---|---|
| `start`, `end` | Inclusive date range for the backtest |
| `config` | `RunConfig` object, or path string/`Path` to a YAML file |
| `scenario_method` | Override scenario method for all days |
| `output_dir` | Write `daily_summary.parquet` and `.csv` here if given |

**Example:**

```python
from datetime import date
from storopt import run_backtest
from storopt.config.loader import load_config
from storopt.evaluation.metrics import summarize_backtest

config = load_config("configs/horns_rev1.yaml")

summary = run_backtest(
    date(2025, 1, 1),
    date(2025, 3, 31),
    config,
    output_dir="./results/q1_2025",
)

stats = summarize_backtest(summary)
print(f"Total profit: €{stats['total_expected_profit_eur']:,.0f}")
print(f"Avg VSS:      €{stats['avg_vss_eur']:,.0f}")
print(f"Max drawdown: €{stats['max_drawdown_eur']:,.0f}")
```

---

## 10. CLI Reference

The `storopt` CLI is installed as a console script (entry point: `storopt.cli:app`).

### `storopt run-day`

```
storopt run-day TARGET_DATE [OPTIONS]

Arguments:
  TARGET_DATE       Target trading date in YYYY-MM-DD format [required]

Options:
  --config, -c      Path to case YAML config [default: configs/default.yaml]
  --scenario, -s    Override scenario method (knn|naive)
  --n-scenarios, -n Override n_scenarios
  --solver          Override solver (highs|gurobi)
  --output, -o      Write result JSON to this path
```

**Examples:**

```bash
# Default KNN, HiGHS
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml

# Override to naive scenario
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario naive

# 5 scenarios, save result
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml \
  --n-scenarios 5 --output ./results/2025-01-15.json
```

**Output JSON schema** (when `--output` is given):

```json
{
  "target_date": "2025-01-15",
  "solve_status": "optimal",
  "expected_profit_eur": 1234.56,
  "solve_time_s": 0.12,
  "scenario_profits": [...],
  "probabilities": [...],
  "extras": { "per_scenario_throughput_mwh": [...] }
}
```

### `storopt run-backtest`

```
storopt run-backtest [OPTIONS]

Options:
  --start           Backtest start date YYYY-MM-DD [required]
  --end             Backtest end date YYYY-MM-DD [required]
  --config, -c      Path to case YAML config [default: configs/default.yaml]
  --scenario, -s    Override scenario method for all days
  --output-dir, -o  Directory for output parquet/CSV files
```

**Example:**

```bash
storopt run-backtest \
  --start 2025-01-01 \
  --end 2025-03-31 \
  --config configs/horns_rev1.yaml \
  --output-dir ./results/q1_2025
```

**Terminal summary output:**
```
Backtest 2025-01-01 → 2025-03-31 (90 days) | scenario=knn
Days completed:  90
Successful:      88
Total exp. profit: €98,234.50
Results written to results/q1_2025/
```

---

## 11. End-to-End Data Flow

```
run_day(target_date, config)
│
│  _resolve_config(config, ...)
│    ├─ load default.yaml
│    ├─ deep-merge case YAML
│    └─ apply dot-notation overrides
│    → RunConfig
│
│  build_panel(history_start, target_date, cfg.ingestion)
│    ├─ EnerginetDAAdapter.fetch()         → da_eur_mwh
│    │   ├─ DataCache.get()  → hit? return slice
│    │   └─ API fetch → DataCache.put() → return
│    │
│    ├─ EnerginetIDAdapter.fetch()         → id_eur_mwh
│    │   └─ (same cache pattern)
│    │
│    ├─ OpenMeteoAdapter.fetch()           → weather_*
│    │   ├─ DataCache.get()  → hit? return slice
│    │   └─ chunk loop:
│    │       ├─ raw JSON cache hit? load
│    │       └─ API fetch → save raw JSON
│    │       concat → DataCache.put() → return
│    │
│    └─ EntsoeGenerationAdapter.fetch()    → hr1_generation_mw
│        ├─ DataCache.get()  → hit? return slice
│        └─ read parquet file/dir → resample to hourly → DataCache.put()
│
│    inner join all four DataFrames on delivery_ts_utc
│    → panel: DataFrame (rows = UTC hours, cols = all signals)
│
│  get_generator("knn", **cfg.scenarios.params)
│    → KNNScenarioGenerator(**params)
│
│  generator.fit(panel)      ← full panel including target-date rows
│    store panel, detect weather columns
│
│  generator.generate(target_date, n_scenarios)
│    ├─ validate 24 complete target-day rows exist (for query vector)
│    ├─ _eligible_candidates(): filter to days with 24 complete rows,
│    │   no NaN in DA/ID/gen, date ≤ target - info_cutoff_days < target
│    ├─ build query vectors for target + all candidates
│    ├─ standardize using candidate-pool stats
│    ├─ compute weighted Euclidean distances
│    ├─ select k closest (+ optional diversity filter)
│    ├─ assign probabilities (softmax / inverse_distance / uniform)
│    └─ extract 24-hour DA/ID/gen paths for each selected day
│    → ScenarioBundle (S, T)
│
│  get_optimizer("stochastic_milp")
│    → StochasticMILP()
│
│  optimizer.solve(bundle, cfg)
│    ├─ bundle.validate()
│    ├─ _build(): construct Pyomo ConcreteModel
│    │   ├─ set parameters from bundle and config
│    │   ├─ declare variables (q_da, q_id, p_ch, p_dis, soc, delta)
│    │   ├─ declare constraints (C1–C6 + optional CVaR)
│    │   └─ declare objective
│    ├─ _solve(): SolverFactory("appsi_highs").solve()
│    └─ _extract(): read variable values, compute profits
│    → OptimizationResult
│
└─ return OptimizationResult
```

---

## 12. Key Invariants

These invariants must never be broken. Each encodes a real correctness or
no-free-lunch constraint.

### 1. KNN leakage guard

The target day's **actual** DA prices, ID prices, and generation output must never enter
the query vector used to select neighbor days. Violating this would constitute
look-ahead bias (using future information that a live system would not have).

**How it is enforced:**
- `_build_query_vector()` uses only calendar features, archived NWP weather, and
  **lagged** actuals ending at `target_date - info_cutoff_days`.
- `_eligible_candidates()` rejects any candidate day with `date > target_date - info_cutoff_days`.
- The `fit()` method receives the full panel (including target-date weather), but weather
  columns for the target date are NWP forecasts — leakage-safe by definition.

### 2. No synthetic data

No forward-fill, backward-fill, interpolation, or fabrication of missing values anywhere
in the pipeline. `build_panel()` uses inner join only. Candidate days with any missing
DA/ID/generation values are excluded by `_eligible_candidates()`.

### 3. Terminal SOC constraint

The battery must return to its initial SOC at the end of each day (constraint C4). This
prevents the optimizer from treating the battery as a free energy source by always
ending the day with less charge than it started.

### 4. Scenario coherence

Each scenario row in `ScenarioBundle` represents one complete historical calendar day.
`da_prices[s,:]`, `id_prices[s,:]`, and `res_generation[s,:]` are all drawn from the
same historical date. No mixing of signals from different days is allowed.

### 5. Non-anticipativity

`q_da[t]` is a Stage-1 variable with no scenario index — the same DA position is
committed across all scenarios. This is what makes the problem a true two-stage
stochastic program rather than a scenario-by-scenario optimization.

### 6. Inner join only in panel

`build_panel()` uses inner join on `delivery_ts_utc` across all four sources. Any UTC
hour where any source is missing is silently dropped. This ensures the panel never
contains rows with partially-observed signals.

---

## 13. Extending the System

### Adding a new scenario method (e.g. SARIMA)

**Step 1** — Create `src/storopt/scenarios/sarima.py`:

```python
from storopt.scenarios.base import ScenarioGenerator
from storopt.scenarios.types import ScenarioBundle

class SARIMAScenarioGenerator(ScenarioGenerator):
    def __init__(self, **params): ...
    def fit(self, history): ...
    def generate(self, target_date, n_scenarios) -> ScenarioBundle: ...
```

**Step 2** — Add one line to `src/storopt/scenarios/registry.py`:

```python
REGISTRY = {
    "knn":    KNNScenarioGenerator,
    "naive":  NaiveScenarioGenerator,
    "sarima": SARIMAScenarioGenerator,   # ← add this
}
```

**Step 3** — Use it:

```bash
storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario sarima
```

Or in Python:

```python
result = run_day(date(2025, 1, 15), config, scenario_method="sarima")
```

The `Literal["knn", "naive"]` type annotation on `ScenarioConfig.method` can be relaxed
to `str` when adding new methods, or extended to `Literal["knn", "naive", "sarima"]`.

### Adding a new optimizer

**Step 1** — Create `src/storopt/optimizer/cvar_milp.py` implementing `Optimizer`:

```python
from storopt.optimizer.base import Optimizer
from storopt.optimizer.types import OptimizationResult

class CVaRMILP(Optimizer):
    def solve(self, bundle, config) -> OptimizationResult: ...
```

**Step 2** — Register in `src/storopt/optimizer/registry.py`:

```python
REGISTRY = {
    "stochastic_milp": StochasticMILP,
    "deterministic":   DeterministicOptimizer,
    "cvar_milp":       CVaRMILP,   # ← add this
}
```

### Adding a new data source (e.g. Germany / SMARD)

**Step 1** — Create `src/storopt/ingestion/adapters/smard.py` implementing `DataAdapter`.

**Step 2** — Update `ingestion/panel.py` to instantiate the adapter when
`config.ingestion.area == "DE"` (or via a new `config.ingestion.market` field).

### Adding a new market area

1. Add a new case YAML in `configs/` (e.g. `configs/tennet_de.yaml`) overriding
   `ingestion.area`, `ingestion.entsoe_area`, `ingestion.plant_eic`, and coordinates.
2. Supply the corresponding generation parquet file and set `generation_file` in the YAML.
3. No source code changes needed for the optimization or scenario layer.

---

## 14. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ENTSOE_SECURITY_TOKEN` | Conditionally | ENTSO-E Transparency Platform REST API token. Required only if live ENTSO-E REST fetching is used. Not needed when `generation_file` points to a pre-fetched parquet. |
| `ENTSOE_FMS_USER` | No | FMS bulk download username (bulk historical generation download fallback). |
| `ENTSOE_FMS_PASS` | No | FMS bulk download password. |

All variables are loaded from `.env` at CLI startup via `python-dotenv`. When using the
Python API directly, call `from dotenv import load_dotenv; load_dotenv()` before the
first `build_panel()` call if you rely on these variables.

The Energinet EDS API (DA and ID prices) is fully public and requires no credentials.
The Open-Meteo Historical-Forecast API is also public and requires no credentials.
