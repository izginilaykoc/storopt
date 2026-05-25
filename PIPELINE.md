# storopt — Full System Pipeline

## Overview

storopt answers one question every day at gate-closure:

> Given what the wind will do tomorrow and what prices have looked like on similar days, how should we bid in the day-ahead market and operate the battery intraday to maximize expected profit?

The output is a set of **market orders** and **battery operating schedules** — one for each price scenario — expressed in MW and MWh for each of the 24 hours of the next trading day.

---

## Pipeline at a Glance

```
CONFIG FILE (.yaml)
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1 — INGESTION                                            │
│                                                                 │
│  Energinet EDS (public, no token)                               │
│    ├─ Elspotprices            → DA prices [€/MWh]              │
│    ├─ RegulatingBalancePower  → ID imbalance prices [€/MWh]    │
│    └─ GenerationProdType...   → DK1 offshore wind [MW] × 0.100 │
│                                          (160 MW / 1 592 MW)    │
│  Open-Meteo Historical Forecast API (public, no token)         │
│    └─ wind speed/dir, temperature, cloud cover at plant coords  │
│                                                                 │
│  Output: hourly panel (inner-joined, UTC, zero NaN)            │
│    delivery_ts_utc | da_eur_mwh | id_eur_mwh |                 │
│    hr1_generation_mw | weather_*                                │
└─────────────────────────────────────────────────────────────────┘
      │  2 years of history (default 730 days)
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2 — SCENARIO GENERATION (KNN)                           │
│                                                                 │
│  Input A — TARGET DAY query vector (leakage-safe):             │
│    • Calendar: day-of-week, month, season flags                 │
│    • NWP forecast (archived Open-Meteo): wind speed/dir at     │
│      80m, temperature, cloud cover — mean/min/max/std of day   │
│                                                                 │
│  Input B — HISTORY query vectors (same features, lagged):      │
│    • Lagged DA mean/peak, ID-DA spread mean/peak               │
│    • Lagged generation mean/peak                               │
│    • All lagged > 2 days before target (cutoff guard)          │
│                                                                 │
│  KNN search:                                                    │
│    Weighted Euclidean distance in standardised feature space    │
│    → picks N most similar historical days (default N=20)       │
│    → assigns softmax probabilities (closer = more probable)    │
│                                                                 │
│  Each selected day's ACTUAL DA prices, ID prices, generation   │
│  become one scenario. No mixing across days.                   │
│                                                                 │
│  Output: ScenarioBundle                                         │
│    da_prices     (S × 24)  [€/MWh]                            │
│    id_prices     (S × 24)  [€/MWh]                            │
│    res_generation (S × 24)  [MW]                               │
│    probabilities  (S,)      sums to 1.0                        │
└─────────────────────────────────────────────────────────────────┘
      │  S=20 scenarios, each a coherent historical day
      ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3 — TWO-STAGE STOCHASTIC MILP (Pyomo + HiGHS)          │
│                                                                 │
│  Config inputs:                                                 │
│    BESSConfig   — power limits, capacity, SOC bounds,          │
│                   round-trip efficiency (95%×95%=90.25%),      │
│                   degradation cost [€/MWh throughput]          │
│    MarketConfig — 24 periods, DA price floor/ceil              │
│    OptimizerConfig — risk-neutral or CVaR-weighted             │
│                                                                 │
│  Stage 1 decision (same across all scenarios):                 │
│    q_da[t]  — DA net position [MW], t=1..24                    │
│                                                                 │
│  Stage 2 decisions (per scenario):                             │
│    q_id[s,t] — ID adjustment [MW]                              │
│    p_ch[s,t] — charge power [MW]                               │
│    p_dis[s,t] — discharge power [MW]                           │
│    soc[s,t]   — state of charge at start of period t [MWh]    │
│                                                                 │
│  Hard constraints:                                             │
│    Energy balance: q_da[t] + q_id[s,t]                        │
│                  = generation[s,t] + p_dis[s,t] - p_ch[s,t]   │
│    SOC dynamics:  soc[t+1] = soc[t] + η_ch·p_ch - p_dis/η_dis │
│    Terminal SOC:  battery must end at same SOC it started      │
│    Mutual excl.:  cannot charge and discharge simultaneously   │
│    Cycle cap:     optional daily EFC limit                     │
│                                                                 │
│  Objective: maximise Σ_s prob[s] × profit[s]                  │
│    profit[s] = Σ_t da_price[s,t]·q_da[t]                      │
│              + Σ_t id_price[s,t]·q_id[s,t]                    │
│              - Σ_t c_deg·(p_ch[s,t] + p_dis[s,t])            │
│                                                                 │
│  Output: OptimizationResult                                     │
│    da_bids           (24,)     [MW]                            │
│    id_trades         (S, 24)   [MW]                            │
│    charge            (S, 24)   [MW]                            │
│    discharge         (S, 24)   [MW]                            │
│    soc               (S, 24)   [MWh]                           │
│    scenario_profits  (S,)      [€]                             │
│    expected_profit   float     [€]                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Inputs

| Input | Source | Requires token? | What it covers |
|---|---|---|---|
| DA spot prices | Energinet `Elspotprices` | No | Historical hourly day-ahead [€/MWh] for DK1 |
| ID imbalance prices | Energinet `RegulatingBalancePowerdata` | No | Historical hourly balancing/ID proxy [€/MWh] for DK1 |
| Wind generation | Energinet `GenerationProdTypeExchange` | No | DK1 total offshore [MW] × 0.100 ≈ HR1 capacity share |
| NWP weather forecasts | Open-Meteo Historical Forecast API | No | Archived NWP runs at plant coordinates (not post-hoc observations) |
| Plant/battery config | YAML config file | — | Capacity, efficiency, SOC limits, degradation cost |

All fetched data is cached as Parquet in `./data/cache/` — subsequent runs for the same date range are served from disk.

---

## Outputs and Their Real-World Meaning

### `da_bids` — shape (24,) [MW]

**What the solver decides before knowing which scenario materialises.**

This is your **day-ahead market submission**, one number per hour, filed at gate-closure (~12:00 CET the day before delivery).

| Value | Real-world meaning |
|---|---|
| Positive (e.g. +8 MW) | You are a **seller** in the DA market for that hour. You commit to deliver 8 MW at the clearing price. This typically covers wind generation that will flow anyway, plus any discharge planned across most scenarios. |
| Negative (e.g. −4 MW) | You are a **buyer** in the DA market for that hour. You commit to receive 4 MW at the clearing price. This happens when the battery plans to charge in a low-price hour and generation is lower than the charge power needed. |
| Zero | You hold no DA position for that hour — all energy will be handled intraday. Unusual; the solver will generally prefer to lock in DA revenue. |

**Key invariant:** `q_da[t]` is the same regardless of which price scenario actually occurs. This is the non-anticipative, "commit before uncertainty resolves" decision.

---

### `id_trades` — shape (S, 24) [MW]

**Intraday market corrections, one value per scenario per hour.**

Filed on EPEX Spot Intraday (continuous) during the delivery day, after more accurate wind forecasts become available.

| Value | Real-world meaning |
|---|---|
| Positive `q_id[s,t]` | Extra selling in intraday: actual generation came in higher than DA bid, or DA prices turned out low and ID prices are high. Sell the surplus or re-optimise. |
| Negative `q_id[s,t]` | Buy back in intraday: actual generation came in lower than DA bid (need to cover the DA commitment), or ID prices are cheap and it's worth charging more. |

Net position submitted to the grid each hour = `q_da[t] + q_id[s,t]`.

If you end up with a residual imbalance despite ID trading, TSO settles it at imbalance price — this adapter uses `RegulatingBalancePowerdata` as the ID proxy because it approximates what the imbalance settlement price looks like.

---

### `charge` and `discharge` — shape (S, 24) [MW]

**Battery operating schedule, per scenario.**

| Variable | Real-world meaning |
|---|---|
| `charge[s,t]` | MW drawn from the grid into the battery at hour t under scenario s. Reduces net market position (you appear as a load). |
| `discharge[s,t]` | MW pushed from the battery into the grid at hour t under scenario s. Increases net market position (you appear as a generator). |

The mutual-exclusivity binary constraint (`delta[s,t]`) ensures the battery never charges and discharges at the same time — this mirrors real inverter control logic and prevents artificial round-trip losses in the model.

---

### `soc` — shape (S, 24) [MWh]

**State of charge at the start of each hour, per scenario.**

The value at `soc[s, t=0]` is fixed to `soc_init_mwh` (config). The terminal constraint forces `soc` after the last action back to `soc_init_mwh`.

| Bound | Real-world meaning |
|---|---|
| `soc_min_mwh` (10% of capacity) | Minimum buffer — protects cell chemistry, prevents deep discharge. |
| `soc_max_mwh` (90% of capacity) | Maximum usable — leaves headroom to avoid overcharge. |
| Terminal = Initial | Battery starts and ends each day at the same SOC, so you don't "borrow" energy from the next day's capacity. |

---

### `scenario_profits` — shape (S,) [€]

**Total daily profit under each scenario.**

Profit = DA revenue + ID revenue − degradation cost.

```
profit[s] = Σ_t da_price[s,t] · q_da[t]       ← DA revenue
          + Σ_t id_price[s,t] · q_id[s,t]      ← ID revenue
          − Σ_t c_deg · (p_ch[s,t]+p_dis[s,t]) ← wear cost (€/MWh throughput)
```

The spread across scenarios tells you how sensitive today's profit is to which price day materialises. A wide spread = high scenario risk.

---

### `expected_profit` — float [€]

**The probability-weighted average of `scenario_profits`.**

```
E[profit] = Σ_s prob[s] × profit[s]
```

This is the optimizer's objective value — the single number it maximises. In a backtest, comparing this to the deterministic (naive) solution gives the **Value of Stochastic Solution (VSS)**: how much having N scenarios instead of just one mean scenario is worth in expected profit.

---

## Information Flow for One Trading Day

```
Day D−1, 12:00 CET (DA gate-closure)
│
├─ Ingestion runs, fetches history up to D−2 (2-day lag)
│   └─ panel: 730 days × 24 hours × 13 columns → ~420 k rows
│
├─ KNN query built for day D:
│   └─ features: archived NWP wind forecast for D + calendar + lagged prices/gen from ≤D−2
│
├─ KNN finds 20 nearest historical days from the panel
│   └─ each day's actual prices and generation become one scenario
│
├─ MILP solves (~1–30 s with HiGHS):
│   └─ decides q_da[0..23] that is robust across all 20 scenarios
│
└─ OUTPUT: submit q_da[t] to EPEX Spot DA auction
           save {id_trades, charge, discharge, soc} as contingency plan

Day D (intraday, per hour):
    As each hour approaches, observe actual wind and prices,
    execute the scenario-specific id_trades / charge / discharge
    plan that best matches the unfolding reality.
```

---

## What the System Does NOT Do

| Gap | Why it matters |
|---|---|
| **No explicit generation forecast model** | Generation is implicit in KNN analog days. A power-curve model (NWP wind speed → MW) would be more accurate, especially for rare wind regimes. |
| **Generation is a DK1 aggregate proxy** | `hr1_generation_mw` = DK1 offshore × 0.100. Real HR1 metering (SCADA) would be exact. |
| **No execution layer** | The system produces orders but does not submit them to any exchange API. |
| **No real-time re-optimisation** | The intraday contingency plan is pre-computed; it does not re-solve as prices evolve during the day. |
| **No curtailment** | Wind generation is always fully injected. The energy balance is a hard equality — the battery and market positions absorb everything the turbines produce. |
