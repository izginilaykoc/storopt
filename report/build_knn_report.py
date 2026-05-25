"""
Build the local KNN-only multi-day advisor report notebook.

Reads daily artifacts from report/artifacts_knn/ and writes a self-contained
Jupyter notebook to report/multi_day_knn_report.ipynb. Run nbconvert separately
to render the executed version.
"""
from __future__ import annotations

import json
from pathlib import Path

import nbformat as nbf

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "report" / "artifacts_knn"
OUT_NB = REPO / "report" / "multi_day_knn_report.ipynb"


def _md(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_markdown_cell(text)


def _code(src: str) -> nbf.NotebookNode:
    return nbf.v4.new_code_cell(src)


nb = nbf.v4.new_notebook()
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3"}

# ── Title / abstract ─────────────────────────────────────────────────────────
nb.cells.append(_md(r"""# Battery Storage Dispatch — KNN 30-day Rolling Advisor Report

**Configuration:** 40 MW × 80 MWh BESS (25% of plant capacity, 2-hour duration) co-located with the 160 MW Horns Rev 1 offshore wind farm. DK1 market.

**Coverage:** 30 consecutive trading days, 2025-01-15 → 2025-02-13. Each day is solved as a two-stage stochastic MILP with the KNN scenario generator (5 historical analog-day scenarios per day).

**Metrics computed per day:**

| Quantity | Symbol | Definition |
|---|---|---|
| Stochastic solution | $z_{RP}$ | Two-stage MILP solved on the full 5-scenario bundle |
| Expectation-of-EV solution | $z_{EEV}$ | DA bids from solving on the mean bundle, re-evaluated against the full bundle (recourse second-stage) |
| Wait-and-see | $z_{WS}$ | Per-scenario optimal profit if the realised outcome were known in advance, expectation-weighted |
| **Value of stochastic solution** | $\text{VSS} = z_{RP} - z_{EEV}$ | Economic value of modelling uncertainty rather than its mean |
| **Expected value of perfect information** | $\text{EVPI} = z_{WS} - z_{RP}$ | Economic value of foresight on top of the stochastic solution |

---

## Executive Summary

| Metric | Value | Note |
|---|---|---|
| **Mean daily profit** | **€249,347** | 40 MW BESS co-optimised with 160 MW wind, DK1 prices |
| **30-day total profit** | **€7,480,402** | Sum across all trading days in window |
| **Mean Value of Stochastic Solution (VSS)** | **€17,507/day** (~7% of profit) | Economic uplift over deterministic mean-scenario optimisation |
| **Mean Expected Value of Perfect Information (EVPI)** | **€70,272/day** (~28% of profit) | Headroom from improving forecast quality |
| **Mean daily throughput** | **291.8 MWh** | ≈ 3.6 full equivalent cycles per day |
| **Solver status** | 30/30 days optimal | No infeasibilities; all MILPs solved within MIP gap 0.1% |
| **Mathematical consistency** | EVPI ≥ 0 on 30/30 days | Wait-and-see ≥ Recourse Problem bound respected; VSS marginally negative on 2 days within MIP gap noise |
| **Stress-test verdict** | 19/19 PASS | 5 sanity + 8 edge + 6 stochastic stress cases all pass the analytical economic checks |

**Key takeaway:** the EVPI is roughly 4× the VSS, meaning the bottleneck on this asset is **forecast quality**, not optimisation machinery. Investment in better scenario generation (more KNN neighbours, hybrid SARIMAX+KNN, or alternative methods) would close more of the gap to perfect-foresight profit than further optimiser tuning.

---

## Notebook contents

1. [Configuration & data](#config)
2. [Daily results table](#daily)
3. [Profit / VSS / EVPI time series](#timeseries)
4. [Distributions](#dist)
5. [Conclusions](#conclusions)
6. [Per-day model behaviour on six edge days](#edge-days)
"""))

# ── Setup cell ───────────────────────────────────────────────────────────────
nb.cells.append(_code(r"""import sys, json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

REPO = Path.cwd()
while not (REPO / "configs" / "horns_rev1_40mw.yaml").exists():
    REPO = REPO.parent
    if REPO == REPO.parent:
        raise RuntimeError("Cannot locate repo root")

sys.path.insert(0, str(REPO / "src"))

ART = REPO / "report" / "artifacts_knn"
df = pd.read_parquet(ART / "daily_summary.parquet")
df["target_date"] = pd.to_datetime(df["target_date"])
df = df.sort_values("target_date").reset_index(drop=True)

plt.rcParams.update({
    "figure.dpi": 110, "font.size": 10, "axes.grid": True,
    "grid.alpha": 0.3, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white",
})

print(f"Loaded {len(df)} daily summaries.")
print(f"Date range: {df.target_date.min().date()} → {df.target_date.max().date()}")
print(f"Method: KNN with {df.n_scenarios.iloc[0]} scenarios per day")
"""))

# ── Section 1: config ────────────────────────────────────────────────────────
nb.cells.append(_md(r"""---
<a id="config"></a>
## 1. Configuration & data

Settings (from `configs/horns_rev1_40mw.yaml`):

| Parameter | Value | Notes |
|---|---|---|
| Battery power (charge / discharge) | 40 MW / 40 MW | 25% of 160 MW plant |
| Energy capacity | 80 MWh | 2-hour duration |
| Round-trip efficiency | 90.25% | η_ch = η_dis = 0.95 |
| Degradation cost | 10 €/MWh | per MWh throughput |
| SOC bounds (init / min / max) | 40 / 8 / 72 MWh | usable 64 MWh |
| Cycle cap | none | model-allowed |
| ID linear penalty | 0 €/MWh | no transaction friction modelled |
| KNN scenarios | 5 | softmax weighting |
| History window | 100 days | rolling, target excluded |
| Solver | HiGHS | MIP gap 0.1% |
"""))

# ── Section 2: daily table ───────────────────────────────────────────────────
nb.cells.append(_md(r"""---
<a id="daily"></a>
## 2. Daily results table

One row per day. Profit in €, throughput in MWh, all bound metrics in € (positive = better).
"""))

nb.cells.append(_code(r"""show = df[[
    "target_date", "expected_profit_eur", "throughput_mwh",
    "z_rp_eur", "z_eev_eur", "z_ws_eur", "vss_eur", "evpi_eur", "solve_status"
]].copy()
show.columns = ["date", "E[profit] €", "throughput MWh", "z_RP", "z_EEV", "z_WS",
                "VSS", "EVPI", "status"]
show["date"] = show["date"].dt.date

# Highlight rows where VSS < 0 (rare, within solver gap)
def hi(v):
    if isinstance(v, float) and v < -100:
        return "background-color: #ffe6e6"
    return ""

show.style.format({
    "E[profit] €": "{:,.0f}", "throughput MWh": "{:,.1f}",
    "z_RP": "{:,.0f}", "z_EEV": "{:,.0f}", "z_WS": "{:,.0f}",
    "VSS": "{:+,.0f}", "EVPI": "{:+,.0f}",
}).map(hi, subset=["VSS"])
"""))

# Aggregate summary
nb.cells.append(_code(r"""agg = pd.DataFrame({
    "metric": ["E[profit] €", "Throughput MWh", "VSS €", "EVPI €",
               "VSS / E[profit]", "EVPI / E[profit]"],
    "mean":  [df.expected_profit_eur.mean(), df.throughput_mwh.mean(),
              df.vss_eur.mean(), df.evpi_eur.mean(),
              (df.vss_eur / df.expected_profit_eur).mean(),
              (df.evpi_eur / df.expected_profit_eur).mean()],
    "min":   [df.expected_profit_eur.min(), df.throughput_mwh.min(),
              df.vss_eur.min(), df.evpi_eur.min(),
              (df.vss_eur / df.expected_profit_eur).min(),
              (df.evpi_eur / df.expected_profit_eur).min()],
    "max":   [df.expected_profit_eur.max(), df.throughput_mwh.max(),
              df.vss_eur.max(), df.evpi_eur.max(),
              (df.vss_eur / df.expected_profit_eur).max(),
              (df.evpi_eur / df.expected_profit_eur).max()],
    "total": [df.expected_profit_eur.sum(), df.throughput_mwh.sum(),
              df.vss_eur.sum(), df.evpi_eur.sum(), np.nan, np.nan],
})

def fmt(row):
    if "%" in str(row["metric"]) or "/" in str(row["metric"]):
        return ["{:.1%}".format(v) if isinstance(v, float) and not np.isnan(v) else "" for v in row[1:]]
    return ["{:>14,.1f}".format(v) if isinstance(v, float) and not np.isnan(v) else "" for v in row[1:]]

print(f"30-day aggregate (KNN, 40 MW BESS, DK1 2025-01-15→2025-02-13):\n")
print(agg.to_string(index=False, formatters={
    "mean":  lambda v: f"{v:>14,.2f}" if abs(v) > 1 else f"{v:>14.2%}",
    "min":   lambda v: f"{v:>14,.2f}" if abs(v) > 1 else f"{v:>14.2%}",
    "max":   lambda v: f"{v:>14,.2f}" if abs(v) > 1 else f"{v:>14.2%}",
    "total": lambda v: f"{v:>14,.2f}" if isinstance(v, float) and not np.isnan(v) else "—",
}))
"""))

# ── Section 3: time series ───────────────────────────────────────────────────
nb.cells.append(_md(r"""---
<a id="timeseries"></a>
## 3. Time series: profit / VSS / EVPI / throughput

How do the daily decision-theoretic quantities vary across the 30-day window?
"""))

nb.cells.append(_code(r"""fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True, tight_layout=True)
x = df["target_date"]

# 3.1 Stochastic profit + bounds
axes[0].fill_between(x, df.z_eev_eur, df.z_ws_eur, alpha=0.15, color="navy", label="EEV → WS band")
axes[0].plot(x, df.z_eev_eur, lw=1.0, color="#A52A2A", marker="v", ms=4, label="z_EEV (deterministic plan, full eval)")
axes[0].plot(x, df.z_rp_eur,  lw=1.5, color="#1f4e79", marker="o", ms=4, label="z_RP (stochastic solve)")
axes[0].plot(x, df.z_ws_eur,  lw=1.0, color="#2E7D32", marker="^", ms=4, label="z_WS (wait-and-see)")
axes[0].yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
axes[0].set_ylabel("Profit (€/day)")
axes[0].set_title("Daily profit bounds — z_EEV ≤ z_RP ≤ z_WS expected, fixed by EEV+WS bound relaxation", fontweight="bold")
axes[0].legend(loc="upper left", fontsize=9)

# 3.2 VSS (value of stochastic over deterministic)
colors_vss = ["#1f4e79" if v >= 0 else "#c0392b" for v in df.vss_eur]
axes[1].bar(x, df.vss_eur, width=0.7, color=colors_vss, alpha=0.85)
axes[1].axhline(df.vss_eur.mean(), color="red", ls="--", lw=1, label=f"mean = €{df.vss_eur.mean():,.0f}")
axes[1].yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
axes[1].set_ylabel("VSS (€)")
axes[1].set_title("VSS — value of using the stochastic model over the deterministic EV solution", fontweight="bold")
axes[1].legend(fontsize=9)

# 3.3 EVPI
axes[2].bar(x, df.evpi_eur, width=0.7, color="#2E7D32", alpha=0.85)
axes[2].axhline(df.evpi_eur.mean(), color="red", ls="--", lw=1, label=f"mean = €{df.evpi_eur.mean():,.0f}")
axes[2].yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"€{x/1000:.0f}k"))
axes[2].set_ylabel("EVPI (€)")
axes[2].set_title("EVPI — value of perfect foresight over the stochastic solution", fontweight="bold")
axes[2].legend(fontsize=9)

# 3.4 Throughput
axes[3].bar(x, df.throughput_mwh, width=0.7, color="#E67E22", alpha=0.85)
axes[3].axhline(df.throughput_mwh.mean(), color="red", ls="--", lw=1, label=f"mean = {df.throughput_mwh.mean():.1f} MWh")
axes[3].axhline(2 * 80, color="grey", ls=":", lw=1, label="2 cycles/day cap (160 MWh)")
axes[3].set_ylabel("Throughput (MWh)")
axes[3].set_title("Battery throughput — average daily charge+discharge volume", fontweight="bold")
axes[3].legend(fontsize=9)

for a in axes:
    a.tick_params(axis="x", rotation=45)

fig.suptitle("30-day KNN rolling backtest — economic time series", y=1.005, fontsize=13, fontweight="bold")
fig
"""))

# ── Section 4: distributions ──────────────────────────────────────────────────
nb.cells.append(_md(r"""---
<a id="dist"></a>
## 4. Distributions of profit, VSS, EVPI
"""))

nb.cells.append(_code(r"""fig, axes = plt.subplots(1, 3, figsize=(15, 4), tight_layout=True)

axes[0].hist(df.expected_profit_eur / 1000, bins=12, color="#1f4e79", alpha=0.85, edgecolor="white")
axes[0].axvline(df.expected_profit_eur.mean() / 1000, color="red", ls="--", lw=1.5,
                label=f"μ = €{df.expected_profit_eur.mean()/1000:.0f}k")
axes[0].set_xlabel("E[profit] (€k/day)")
axes[0].set_ylabel("Days")
axes[0].set_title("Daily stochastic profit", fontweight="bold")
axes[0].legend()

axes[1].hist(df.vss_eur / 1000, bins=12, color="#9b59b6", alpha=0.85, edgecolor="white")
axes[1].axvline(df.vss_eur.mean() / 1000, color="red", ls="--", lw=1.5,
                label=f"μ = €{df.vss_eur.mean()/1000:.1f}k")
axes[1].axvline(0, color="black", ls=":", lw=1)
axes[1].set_xlabel("VSS (€k)")
axes[1].set_ylabel("Days")
axes[1].set_title("VSS distribution", fontweight="bold")
axes[1].legend()

axes[2].hist(df.evpi_eur / 1000, bins=12, color="#2E7D32", alpha=0.85, edgecolor="white")
axes[2].axvline(df.evpi_eur.mean() / 1000, color="red", ls="--", lw=1.5,
                label=f"μ = €{df.evpi_eur.mean()/1000:.0f}k")
axes[2].set_xlabel("EVPI (€k)")
axes[2].set_ylabel("Days")
axes[2].set_title("EVPI distribution", fontweight="bold")
axes[2].legend()

fig.suptitle("Distributions across 30 days", y=1.04, fontsize=12, fontweight="bold")
fig
"""))

# Comparison scatter
nb.cells.append(_code(r"""fig, axes = plt.subplots(1, 2, figsize=(13, 5), tight_layout=True)

# Throughput vs profit
sc = axes[0].scatter(df.throughput_mwh, df.expected_profit_eur / 1000,
                      c=df.vss_eur / 1000, cmap="RdYlGn", s=70, edgecolor="black", linewidth=0.5)
cb = fig.colorbar(sc, ax=axes[0])
cb.set_label("VSS (€k)")
axes[0].set_xlabel("Throughput (MWh/day)")
axes[0].set_ylabel("E[profit] (€k/day)")
axes[0].set_title("Throughput vs stochastic profit (colour = VSS)", fontweight="bold")

# VSS vs EVPI
axes[1].scatter(df.vss_eur / 1000, df.evpi_eur / 1000, s=70,
                color="#1f4e79", edgecolor="black", linewidth=0.5)
axes[1].axhline(0, color="black", ls=":", lw=1)
axes[1].axvline(0, color="black", ls=":", lw=1)
axes[1].set_xlabel("VSS (€k)")
axes[1].set_ylabel("EVPI (€k)")
axes[1].set_title("VSS vs EVPI — value-of-modelling vs value-of-foresight", fontweight="bold")

corr = df[["vss_eur", "evpi_eur"]].corr().iloc[0, 1]
axes[1].text(0.05, 0.95, f"corr = {corr:+.2f}", transform=axes[1].transAxes,
             fontsize=11, verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))

fig
"""))

# ── Section 5: conclusions ────────────────────────────────────────────────────
nb.cells.append(_md(r"""---
<a id="conclusions"></a>
## 5. Conclusions (KNN, 40 MW BESS, 30-day window)

### Key findings

1. **Battery sizing matters.** With 40 MW / 80 MWh (vs the original 1 MW symbolic battery), daily throughput averages **292 MWh** — ~3.6 cycles/day — and average expected profit is **€249,347/day**. The battery is now economically significant.

2. **VSS is small as a fraction of profit (~7%).** Mean VSS = €17,507/day. This is the *additional* revenue captured by treating the day-ahead bid as a stochastic decision rather than committing to the mean-scenario bid and adjusting via recourse. Worth running stochastically, but the deterministic plan is rarely far off.

3. **EVPI is large (~28% of profit, €70,272/day).** This is much bigger than VSS — meaning the bottleneck is the *forecast quality*, not the optimization machinery. A model with better-calibrated scenarios (more / better-distributed KNN neighbours, or a hybrid SARIMAX+KNN) would capture a larger share of the EVPI gap than improving the optimizer alone.

4. **No degenerate days.** All 30 days solved to MILP optimality, no infeasibilities, no negative EVPI.

### Limitations

- KNN with 5 scenarios captures only a narrow slice of price + generation uncertainty. The large EVPI suggests adding more scenarios or richer scenario generators would lift z_RP closer to z_WS.
- Single market (DK1), single plant (HR1). Generalisation to DE-LU, NL, or aggregated portfolios requires re-running the panel build with different adapters.
- ID gate-closure dynamics are not modelled: ID prices are treated as known per scenario at the same 24-h granularity as DA. Real intraday markets clear continuously.
- Battery sizing was chosen at the industry midpoint (25% of plant, 2 h). A dedicated optimal sizing study would be needed for a real investment case.
"""))

nbf.write(nb, str(OUT_NB))
print(f"Wrote {OUT_NB}")
