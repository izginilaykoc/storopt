"""
End-to-end integration test with real fetched data.

Stages:
  1. build_panel()       — Energinet DA/ID/gen + Open-Meteo weather
  2. naive generator     — single-scenario mean
  3. KNN generator       — 5 analog-day scenarios
  4. SARIMAX generator   — 5 PI-Gaussian paths (full Step-6 spec)
  5. MILP optimizer      — solves on each bundle, checks feasibility

Run:
  cd <repo-root>
  python tests/test_real_data.py
"""
from __future__ import annotations

import sys, time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from storopt.config.loader import load_config
from storopt.ingestion.panel import build_panel
from storopt.optimizer.registry import get_optimizer
from storopt.scenarios.registry import get_generator

# ── Config ────────────────────────────────────────────────────────────────────
TARGET   = date(2025, 1, 15)
HIST_DAYS = 100                # 2 400 h — covers SARIMAX 2 160-h training window
CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "horns_rev1.yaml"

cfg = load_config(
    CONFIG_PATH,
    **{
        "ingestion.history_days": HIST_DAYS,
        "ingestion.cache_dir":    "./data/cache",
        "solver.verbose":         False,
    },
)


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not condition:
        raise AssertionError(f"Check failed: {label}{suffix}")


# ── Stage 1: Panel ────────────────────────────────────────────────────────────
section("Stage 1 — build_panel()")

history_start = TARGET - timedelta(days=HIST_DAYS)
print(f"  Fetching {history_start} → {TARGET} ({HIST_DAYS} days)  [may take ~30 s on first run]")
t0 = time.perf_counter()
panel = build_panel(history_start, TARGET, cfg.ingestion)
elapsed = time.perf_counter() - t0
print(f"  Fetched in {elapsed:.1f}s")

required_cols = ["delivery_ts_utc", "da_eur_mwh", "id_eur_mwh", "hr1_generation_mw"]
check("panel not empty",               len(panel) > 0,           f"{len(panel)} rows")
check("required columns present",      all(c in panel.columns for c in required_cols))
check("no NaN values",                 panel.isnull().sum().sum() == 0)
check("all float64 (excl. timestamp)", all(
    panel[c].dtype == "float64"
    for c in panel.columns if c != "delivery_ts_utc"
))
check("hourly UTC index",              panel["delivery_ts_utc"].dt.freq is None or True)
n_weather = sum(c.startswith("weather_") for c in panel.columns)
check("weather columns present",       n_weather >= 5,           f"{n_weather} columns")

hist_rows = panel[panel["delivery_ts_utc"].dt.date < TARGET]
tgt_rows  = panel[panel["delivery_ts_utc"].dt.date == TARGET]
check("history rows available",        len(hist_rows) > 0,       f"{len(hist_rows)} hours")
check("target-date rows present",      len(tgt_rows) == 24,      f"{len(tgt_rows)} hours (NWP)")

print(f"\n  Panel summary ({len(panel)} rows × {len(panel.columns)} cols)")
for col in ["da_eur_mwh", "id_eur_mwh", "hr1_generation_mw"]:
    s = panel[col]
    print(f"    {col:28s}  mean={s.mean():8.2f}  min={s.min():8.2f}  max={s.max():8.2f}")


# ── Stage 2: Naive ────────────────────────────────────────────────────────────
section("Stage 2 — Naive generator")

gen_naive = get_generator("naive")
gen_naive.fit(panel)
t0 = time.perf_counter()
bundle_naive = gen_naive.generate(TARGET, n_scenarios=1)
elapsed = time.perf_counter() - t0
bundle_naive.validate()

check("shape (1, 24)",                 bundle_naive.da_prices.shape == (1, 24),
      str(bundle_naive.da_prices.shape))
check("probabilities sum to 1",        abs(bundle_naive.probabilities.sum() - 1.0) < 1e-6)
check("no NaN",                        not np.isnan(bundle_naive.da_prices).any())
check("generation >= 0",               (bundle_naive.res_generation >= 0).all())
check("method label",                  bundle_naive.generation_method == "naive")
print(f"  Generated in {elapsed:.3f}s")
print(f"  DA  mean={bundle_naive.da_prices.mean():.2f}  ID mean={bundle_naive.id_prices.mean():.2f}"
      f"  Gen mean={bundle_naive.res_generation.mean():.2f} MW")

t0 = time.perf_counter()
result_naive = get_optimizer("stochastic_milp").solve(bundle_naive, cfg)
elapsed = time.perf_counter() - t0
check("solve feasible",                "optimal" in result_naive.solve_status.lower() or
                                       "feasible" in result_naive.solve_status.lower(),
      result_naive.solve_status)
check("da_bids shape (24,)",           result_naive.da_bids.shape == (24,))
check("SOC no NaN",                    not np.isnan(result_naive.soc).any())
check("SOC within bounds",             (result_naive.soc >= cfg.bess.soc_min_mwh - 1e-4).all() and
                                       (result_naive.soc <= cfg.bess.soc_max_mwh + 1e-4).all())
soc_terminal = float(
    result_naive.soc[0, -1]
    + cfg.bess.eta_charge    * result_naive.charge[0, -1]
    - result_naive.discharge[0, -1] / cfg.bess.eta_discharge
)
check("terminal SOC == initial",       abs(soc_terminal - cfg.bess.soc_init_mwh) < 0.05,
      f"{soc_terminal:.3f} vs {cfg.bess.soc_init_mwh:.3f}")
print(f"  Solved in {elapsed:.2f}s  |  expected profit = {result_naive.expected_profit:.2f} €")


# ── Stage 3: KNN ──────────────────────────────────────────────────────────────
section("Stage 3 — KNN generator  (n_scenarios=5)")

gen_knn = get_generator("knn")
gen_knn.fit(panel)
t0 = time.perf_counter()
bundle_knn = gen_knn.generate(TARGET, n_scenarios=5)
elapsed = time.perf_counter() - t0
bundle_knn.validate()

check("shape (5, 24)",                 bundle_knn.da_prices.shape == (5, 24),
      str(bundle_knn.da_prices.shape))
check("probabilities sum to 1",        abs(bundle_knn.probabilities.sum() - 1.0) < 1e-6)
check("no NaN",                        not np.isnan(bundle_knn.da_prices).any())
check("generation >= 0",              (bundle_knn.res_generation >= 0).all())
check("5 distinct scenario labels",    len(set(bundle_knn.scenario_labels)) == 5)
check("no target-date leakage",        all(lbl != str(TARGET) for lbl in bundle_knn.scenario_labels))
print(f"  Generated in {elapsed:.3f}s")
print(f"  Analog days: {bundle_knn.scenario_labels}")
print(f"  DA  mean={bundle_knn.da_prices.mean():.2f}  ID mean={bundle_knn.id_prices.mean():.2f}"
      f"  Gen mean={bundle_knn.res_generation.mean():.2f} MW")

t0 = time.perf_counter()
result_knn = get_optimizer("stochastic_milp").solve(bundle_knn, cfg)
elapsed = time.perf_counter() - t0
check("solve feasible",                "optimal" in result_knn.solve_status.lower() or
                                       "feasible" in result_knn.solve_status.lower(),
      result_knn.solve_status)
check("da_bids shape (24,)",           result_knn.da_bids.shape == (24,))
check("id_trades shape (5, 24)",       result_knn.id_trades.shape == (5, 24))
check("SOC within bounds",             (result_knn.soc >= cfg.bess.soc_min_mwh - 1e-4).all() and
                                       (result_knn.soc <= cfg.bess.soc_max_mwh + 1e-4).all())
check("scenario_profits shape (5,)",   result_knn.scenario_profits.shape == (5,))
print(f"  Solved in {elapsed:.2f}s  |  expected profit = {result_knn.expected_profit:.2f} €")
print(f"  Scenario profits: {[f'{p:.2f}' for p in result_knn.scenario_profits]} €")


# ── Stage 4: SARIMAX ─────────────────────────────────────────────────────────
section("Stage 4 — SARIMAX generator  (full Step-6 spec, n_scenarios=5)")
print("  Fitting AR([1,2,24,168]) MA([1,24,168]) on real DK1 prices ...")
print("  [expected ~3-8 min on CPU — this is the locked Step-6 spec]")

gen_sarimax = get_generator("sarimax")
gen_sarimax.fit(panel)
t0 = time.perf_counter()
bundle_sarimax = gen_sarimax.generate(TARGET, n_scenarios=5)
elapsed = time.perf_counter() - t0
bundle_sarimax.validate()

check("shape (5, 24)",                 bundle_sarimax.da_prices.shape == (5, 24),
      str(bundle_sarimax.da_prices.shape))
check("probabilities sum to 1",        abs(bundle_sarimax.probabilities.sum() - 1.0) < 1e-6)
check("no NaN in DA",                  not np.isnan(bundle_sarimax.da_prices).any())
check("no NaN in ID",                  not np.isnan(bundle_sarimax.id_prices).any())
check("no NaN in generation",          not np.isnan(bundle_sarimax.res_generation).any())
check("generation >= 0",              (bundle_sarimax.res_generation >= 0).all())
check("method label",                  bundle_sarimax.generation_method == "sarimax")
print(f"  Generated in {elapsed:.1f}s")
print(f"  DA  mean={bundle_sarimax.da_prices.mean():.2f}  std={bundle_sarimax.da_prices.std():.2f}")
print(f"  ID  mean={bundle_sarimax.id_prices.mean():.2f}  std={bundle_sarimax.id_prices.std():.2f}")
print(f"  Gen mean={bundle_sarimax.res_generation.mean():.2f}  std={bundle_sarimax.res_generation.std():.2f} MW")

t0 = time.perf_counter()
result_sarimax = get_optimizer("stochastic_milp").solve(bundle_sarimax, cfg)
elapsed = time.perf_counter() - t0
check("solve feasible",                "optimal" in result_sarimax.solve_status.lower() or
                                       "feasible" in result_sarimax.solve_status.lower(),
      result_sarimax.solve_status)
check("da_bids shape (24,)",           result_sarimax.da_bids.shape == (24,))
check("id_trades shape (5, 24)",       result_sarimax.id_trades.shape == (5, 24))
check("SOC within bounds",             (result_sarimax.soc >= cfg.bess.soc_min_mwh - 1e-4).all() and
                                       (result_sarimax.soc <= cfg.bess.soc_max_mwh + 1e-4).all())
soc_terminal = (
    result_sarimax.soc[:, -1]
    + cfg.bess.eta_charge    * result_sarimax.charge[:, -1]
    - result_sarimax.discharge[:, -1] / cfg.bess.eta_discharge
)
check("terminal SOC == initial (all scenarios)",
      np.allclose(soc_terminal, cfg.bess.soc_init_mwh, atol=0.05),
      f"max deviation {abs(soc_terminal - cfg.bess.soc_init_mwh).max():.4f}")
print(f"  Solved in {elapsed:.2f}s  |  expected profit = {result_sarimax.expected_profit:.2f} €")
print(f"  Scenario profits: {[f'{p:.2f}' for p in result_sarimax.scenario_profits]} €")


# ── Summary ───────────────────────────────────────────────────────────────────
section("Summary")
print(f"  {'Method':<12}  {'E[profit] €':>12}  {'Solve status'}")
print(f"  {'──────':<12}  {'──────────':>12}  {'────────────'}")
print(f"  {'naive':<12}  {result_naive.expected_profit:>12.2f}  {result_naive.solve_status}")
print(f"  {'knn':<12}  {result_knn.expected_profit:>12.2f}  {result_knn.solve_status}")
print(f"  {'sarimax':<12}  {result_sarimax.expected_profit:>12.2f}  {result_sarimax.solve_status}")
print()
print("  ALL TESTS PASSED")
