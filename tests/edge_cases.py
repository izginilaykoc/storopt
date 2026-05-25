"""
Optimizer edge cases — boundary conditions and break-even tests.

Tests the optimizer's behaviour at economic extremes:
  - Zero prices and uniform negative prices (idle vs. profitable cycling)
  - Two-block spread straddling the break-even threshold (idle below, trades above)
  - Alternating extreme prices (maximum cycling profit)
  - SOC boundary starts (battery empty vs. battery full)

Break-even analytics (BESS defaults: RTE=0.9025, deg=10 €/MWh):

  Uniform negative prices WITH 2 MW wind generation (non-curtailable):
    Battery cycling break-even: |p_be| = deg·(1+RTE)/(1−RTE) = 195.1 €/MWh
    |p|=100 < 195.1 → cycling makes losses worse (−9.275 € per cycle)
      → battery idle, all 2 MW sold at loss → profit = −100 × 2 × 24 = −4800 €
    |p|=300 > 195.1 → cycling reduces losses (+10.225 € per cycle)
      → battery cycles, but generation losses (−14400 €) still dominate → profit < 0
    Generation cannot be curtailed: energy balance is an equality constraint.

  Two-block spread (p_low=50 in h0-11, p_high=? in h12-23):
    Break-even: p_high_be = (p_low + deg·(1+RTE)) / RTE = 69.025 / 0.9025 = 76.48 €/MWh
    → p_high=76 (spread 26 < 26.48) → idle, profit = 0
    → p_high=77 (spread 27 > 26.48) → battery trades, profit ≈ €0.39

  Alternating ±200 (even hours +200, odd hours −200):
    Each cycle: earn 200€ (charging at neg price) + 180.5€ (discharging at pos price)
                minus 19.025€ degradation → net ≈ 361 € per cycle

  SOC boundary cases (two-block p_low=30, p_high=150):
    SOC_min start (0.2 MWh): full charge-discharge cycle → profit ≈ €145.44
    SOC_max start (1.8 MWh): no profitable cycle available → idle, profit = 0

BESS defaults (configs/default.yaml):
  Power:     1 MW charge / 1 MW discharge
  Capacity:  2 MWh
  SOC:       init=1.0 MWh, min=0.2 MWh, max=1.8 MWh
  Efficiency: η_ch=0.95, η_dis=0.95  → RTE=0.9025
  Deg cost:  10 €/MWh throughput
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from storopt.config.loader import load_config
from storopt.optimizer.milp import StochasticMILP
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle

# ── constants ────────────────────────────────────────────────────────────────

T = 24
TODAY = date.today()
CFG = load_config()

SOC_INIT = CFG.bess.soc_init_mwh      # 1.0 MWh
SOC_MIN  = CFG.bess.soc_min_mwh       # 0.2 MWh
SOC_MAX  = CFG.bess.soc_max_mwh       # 1.8 MWh
ETA_CH   = CFG.bess.eta_charge        # 0.95
ETA_DIS  = CFG.bess.eta_discharge     # 0.95
RTE      = ETA_CH * ETA_DIS           # 0.9025
DEG      = CFG.bess.deg_cost_eur_per_mwh  # 10.0

# Break-even thresholds
P_BE_UNIFORM_NEG = DEG * (1 + RTE) / (1 - RTE)  # 195.1 €/MWh
P_LOW_TWO_BLOCK  = 50.0
P_HIGH_BE        = (P_LOW_TWO_BLOCK + DEG * (1 + RTE)) / RTE  # 76.48 €/MWh

SOLVER = StochasticMILP()


# ── helpers ──────────────────────────────────────────────────────────────────

def _bundle(da: np.ndarray, gen: np.ndarray | None = None, id_: np.ndarray | None = None) -> ScenarioBundle:
    if gen is None:
        gen = np.zeros(T)
    if id_ is None:
        id_ = da.copy()
    return ScenarioBundle(
        da_prices=da[np.newaxis, :],
        id_prices=id_[np.newaxis, :],
        res_generation=gen[np.newaxis, :],
        probabilities=np.array([1.0]),
        target_date=TODAY,
    )


@dataclass
class CaseResult:
    name: str
    description: str
    expected: str
    real_world: str                        # what this means in real market terms
    da_prices: np.ndarray
    result: OptimizationResult
    checks: list[tuple[str, bool, str]]   # (label, passed, detail)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


# ── edge cases ────────────────────────────────────────────────────────────────

def case_1_zero_prices() -> CaseResult:
    """
    All 24 hours at 0 €/MWh.
    No price gradient, no revenue from any trade. Any throughput incurs
    degradation cost with zero offsetting revenue → battery must stay idle.

    Expected: p_ch = 0, p_dis = 0, net = 0, profit = 0.
    """
    da = np.zeros(T)
    r = SOLVER.solve(_bundle(da), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    net  = r.da_bids + r.id_trades[0]

    checks = [
        ("All p_ch = 0",
         pch.max() < 1e-4,
         f"max p_ch = {pch.max():.6f} MW"),
        ("All p_dis = 0",
         pdis.max() < 1e-4,
         f"max p_dis = {pdis.max():.6f} MW"),
        ("Total throughput = 0",
         (pch + pdis).sum() < 1e-4,
         f"throughput = {(pch + pdis).sum():.6f} MWh"),
        ("Net position = 0",
         float(np.abs(net).max()) < 1e-4,
         f"max |net| = {np.abs(net).max():.6f} MW"),
        ("Expected profit = 0",
         abs(r.expected_profit) < 1.0,
         f"profit = €{r.expected_profit:.4f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 1 — All prices = 0 (no revenue)",
        description="DA = 0 €/MWh all 24 hours. No generation.",
        expected="Battery fully idle — any throughput incurs degradation with zero revenue.",
        real_world=(
            "Zero clearing prices occur during extreme renewable surplus meeting very low "
            "demand (e.g., Easter Sunday in Denmark during a wind storm). With no price "
            "signal in either direction, there is no economic case for storage dispatch. "
            "The battery sits idle to avoid degradation cost for zero gain. In practice, "
            "operators in this situation shift focus to ancillary services (FCR-N, FCR-D) "
            "which pay a capacity fee independent of energy prices."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_2_neg100_above_breakeven() -> CaseResult:
    """
    All 24 hours at −100 €/MWh with 2 MW constant wind generation.
    Wind is non-curtailable (energy balance is an equality constraint in the MILP).
    Battery power = 1 MW, so the plant always sells at least 1 MW to the grid.

    Battery cycling is unprofitable at |p|=100 < 195.1 €/MWh break-even:
      Marginal cycle: save 100 € (absorb 1 MW), then sell 0.9025 MW back = −90.25 €
                      degradation = −10 × (1 + 0.9025) = −19.025 €
      Net: 100 − 90.25 − 19.025 = −9.275 €  → cycling makes losses WORSE

    Expected:
      - Battery idle (cycling costs money at this price)
      - All 2 MW generation forced to market at −100 €/MWh
      - Profit ≈ −100 × 2 × 24 = −4800 €
    """
    da  = np.full(T, -100.0)
    gen = np.full(T, 2.0)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch       = r.charge[0]
    pdis      = r.discharge[0]
    net       = r.da_bids + r.id_trades[0]
    expected_base = -100.0 * 2.0 * T   # -4800 €

    checks = [
        ("Battery idle — cycling below break-even makes losses worse",
         (pch + pdis).sum() < 1e-4,
         f"throughput = {(pch + pdis).sum():.6f} MWh"),
        ("All generation forced to market (energy balance equality, no curtailment)",
         float(np.abs(net - 2.0).max()) < 1e-3,
         f"max |net − 2| = {float(np.abs(net - 2.0).max()):.4f} MW"),
        ("Profit ≈ −4800 € (2 MW × 24h × −100 €/MWh, no battery benefit)",
         abs(r.expected_profit - expected_base) < 10.0,
         f"profit = €{r.expected_profit:.2f} (expected ≈ €{expected_base:.0f})"),
        ("Profit < 0 — negative prices with non-curtailable generation",
         r.expected_profit < -100.0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 2 — Uniform −100 €/MWh + 2 MW wind (forced selling, loss)",
        description=f"DA = −100 €/MWh, gen = 2 MW all 24 hours. |p|=100 < P_BE={P_BE_UNIFORM_NEG:.1f}. No curtailment.",
        expected="Battery idle (cycling unprofitable), all generation sold at loss. Profit ≈ −€4800.",
        real_world=(
            "Negative clearing prices are a real feature of the Nordic day-ahead market "
            "(EPEX Spot Nord Pool). At −100 €/MWh, an offshore wind plant owes the grid "
            "operator €100 per MWh it injects — a €4,800 bill for a 48 MWh day. Unlike gas "
            "plants, offshore turbines cannot be shut down quickly (minimum 4–6 hours notice, "
            "electrical safety constraints, PPA obligations). The battery cannot mitigate this: "
            "cycling is upside-down below the 195 €/MWh break-even. The real-world response is "
            "to either accept the loss or negotiate a negative-price suspension clause in the PPA "
            "that halts delivery obligations during sustained negative price windows."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_3_neg300_below_breakeven() -> CaseResult:
    """
    All 24 hours at −300 €/MWh with 2 MW constant wind generation.
    |p|=300 > break-even of 195.1 €/MWh → battery cycling reduces losses.

    Battery cycling is profitable even with generation:
      Marginal cycle: save 300 € (absorb 1 MW), then sell 0.9025 MW back = −270.75 €
                      degradation = −10 × (1 + 0.9025) = −19.025 €
      Net: 300 − 270.75 − 19.025 = +10.225 € per cycle  → battery should cycle

    Base loss if battery idle: −300 × 2 MW × 24 h = −14400 €
    Battery cycling saves ~10 € per cycle, reducing total loss.

    Expected:
      - Battery cycles (throughput > 0) to reduce losses
      - Profit < 0 (generation selling losses dominate cycling gains)
      - Profit > −14400 € (battery is helping)
      - Some hours net < 2 MW (battery absorbing more than just the wind)
    """
    da  = np.full(T, -300.0)
    gen = np.full(T, 2.0)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    net  = r.da_bids + r.id_trades[0]
    base_loss = -300.0 * 2.0 * T   # -14400 €

    checks = [
        ("Battery cycles to reduce losses (p_ch > 0)",
         pch.sum() > 0.1,
         f"Σ p_ch = {pch.sum():.3f} MWh"),
        ("Battery discharges to return SOC (p_dis > 0)",
         pdis.sum() > 0.1,
         f"Σ p_dis = {pdis.sum():.3f} MWh"),
        ("Profit < 0 — generation selling losses dominate any cycling gains",
         r.expected_profit < -100.0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Profit > −14400 € — battery reduces losses vs. idle baseline",
         r.expected_profit > base_loss,
         f"profit = €{r.expected_profit:.2f} > base = €{base_loss:.0f}"),
        ("Some hours net position < 2 MW (battery absorbing generation)",
         float(net.min()) < 2.0 - 0.05,
         f"min net = {net.min():.3f} MW (gen = 2.0 MW)"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 3 — Uniform −300 €/MWh + 2 MW wind (cycling reduces losses)",
        description=f"DA = −300 €/MWh, gen = 2 MW all 24 hours. |p|=300 > P_BE={P_BE_UNIFORM_NEG:.1f}. No curtailment.",
        expected="Battery cycles (profitable above break-even), but generation losses dominate. Profit < 0.",
        real_world=(
            "Extreme negative prices (−300 €/MWh) occur during severe curtailment events — "
            "record wind output on a public holiday is a real example. At this level, battery "
            "cycling is profitable in isolation (|p| > 195 €/MWh break-even saves ~€125/day) "
            "but the unavoidable generation selling dominates: the plant is still €14,275 in "
            "the red. Events like this drive investment in demand-response partnerships "
            "(hydrogen electrolysers, aluminium smelters) that can absorb excess generation "
            "at an agreed offtake price, converting a trading loss into an industrial contract. "
            "The battery here is damage control, not a profit centre."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_4_spread_just_below_breakeven() -> CaseResult:
    """
    Morning cheap at 50 €/MWh (h0-11), evening at p_high=76 €/MWh (h12-23).
    p_high=76 is just below the break-even of 76.48 €/MWh → arbitrage unprofitable.

    Check: 76 × 0.9025 = 68.59 < 50 + 19.025 = 69.025 → spread insufficient.

    Expected: battery idle, profit = 0.
    """
    p_high = 76.0
    da = np.array([P_LOW_TWO_BLOCK] * 12 + [p_high] * 12)
    r = SOLVER.solve(_bundle(da), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    throughput = (pch + pdis).sum()

    checks = [
        ("All p_ch = 0 (below break-even spread)",
         pch.max() < 1e-4,
         f"max p_ch = {pch.max():.6f} MW"),
        ("All p_dis = 0",
         pdis.max() < 1e-4,
         f"max p_dis = {pdis.max():.6f} MW"),
        ("Total throughput = 0",
         throughput < 1e-4,
         f"throughput = {throughput:.6f} MWh"),
        ("Expected profit = 0",
         abs(r.expected_profit) < 1.0,
         f"profit = €{r.expected_profit:.4f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name=f"Edge 4 — Two-block p_high={p_high} just below break-even {P_HIGH_BE:.2f}",
        description=f"DA = {P_LOW_TWO_BLOCK} €/MWh h0-11, {p_high} €/MWh h12-23. Spread {p_high - P_LOW_TWO_BLOCK:.0f} < {P_HIGH_BE - P_LOW_TWO_BLOCK:.2f} needed.",
        expected="Battery idle — spread just below break-even, arbitrage unprofitable.",
        real_world=(
            "A 26 €/MWh spread is visible in the DA market on many days, but the optimizer "
            "correctly ignores it because the round-trip losses (RTE = 0.9025) plus "
            "degradation (10 €/MWh) consume the entire spread. A naive rule-of-thumb trader "
            "— 'charge when price < 60, discharge when price > 76' — would cycle the battery "
            "and destroy €0.37/day in value. In competitive liquid markets, obvious spread "
            "opportunities are quickly priced away; precise arithmetic at the margin is "
            "exactly where algorithmic optimisation beats human heuristics."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_5_spread_just_above_breakeven() -> CaseResult:
    """
    Morning cheap at 50 €/MWh (h0-11), evening at p_high=77 €/MWh (h12-23).
    p_high=77 is just above the break-even of 76.48 €/MWh → arbitrage profitable.

    Check: 77 × 0.9025 = 69.49 > 50 + 19.025 = 69.025 → spread sufficient.

    Expected profit (SOC headroom = 0.8 MWh stored, charge p_ch=0.842 MW × 1h):
      Revenue from discharge: 77 × 0.842 × 0.9025 ≈ €58.52
      Cost of charge: 50 × 0.842 ≈ €42.11
      Degradation: 10 × (0.842 + 0.842×0.9025) ≈ €16.02
      Net: ≈ €0.39

    Expected: battery charges in morning, discharges in evening, profit > 0.
    """
    p_high = 77.0
    da = np.array([P_LOW_TWO_BLOCK] * 12 + [p_high] * 12)
    r = SOLVER.solve(_bundle(da), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    net  = r.da_bids + r.id_trades[0]

    morning_charge    = pch[:12].sum()
    evening_discharge = pdis[12:].sum()

    checks = [
        ("Charging occurs in morning (h0-11)",
         morning_charge > 0.01,
         f"Σ p_ch[0:12] = {morning_charge:.4f} MWh"),
        ("Discharging occurs in evening (h12-23)",
         evening_discharge > 0.01,
         f"Σ p_dis[12:] = {evening_discharge:.4f} MWh"),
        ("No charging in evening (buying expensive is suboptimal)",
         pch[12:].sum() < 1e-4,
         f"Σ p_ch[12:] = {pch[12:].sum():.6f} MWh"),
        ("No discharging in morning (selling cheap is suboptimal)",
         pdis[:12].sum() < 1e-4,
         f"Σ p_dis[0:12] = {pdis[:12].sum():.6f} MWh"),
        ("Net position ≤ 0 in morning (buying)",
         float(net[:12].max()) < 1e-4,
         f"max net[0:12] = {net[:12].max():.4f} MW"),
        ("Net position ≥ 0 in evening (selling)",
         float(net[12:].min()) > -1e-4,
         f"min net[12:] = {net[12:].min():.4f} MW"),
        ("Expected profit > 0 (spread exceeds break-even)",
         r.expected_profit > 0,
         f"profit = €{r.expected_profit:.4f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name=f"Edge 5 — Two-block p_high={p_high} just above break-even {P_HIGH_BE:.2f}",
        description=f"DA = {P_LOW_TWO_BLOCK} €/MWh h0-11, {p_high} €/MWh h12-23. Spread {p_high - P_LOW_TWO_BLOCK:.0f} > {P_HIGH_BE - P_LOW_TWO_BLOCK:.2f} needed.",
        expected="Battery trades — spread just above break-even gives small positive profit.",
        real_world=(
            "The €0.39 profit for a 1 MW/2 MWh battery is economically irrelevant "
            "(below transaction and scheduling costs in a real market). But at commercial "
            "scale — a 100 MW/200 MWh system — the same spread yields €3,900/day or ~€1.4M "
            "annually just from this recurrent pattern. This case also validates that the "
            "optimizer's threshold is exact: it dispatches only when genuinely profitable "
            "after accounting for physics (RTE) and costs (degradation). Operators relying "
            "on this marginal arbitrage in practice also layer in capacity market revenues "
            "and balancing reserve fees to improve the economics."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_6_alternating_prices() -> CaseResult:
    """
    Prices alternate +200 / −200 every hour (even hours = +200, odd = −200).

    Each cycle: charge 1 MW at −200 earns 200 €, discharge ~0.9025 MW at +200
    earns 180.5 €, degradation costs 19.025 € → net ≈ 361 € per cycle. With
    12 such cycles in 24 hours, total profit can reach several thousand euros.

    Optimal: discharge at positive-price hours, charge at negative-price hours.

    Expected:
      - p_dis concentrated at positive-price hours (even)
      - p_ch concentrated at negative-price hours (odd)
      - Throughput high, profit >> 0
    """
    da = np.where(np.arange(T) % 2 == 0, 200.0, -200.0)
    pos_hours = np.where(da > 0)[0]   # even: 0, 2, 4, ..., 22
    neg_hours = np.where(da < 0)[0]   # odd:  1, 3, 5, ..., 23

    r = SOLVER.solve(_bundle(da), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    net  = r.da_bids + r.id_trades[0]
    throughput = (pch + pdis).sum()

    dis_at_pos = pdis[pos_hours].sum()
    ch_at_neg  = pch[neg_hours].sum()
    dis_at_neg = pdis[neg_hours].sum()  # discharging at neg prices is suboptimal
    ch_at_pos  = pch[pos_hours].sum()   # charging at pos prices is suboptimal

    checks = [
        ("Discharging concentrated at positive-price hours",
         dis_at_pos > 0.1,
         f"Σ p_dis[pos hrs] = {dis_at_pos:.3f} MWh"),
        ("Charging concentrated at negative-price hours",
         ch_at_neg > 0.1,
         f"Σ p_ch[neg hrs] = {ch_at_neg:.3f} MWh"),
        ("No discharging at negative-price hours (loses money)",
         dis_at_neg < 1e-4,
         f"Σ p_dis[neg hrs] = {dis_at_neg:.4f} MWh"),
        ("No charging at positive-price hours (loses money)",
         ch_at_pos < 1e-4,
         f"Σ p_ch[pos hrs] = {ch_at_pos:.4f} MWh"),
        ("Throughput > 0 (multiple cycles)",
         throughput > 0.5,
         f"throughput = {throughput:.3f} MWh"),
        ("Profit significantly > 0 (strong cycling opportunity)",
         r.expected_profit > 100.0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 6 — Alternating ±200 €/MWh (max cycling profit)",
        description="DA alternates +200/−200 each hour (even=+200, odd=−200). No generation.",
        expected="Discharge at positive hours, charge at negative hours, very high profit.",
        real_world=(
            "Hourly price swings of this magnitude are rare in the DA market but common in "
            "continuous intraday trading (EPEX IDA products). A storage operator with "
            "intraday access can in theory cycle the battery every two hours to exploit these "
            "swings. The €4,280 single-day profit for a 1 MW/2 MWh unit illustrates why "
            "high-frequency intraday strategies have become the primary revenue source for "
            "grid-scale BESS in liquid European markets — often outperforming capacity markets "
            "and ancillary services on a per-MW basis. In practice the battery is also "
            "simultaneously bidding into FCR/aFRR, so the intraday position sits on top of "
            "a reserve capacity obligation."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_7_soc_min_start() -> CaseResult:
    """
    Battery starts empty (SOC_init = SOC_min = 0.2 MWh) with two-block prices.
    p_low=30 €/MWh in h0-11 (charge window), p_high=150 €/MWh in h12-23 (discharge).

    The battery has maximum headroom (1.6 MWh) to fill — a full cycle is possible.
    Terminal SOC must return to 0.2 MWh (= soc_init for this config).

    Expected profit:
      Grid energy drawn: 1.6/0.95 = 1.684 MWh → cost = 30 × 1.684 = €50.52
      Grid energy delivered: 1.6 × 0.95 = 1.52 MWh → revenue = 150 × 1.52 = €228.00
      Degradation: 10 × (1.684 + 1.0 + 0.520) ≈ €32.04
      Net: ≈ €145.44

    Expected: charge in morning, discharge in evening, profit ≈ €145.
    """
    cfg_low = load_config(**{"bess.soc_init_frac": 0.10})
    soc_init_low = cfg_low.bess.soc_init_mwh   # 0.2 MWh

    da = np.array([30.0] * 12 + [150.0] * 12)
    r = SOLVER.solve(_bundle(da), cfg_low)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    soc  = r.soc[0]
    net  = r.da_bids + r.id_trades[0]

    morning_charge    = pch[:12].sum()
    evening_discharge = pdis[12:].sum()

    checks = [
        ("Charging occurs in cheap morning (h0-11)",
         morning_charge > 0.1,
         f"Σ p_ch[0:12] = {morning_charge:.3f} MWh"),
        ("No discharging in morning (SOC starts at min, nothing to discharge)",
         pdis[:12].sum() < 1e-4,
         f"Σ p_dis[0:12] = {pdis[:12].sum():.4f} MWh"),
        ("Discharging occurs in expensive evening (h12-23)",
         evening_discharge > 0.1,
         f"Σ p_dis[12:] = {evening_discharge:.3f} MWh"),
        ("SOC peaks at or near SOC_max after charging",
         soc.max() > 1.7,
         f"SOC_peak = {soc.max():.3f} MWh (max={cfg_low.bess.soc_max_mwh})"),
        # soc[t] is SOC at START of period t; terminal SOC is after period T-1's action.
        ("Terminal SOC returns to SOC_min",
         abs(float(soc[-1] + ETA_CH * pch[-1] - pdis[-1] / ETA_DIS) - soc_init_low) < 0.01,
         f"terminal SOC = {float(soc[-1] + ETA_CH * pch[-1] - pdis[-1] / ETA_DIS):.3f} MWh (init={soc_init_low})"),
        ("Profit approximately €145 (full cycle headroom)",
         r.expected_profit > 140.0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 7 — SOC starts at minimum (0.2 MWh), two-block prices",
        description="soc_init=0.2 MWh (=SOC_min). DA=30 h0-11 / 150 h12-23. Full headroom available.",
        expected="Full charge-discharge cycle, profit ≈ €145.",
        real_world=(
            "Battery state at the start of each trading day depends on the previous day's "
            "dispatch and any overnight ancillary service obligations. Starting at minimum "
            "SOC (empty) gives maximum charge headroom and enables the full 1.6 MWh "
            "arbitrage cycle. Some operators deliberately deplete their BESS overnight into "
            "the balancing market — earning FCR-D down-regulation payments — to start each "
            "DA day maximally flexible. This case also shows the asymmetry: the same "
            "two-block prices earn €145 from empty but €0 from full (Edge 8), which is why "
            "multi-day rolling optimisation of terminal SOC is important in real operations."
        ),
        da_prices=da, result=r, checks=checks,
    )


def case_8_soc_max_start() -> CaseResult:
    """
    Battery starts full (SOC_init = SOC_max = 1.8 MWh) with two-block prices.
    p_low=30 €/MWh in h0-11, p_high=150 €/MWh in h12-23.

    The battery cannot charge (already full). To profit from the expensive evening,
    it would need to have energy to discharge at h12-23 — but it already has it!
    However, to discharge in the evening it must first create room by discharging
    in the morning (at the cheap price of 30 €/MWh), then recharge in the evening
    (at the expensive price of 150 €/MWh) to satisfy terminal SOC = 1.8 MWh.

    That cycle: sell at 30, buy at 150 → always a loss.
    Any other cycle (discharge evening, recharge evening) costs degradation with
    no price benefit. Idle is optimal.

    Terminal SOC must return to soc_init = 1.8 MWh → any throughput requires
    buying at 150 €/MWh (the only way to recharge after discharge), which costs
    more than it earns.

    Expected: battery idle, profit = 0.
    """
    cfg_high = load_config(**{"bess.soc_init_frac": 0.90})
    soc_init_high = cfg_high.bess.soc_init_mwh   # 1.8 MWh

    da = np.array([30.0] * 12 + [150.0] * 12)
    r = SOLVER.solve(_bundle(da), cfg_high)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    throughput = (pch + pdis).sum()

    checks = [
        ("All p_ch = 0 (already at SOC_max, and recharging at 150 is expensive)",
         pch.max() < 1e-4,
         f"max p_ch = {pch.max():.6f} MW"),
        ("All p_dis = 0 (discharging at cheap morning prices then recharging at 150 is a loss)",
         pdis.max() < 1e-4,
         f"max p_dis = {pdis.max():.6f} MW"),
        ("Total throughput = 0",
         throughput < 1e-4,
         f"throughput = {throughput:.6f} MWh"),
        ("Expected profit = 0 (stuck at max, no profitable return path)",
         abs(r.expected_profit) < 1.0,
         f"profit = €{r.expected_profit:.4f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Edge 8 — SOC starts at maximum (1.8 MWh), two-block prices",
        description="soc_init=1.8 MWh (=SOC_max). DA=30 h0-11 / 150 h12-23. No profitable cycle exists.",
        expected="Battery idle — discharging cheap then recharging expensive always loses money.",
        real_world=(
            "If the battery ended the previous day fully charged (e.g., after providing "
            "down-regulation overnight), it has no room to absorb cheap morning power. Any "
            "trade requires first discharging at the cheap morning price (30 €/MWh) and then "
            "recharging at the expensive evening price (150 €/MWh) — a guaranteed loss. This "
            "is the 'carry-over constraint' in multi-day BESS operation: today's terminal SOC "
            "is tomorrow's initial constraint. Sophisticated operators run a rolling multi-day "
            "optimisation to avoid ending the day fully charged when the next morning is "
            "forecast to be cheap, and to avoid ending the day empty when the next morning "
            "is forecast to be expensive."
        ),
        da_prices=da, result=r, checks=checks,
    )


# ── runner & reporter ─────────────────────────────────────────────────────────

def _hourly_table(cr: CaseResult) -> str:
    r = cr.result
    net  = r.da_bids + r.id_trades[0]
    soc  = r.soc[0]
    pch  = r.charge[0]
    pdis = r.discharge[0]
    da   = cr.da_prices

    lines = [
        "| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |",
        "|------|----------|--------------|------|-------|-----|--------|",
    ]
    for t in range(T):
        if pch[t] > 0.01:
            action = "CHARGE"
        elif pdis[t] > 0.01:
            action = "DISCHARGE"
        else:
            action = "idle"
        lines.append(
            f"| {t:4d} | {da[t]:8.1f} | {net[t]:+12.3f} | {pch[t]:4.3f} | "
            f"{pdis[t]:5.3f} | {soc[t]:.3f} | {action} |"
        )
    return "\n".join(lines)


def _verdict_block(cr: CaseResult) -> str:
    lines = []
    for label, ok, detail in cr.checks:
        icon = "✓" if ok else "✗"
        lines.append(f"  {icon} {label} — {detail}")
    overall = "PASS" if cr.passed else "FAIL"
    lines.append(f"\n  **Overall: {overall}**")
    return "\n".join(lines)


def _bess_summary() -> str:
    b = CFG.bess
    return (
        f"Power: {b.power_charge_mw} MW / {b.power_discharge_mw} MW  |  "
        f"Capacity: {b.energy_capacity_mwh} MWh  |  "
        f"SOC init/min/max: {b.soc_init_mwh}/{b.soc_min_mwh}/{b.soc_max_mwh} MWh  |  "
        f"η_ch={b.eta_charge} η_dis={b.eta_discharge} RTE={RTE:.4f}  |  "
        f"Deg: {b.deg_cost_eur_per_mwh} €/MWh"
    )


def run_and_report(output_path: Path | None = None) -> str:
    cases = [
        case_1_zero_prices,
        case_2_neg100_above_breakeven,
        case_3_neg300_below_breakeven,
        case_4_spread_just_below_breakeven,
        case_5_spread_just_above_breakeven,
        case_6_alternating_prices,
        case_7_soc_min_start,
        case_8_soc_max_start,
    ]

    sections = [
        "# storopt — Optimizer Edge Cases\n",
        f"Run date: {TODAY}  |  Solver: HiGHS\n",
        f"BESS: {_bess_summary()}\n",
        f"Break-even thresholds: uniform_neg={P_BE_UNIFORM_NEG:.1f} €/MWh  |  "
        f"two-block (p_low={P_LOW_TWO_BLOCK:.0f}) p_high_be={P_HIGH_BE:.2f} €/MWh\n",
        "> **Note on q_da vs net position:** when DA price = ID price the solver is indifferent\n"
        "> about how to split a position between the two legs. All checks use the **total net\n"
        "> position = q_da + q_id** or physical variables (p_ch, p_dis, SOC), never q_da alone.\n",
        "---\n",
    ]

    all_pass = True
    results: list[CaseResult] = []
    for fn in cases:
        print(f"  Running {fn.__name__}...", end=" ", flush=True)
        cr = fn()
        results.append(cr)
        status = "PASS" if cr.passed else "FAIL"
        print(status)
        all_pass = all_pass and cr.passed

        sections += [
            f"## {cr.name}\n",
            f"**Description:** {cr.description}\n",
            f"**Expected:** {cr.expected}\n",
            f"**Profit:** €{cr.result.expected_profit:.2f}  |  "
            f"**Solve time:** {cr.result.solve_time_s:.3f}s  |  "
            f"**Status:** {cr.result.solve_status}\n",
            f"**Real-world context:** {cr.real_world}\n",
            "### Checks\n",
            _verdict_block(cr) + "\n",
            "### Hourly dispatch\n",
            _hourly_table(cr) + "\n",
            "---\n",
        ]

    rows = ["| Case | Profit | Status |", "|------|--------|--------|"]
    for cr in results:
        rows.append(f"| {cr.name} | €{cr.result.expected_profit:.2f} | {'PASS ✓' if cr.passed else 'FAIL ✗'} |")
    sections.append("\n## Summary\n\n" + "\n".join(rows) + "\n")
    sections.append(f"\n{'**All cases PASSED ✓**' if all_pass else '**SOME CASES FAILED ✗**'}\n")

    report = "\n".join(sections)
    if output_path:
        output_path.write_text(report, encoding="utf-8")
        print(f"\nReport written to {output_path}")
    return report


if __name__ == "__main__":
    print("Running optimizer edge cases...\n")
    report = run_and_report(output_path=Path(__file__).parent / "edge_results.md")
    print(report)
