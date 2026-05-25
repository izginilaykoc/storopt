"""
Builds report/multi_day_advisor_report.ipynb from the artifacts produced by
run_30_day.py.

Reads daily_summary.parquet plus per-day pickles, then composes a multi-section
Jupyter notebook with:
  - Run-level summary tables across naive / KNN / SARIMAX
  - Per-day profit time series (all three methods overlaid)
  - VSS and EVPI distributions per method
  - Naive-baseline comparison (forecast skill vs true VSS) — both visible
  - Throughput, SOC dynamics, scenario diagnostics for a representative day
  - Cross-method comparison: KNN+EVPI+VSS, SARIMAX+EVPI+VSS, vs naive baseline

The output notebook is fully self-contained — all data is loaded from
report/artifacts/ at render time, so the executed notebook can be inspected
without re-running anything.
"""
from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

# Pin BLAS threads — recompute runs many small MILPs sequentially
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import nbformat
import numpy as np
import pandas as pd
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "report" / "artifacts"
OUT_NB = REPO / "report" / "multi_day_advisor_report.ipynb"
METHODS = ("naive", "knn", "sarimax")


def recompute_summary() -> None:
    """Re-derive z_RP / z_EEV / z_WS / VSS / EVPI from saved bundles + results.

    The original metrics.json files were written by workers using whatever
    version of metrics.py was imported at process start. Calling
    compute_vss_evpi here picks up the latest code (with the EEV q_id-bound
    fix and the WS bound-relaxation fix), so the final report reflects the
    corrected metrics regardless of when each worker started.
    """
    from storopt.config.loader import load_config
    from storopt.evaluation.metrics import compute_vss_evpi

    cfg = load_config(str(REPO / "configs" / "horns_rev1_40mw.yaml"))
    rows = []
    for day_dir in sorted(ARTIFACTS.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.startswith("2025-"):
            continue
        for method in METHODS:
            mp = day_dir / f"{method}_metrics.json"
            bp = day_dir / f"{method}_bundle.pkl"
            rp = day_dir / f"{method}_result.pkl"
            if not mp.exists():
                continue
            base = json.load(open(mp))
            if bp.exists() and rp.exists():
                try:
                    bundle = pickle.load(open(bp, "rb"))
                    result = pickle.load(open(rp, "rb"))
                    ve = compute_vss_evpi(bundle, cfg, stochastic_result=result, compute_evpi=True)
                    fnan = lambda v: float(v) if not (isinstance(v, float) and np.isnan(v)) else None
                    base["z_rp_eur"]  = fnan(ve["z_rp"])
                    base["z_eev_eur"] = fnan(ve["z_eev"])
                    base["z_ws_eur"]  = fnan(ve["z_ws"])
                    base["vss_eur"]   = fnan(ve["vss_eur"])
                    base["evpi_eur"]  = fnan(ve["evpi_eur"])
                except Exception as e:
                    base["recompute_error"] = str(e)
            rows.append(base)
    df = pd.DataFrame(rows)
    df.to_parquet(ARTIFACTS / "daily_summary.parquet", index=False)
    df.to_csv(ARTIFACTS / "daily_summary.csv", index=False)
    print(f"Recomputed VSS/EVPI for {len(df)} rows → daily_summary.parquet (overwritten)")


def md(text: str):
    return new_markdown_cell(text)


def code(src: str):
    return new_code_cell(src)


def build() -> None:
    recompute_summary()
    cells: list = []

    cells.append(md(
        "# storopt — 30-Day Rolling Advisor Report\n"
        "## Multi-Day Backtest with Real EEV / VSS / EVPI\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| **Target window** | 2025-01-15 → 2025-02-13 (30 trading days) |\n"
        "| **Plant** | Horns Rev 1 — 160 MW offshore wind, DK1 |\n"
        "| **Battery** | **40 MW / 80 MWh** (25 % of plant, 2-hour duration) |\n"
        "| **Scenario methods** | naive (1), KNN (5), SARIMAX (5) |\n"
        "| **Metrics** | RP profit, real VSS (z_RP − z_EEV), EVPI (z_WS − z_RP), throughput |\n"
        "| **History window** | 100 days, rolling |\n"
        "| **Solver** | HiGHS, MIP gap 0.1 % |\n\n"
        "This notebook reads pre-computed artifacts produced by `run_30_day.py`. "
        "It does not re-fit SARIMAX or re-solve any MILP — everything is rendered "
        "from `report/artifacts/`.\n\n"
        "---\n\n"
        "## Table of Contents\n"
        "1. [Setup & Load Artifacts](#setup)\n"
        "2. [Daily Summary Table](#summary)\n"
        "3. [Profit Time Series](#profit-ts)\n"
        "4. [True VSS vs. Naive-Baseline Skill](#vss-comparison)\n"
        "5. [EVPI Distribution](#evpi)\n"
        "6. [Throughput & Cycling Behaviour](#throughput)\n"
        "7. [Representative Day — Dispatch Profile](#rep-day)\n"
        "8. [Run-Level Statistics](#stats)\n"
    ))

    cells.append(md('<a id="setup"></a>\n## 1. Setup & Load Artifacts'))

    cells.append(code("""\
import json, pickle
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

ARTIFACTS = Path('artifacts') if Path('artifacts').exists() else Path('.') / 'artifacts'

summary = pd.read_parquet(ARTIFACTS / 'daily_summary.parquet')
summary['target_date'] = pd.to_datetime(summary['target_date'])
print(f"Loaded {len(summary)} rows  ({summary['target_date'].nunique()} unique dates, "
      f"{summary['method'].nunique()} methods: {sorted(summary['method'].unique())})")

COLORS = {'naive': '#4878CF', 'knn': '#6ACC65', 'sarimax': '#D65F5F'}
plt.rcParams.update({
    'figure.dpi': 120, 'font.size': 11, 'axes.grid': True,
    'grid.alpha': 0.3, 'axes.spines.top': False, 'axes.spines.right': False,
    'figure.facecolor': 'white',
})
"""))

    cells.append(md('<a id="summary"></a>\n## 2. Daily Summary — Average Across 30 Days\n\n'
                    'Per-method aggregate: expected profit, VSS, EVPI, throughput.'))

    cells.append(code("""\
agg = (
    summary
    .groupby('method')
    .agg(
        n_days=('target_date', 'count'),
        mean_profit_eur=('expected_profit_eur', 'mean'),
        total_profit_eur=('expected_profit_eur', 'sum'),
        mean_vss_eur=('vss_eur', 'mean'),
        mean_evpi_eur=('evpi_eur', 'mean'),
        median_vss_eur=('vss_eur', 'median'),
        median_evpi_eur=('evpi_eur', 'median'),
        mean_throughput_mwh=('throughput_mwh', 'mean'),
        n_optimal=('solve_status', lambda s: (s == 'optimal').sum()),
    )
    .reindex(['naive', 'knn', 'sarimax'])
)
agg.round(2)
"""))

    cells.append(md('### KNN vs Naive: Forecast Skill (different from VSS)\n\n'
                    'This is the comparison the original advisor notebook labelled as a "VSS proxy". '
                    'It is **not** VSS — it measures how much a better scenario-generation method '
                    '(KNN) earns over a climatological baseline (naive). Real VSS is shown below.'))

    cells.append(code("""\
piv = summary.pivot(index='target_date', columns='method', values='expected_profit_eur')
piv['knn_minus_naive']     = piv['knn']     - piv['naive']
piv['sarimax_minus_naive'] = piv['sarimax'] - piv['naive']
piv['sarimax_minus_knn']   = piv['sarimax'] - piv['knn']

skill = pd.DataFrame({
    'metric': ['KNN − naive', 'SARIMAX − naive', 'SARIMAX − KNN'],
    'mean_eur_per_day':   [piv['knn_minus_naive'].mean(), piv['sarimax_minus_naive'].mean(), piv['sarimax_minus_knn'].mean()],
    'median_eur_per_day': [piv['knn_minus_naive'].median(), piv['sarimax_minus_naive'].median(), piv['sarimax_minus_knn'].median()],
    'total_30d_eur':      [piv['knn_minus_naive'].sum(), piv['sarimax_minus_naive'].sum(), piv['sarimax_minus_knn'].sum()],
}).round(2)
skill
"""))

    cells.append(md('<a id="profit-ts"></a>\n## 3. Profit Time Series — All Methods Overlaid'))

    cells.append(code("""\
fig, ax = plt.subplots(figsize=(13, 5), tight_layout=True)
for m in ['naive', 'knn', 'sarimax']:
    sub = summary[summary['method'] == m].sort_values('target_date')
    ax.plot(sub['target_date'], sub['expected_profit_eur'], 'o-', color=COLORS[m], label=m.upper(), lw=1.5, ms=4)
ax.set_ylabel('Expected daily profit (€)')
ax.set_xlabel('Target date')
ax.set_title('Stochastic MILP expected daily profit — 40 MW / 80 MWh BESS')
ax.legend()
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'€{x:,.0f}'))
fig.autofmt_xdate()
fig
"""))

    cells.append(md('<a id="vss-comparison"></a>\n## 4. True VSS vs Naive-Baseline Skill\n\n'
                    '- **True VSS** = z_RP − z_EEV  (value of stochastic structure given the bundle)\n'
                    '- **Naive-baseline skill** = z_RP(method) − z_RP(naive)  (value of switching to that scenario method)\n\n'
                    'They answer different questions. The naive comparison includes *both* '
                    'forecasting skill and stochastic-structure value; true VSS isolates the latter.'))

    cells.append(code("""\
fig, axes = plt.subplots(1, 2, figsize=(14, 5), tight_layout=True)

# Left: True VSS per method, daily
ax = axes[0]
for m in ['knn', 'sarimax']:
    sub = summary[summary['method'] == m].sort_values('target_date')
    ax.plot(sub['target_date'], sub['vss_eur'], 'o-', color=COLORS[m], label=f'{m.upper()} VSS', lw=1.3, ms=3.5)
ax.axhline(0, color='k', lw=0.5)
ax.set_ylabel('True VSS = z_RP − z_EEV (€)')
ax.set_title('True VSS per day (KNN, SARIMAX). Naive S=1 has no VSS.')
ax.legend()
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'€{x:,.0f}'))

# Right: Naive-baseline skill
ax = axes[1]
ax.plot(piv.index, piv['knn_minus_naive'], 'o-', color=COLORS['knn'], label='KNN − naive', lw=1.3, ms=3.5)
ax.plot(piv.index, piv['sarimax_minus_naive'], 'o-', color=COLORS['sarimax'], label='SARIMAX − naive', lw=1.3, ms=3.5)
ax.axhline(0, color='k', lw=0.5)
ax.set_ylabel('z_RP(method) − z_RP(naive)  (€)')
ax.set_title('Naive-baseline skill — forecast quality vs climatology')
ax.legend()
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, p: f'€{x:,.0f}'))
for a in axes:
    a.tick_params(axis='x', rotation=30)
fig
"""))

    cells.append(code("""\
# Distributions
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), tight_layout=True)
for ax, col, title in [
    (axes[0], 'vss_eur', 'True VSS distribution (per day)'),
    (axes[1], 'evpi_eur', 'EVPI distribution (per day)'),
]:
    for m in ['knn', 'sarimax']:
        vals = summary[summary['method'] == m][col].dropna()
        if vals.empty:
            continue
        ax.hist(vals, bins=15, alpha=0.55, color=COLORS[m], label=f'{m.upper()} (n={len(vals)})', edgecolor='white')
    ax.axvline(0, color='k', lw=0.5)
    ax.set_xlabel('€')
    ax.set_ylabel('Days')
    ax.set_title(title)
    ax.legend()
fig
"""))

    cells.append(md('<a id="evpi"></a>\n## 5. EVPI — Value of Perfect Foresight'))

    cells.append(code("""\
evpi_table = (
    summary[summary['method'].isin(['knn', 'sarimax'])]
    .groupby('method')['evpi_eur']
    .agg(['mean','median','std','min','max'])
    .round(2)
)
display(evpi_table)
print(f"\\nEVPI as % of mean RP profit:")
for m in ['knn', 'sarimax']:
    sub = summary[summary['method'] == m]
    pct = 100 * sub['evpi_eur'].mean() / sub['expected_profit_eur'].mean()
    print(f"  {m.upper()}: {pct:.2f} %")
"""))

    cells.append(md('<a id="throughput"></a>\n## 6. Throughput & Cycling'))

    cells.append(code("""\
fig, ax = plt.subplots(figsize=(13, 4.5), tight_layout=True)
for m in ['naive', 'knn', 'sarimax']:
    sub = summary[summary['method'] == m].sort_values('target_date')
    ax.plot(sub['target_date'], sub['throughput_mwh'], 'o-', color=COLORS[m], label=m.upper(), lw=1.3, ms=4)
ax.axhline(80, color='gray', ls='--', alpha=0.6, label='Full-cycle = 80 MWh')
ax.axhline(160, color='gray', ls=':',  alpha=0.6, label='2 cycles = 160 MWh')
ax.set_ylabel('Daily throughput (MWh)')
ax.set_title('BESS cycling intensity per day — sum of |p_ch| + |p_dis|')
ax.legend(loc='upper right')
fig.autofmt_xdate()
fig
"""))

    cells.append(md('<a id="rep-day"></a>\n## 7. Representative Day Dispatch — 2025-01-15'))

    cells.append(code("""\
# Load 2025-01-15 results for visual comparison
day = '2025-01-15'
day_dir = ARTIFACTS / day
results = {}
for m in ['naive', 'knn', 'sarimax']:
    rp = day_dir / f'{m}_result.pkl'
    if rp.exists():
        with open(rp, 'rb') as f:
            results[m] = pickle.load(f)

hours = np.arange(24)
fig, axes = plt.subplots(2, 1, figsize=(13, 7), tight_layout=True, sharex=True)
ax = axes[0]
for m, r in results.items():
    ax.step(hours, r.da_bids, where='mid', color=COLORS[m], label=f'{m.upper()} q_da', lw=1.5)
ax.set_ylabel('q_da (MW)')
ax.set_title(f'Day-ahead bids — {day}')
ax.legend()

ax = axes[1]
for m, r in results.items():
    # Use first scenario's SOC and net trade
    net = r.da_bids + r.id_trades[0]
    ax.step(hours, net, where='mid', color=COLORS[m], label=f'{m.upper()} net (s0)', lw=1.3, alpha=0.8)
ax.set_ylabel('Net position (MW)')
ax.set_xlabel('Hour of day (UTC)')
ax.set_title('Net market position (q_da + q_id[0])')
ax.legend()
fig
"""))

    cells.append(md('<a id="stats"></a>\n## 8. Run-Level Statistics\n\nFull per-day breakdown:'))

    cells.append(code("""\
pretty = summary[['target_date', 'method', 'expected_profit_eur', 'vss_eur', 'evpi_eur', 'throughput_mwh', 'solve_status']].copy()
pretty['target_date'] = pretty['target_date'].dt.date
pretty.round(2)
"""))

    nb = new_notebook(cells=cells)
    nb.metadata = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    }
    with open(OUT_NB, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    print(f"Wrote {OUT_NB}")


if __name__ == "__main__":
    build()
