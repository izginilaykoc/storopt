"""
Optimizer sanity cases — deterministic single-scenario tests.

Each case uses a hand-crafted price/generation profile with a clear expected
decision. The optimizer must make the economically correct choice.

When da_price == id_price the MILP is indifferent between the DA and ID legs
of any position (profit depends only on q_da + q_id, which equals the net
physical position via the energy-balance constraint). HiGHS may put any split
between q_da and q_id. Checks therefore use PHYSICAL variables (p_ch, p_dis,
soc) or the total net position (q_da + q_id[0]) — never q_da alone.

BESS defaults (configs/default.yaml):
  Power:     1 MW charge / 1 MW discharge
  Capacity:  2 MWh
  SOC:       init=1.0 MWh, min=0.2 MWh, max=1.8 MWh
  Efficiency: η_ch=0.95, η_dis=0.95  → RTE=0.9025
  Deg cost:  10 €/MWh throughput

Break-even spread for arbitrage to be profitable:
  p_high · RTE ≥ p_low + c_deg · (1 + RTE)
  p_high · 0.9025 ≥ p_low + 19.025
  e.g. at p_low=30 →  p_high must exceed 54.3 €/MWh  (spread ≥ 24.3 €/MWh)
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
    res_gen: np.ndarray
    result: OptimizationResult
    checks: list[tuple[str, bool, str]]   # (label, passed, detail)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


# ── test cases ────────────────────────────────────────────────────────────────

def case_1_two_block() -> CaseResult:
    """
    Morning cheap (30 €/MWh), evening expensive (150 €/MWh).
    Battery starts at SOC_init = 1.0 MWh. Usable headroom to charge: 0.8 MWh
    (→ 0.842 MWh of grid energy); usable headroom to discharge: 0.8 MWh
    (→ 0.76 MWh delivered to grid). Arbitrage spread = 120 €/MWh >> break-even.

    Expected: charge at some point during cheap hours (h0-11),
              discharge at some point during expensive hours (h12-23).
    """
    da = np.array([30.0] * 12 + [150.0] * 12)
    gen = np.zeros(T)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]          # (T,) MW
    pdis = r.discharge[0]       # (T,) MW
    soc  = r.soc[0]             # (T,) MWh   — SOC at start of each period
    net  = r.da_bids + r.id_trades[0]  # (T,) total net position MW

    morning_charge    = pch[:12].sum()
    evening_discharge = pdis[12:].sum()
    morning_discharge = pdis[:12].sum()
    evening_charge    = pch[12:].sum()

    checks = [
        ("Charging occurs in morning (h0-11)",
         morning_charge > 0.05,
         f"Σ p_ch[0:12] = {morning_charge:.3f} MWh"),
        ("No discharging in morning (h0-11)",
         morning_discharge < 1e-4,
         f"Σ p_dis[0:12] = {morning_discharge:.4f} MWh"),
        ("Discharging occurs in evening (h12-23)",
         evening_discharge > 0.05,
         f"Σ p_dis[12:] = {evening_discharge:.3f} MWh"),
        ("No charging in evening (h12-23)",
         evening_charge < 1e-4,
         f"Σ p_ch[12:] = {evening_charge:.4f} MWh"),
        ("Net position ≤ 0 in morning (buying / idle)",
         float(net[:12].max()) < 1e-4,
         f"max net[0:12] = {net[:12].max():.4f} MW"),
        ("Net position ≥ 0 in evening (selling / idle)",
         float(net[12:].min()) > -1e-4,
         f"min net[12:] = {net[12:].min():.4f} MW"),
        ("SOC peaks after morning charge",
         soc.max() > SOC_INIT + 0.05,
         f"SOC_peak = {soc.max():.3f} MWh"),
        ("Expected profit > 0",
         r.expected_profit > 0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Case 1 — Two-block: morning cheap / evening expensive",
        description="DA = 30 €/MWh h0-11 / 150 €/MWh h12-23. No generation.",
        expected="Charge in morning (net ≤ 0), discharge in evening (net ≥ 0).",
        real_world=(
            "The optimizer submits a buy bid (negative net position) for cheap morning hours "
            "and a sell bid for the expensive evening, locked in by 12:00 CET the day before "
            "delivery. In DK1 this pattern matches buying overnight wind surplus and selling "
            "into the evening demand peak. The €72.72 profit for a 1 MW/2 MWh battery is the "
            "textbook price-spread arbitrage revenue stream for grid-scale BESS."
        ),
        da_prices=da, res_gen=gen, result=r, checks=checks,
    )


def case_2_single_spike() -> CaseResult:
    """
    Flat at 50 €/MWh all day except a 500 €/MWh spike at hour 22.
    Battery must pre-charge before h22 to exploit the spike.
    Break-even: charge at 50, discharge at 500 → 500·0.9025 = 451 >> 50+19.

    Expected: pre-charge in hours before h22, full discharge at h22.
    """
    da = np.full(T, 50.0)
    da[22] = 500.0
    gen = np.zeros(T)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    soc  = r.soc[0]
    net  = r.da_bids + r.id_trades[0]

    soc_at_spike = soc[22]      # SOC at start of spike period
    precharge    = pch[:22].sum()
    dis_at_spike = pdis[22]
    charge_at_spike = pch[22]
    net_at_spike = net[22]

    checks = [
        ("Pre-charging before h22",
         precharge > 0.05,
         f"Σ p_ch[0:22] = {precharge:.3f} MWh"),
        ("SOC at spike hour is above SOC_INIT (pre-loaded)",
         soc_at_spike > SOC_INIT + 0.05,
         f"SOC[22] = {soc_at_spike:.3f} MWh (init={SOC_INIT})"),
        ("Discharging at spike hour h22",
         dis_at_spike > 0.1,
         f"p_dis[22] = {dis_at_spike:.3f} MW"),
        ("Not charging at spike hour",
         charge_at_spike < 1e-4,
         f"p_ch[22] = {charge_at_spike:.4f} MW"),
        ("Net position at h22 ≥ 0 (selling)",
         net_at_spike > 0.1,
         f"net[22] = {net_at_spike:.3f} MW"),
        ("Expected profit > 0",
         r.expected_profit > 0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Case 2 — Single price spike at hour 22",
        description="DA = 50 €/MWh all hours, h22 = 500 €/MWh. No generation.",
        expected="Pre-charge before h22, full discharge at h22.",
        real_world=(
            "A price spike at h22 (21:00–22:00 UTC) could reflect a large plant outage or "
            "an unexpected cold snap. The optimizer pre-charges during flat hours (50 €/MWh) "
            "and fires a full sell bid into the 500 €/MWh clearing price. This 'peak-shaving "
            "arbitrage' is also the economic foundation for capacity market bids: the battery "
            "guarantees availability at the exact hour the system is stressed, which is worth "
            "far more than average-price arbitrage. Operators with balancing market access "
            "(FCR/mFRR) can layer ancillary service revenue on top of the same position."
        ),
        da_prices=da, res_gen=gen, result=r, checks=checks,
    )


def case_3_negative_prices() -> CaseResult:
    """
    Negative prices h0-5 (−200 €/MWh): consuming power earns revenue.
    Positive prices h6-23 (+80 €/MWh): selling stored energy earns revenue.

    With negative prices, charging earns revenue (net position negative = buying
    from grid, multiplied by negative price gives positive revenue). Cycling
    (charge–discharge–charge) may occur if it creates room for more charging
    at negative prices; this is economically correct.

    Expected:
      - Net position ≤ 0 (buying) during h0-5  [charging is profitable]
      - SOC rises during h0-5
      - Net position ≥ 0 during h6-23 at hours where discharge occurs
      - No sustained buying at positive prices (only sell or idle post h6)
    """
    da = np.full(T, 80.0)
    da[:6] = -200.0
    gen = np.zeros(T)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    soc  = r.soc[0]
    net  = r.da_bids + r.id_trades[0]

    charge_in_neg   = pch[:6].sum()
    soc_after_neg   = soc[5]           # SOC at START of h5 (after h4 action)
    net_pos_hrs     = net[6:]          # post-negative-price hours: should be ≥ 0
    charge_in_pos   = pch[6:].sum()    # should be 0 — no incentive to charge at 80€

    # Verify break-even at 80€: 80·RTE = 72.2 < 80 + 19.025 = 99 → NOT profitable → no charging
    checks = [
        ("Charging occurs during negative-price hours (h0-5)",
         charge_in_neg > 0.1,
         f"Σ p_ch[0:6] = {charge_in_neg:.3f} MWh"),
        ("SOC at or above SOC_INIT after negative-price window",
         soc_after_neg >= SOC_INIT - 1e-4,
         f"SOC[5] = {soc_after_neg:.3f} MWh (init={SOC_INIT})"),
        # The optimizer may cycle (charge→discharge→charge) during negative-price
        # hours to create room for more charging — this is provably optimal when
        # −p · η_ch · η_dis revenue exceeds degradation cost. The correct assertion
        # is that total net consumption (charging) exceeds total net production:
        ("Net consumption > net production during h0-5 (more buying than selling)",
         pch[:6].sum() > pdis[:6].sum() + 1e-4,
         f"Σ p_ch[0:6]={pch[:6].sum():.3f} > Σ p_dis[0:6]={pdis[:6].sum():.3f} MWh"),
        ("No charging at positive prices (h6-23) — unprofitable",
         charge_in_pos < 1e-4,
         f"Σ p_ch[6:] = {charge_in_pos:.4f} MWh"),
        ("Net position ≥ 0 at positive-price hours (selling or idle)",
         float(net_pos_hrs.min()) > -1e-4,
         f"min net[6:] = {net_pos_hrs.min():.4f} MW"),
        ("Expected profit > 0",
         r.expected_profit > 0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Case 3 — Negative price window (hours 0-5)",
        description="DA = −200 €/MWh h0-5, +80 €/MWh h6-23. No generation.",
        expected="Buy (charge) during negative hours; sell or idle during positive.",
        real_world=(
            "Negative prices occur in the Nordic pool during storm events when wind is high "
            "and demand is low. Energinet allows prices down to −500 €/MWh. At negative "
            "prices, market participants get PAID to consume power, so the optimizer submits "
            "a negative-priced buy bid to charge the battery — turning a curtailment penalty "
            "into revenue. After h6 prices turn positive (80 €/MWh), stored energy is sold. "
            "This is the primary economic rationale for co-locating battery storage with "
            "offshore wind: negative-price hours that would otherwise represent a cost become "
            "a charging opportunity."
        ),
        da_prices=da, res_gen=gen, result=r, checks=checks,
    )


def case_4_flat_prices() -> CaseResult:
    """
    All 24 hours at 80 €/MWh. No price gradient → no arbitrage.
    Any charge+discharge cycle loses: 80 · RTE = 72.2 < 80 + 19.025.
    Battery should be fully idle.

    Expected: p_ch ≈ 0, p_dis ≈ 0, total throughput ≈ 0, profit ≈ 0.
    """
    da = np.full(T, 80.0)
    gen = np.zeros(T)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    throughput = (pch + pdis).sum()
    net  = r.da_bids + r.id_trades[0]

    checks = [
        ("Total throughput ≈ 0 (no trading)",
         throughput < 1e-4,
         f"throughput = {throughput:.6f} MWh"),
        ("All p_ch ≈ 0",
         pch.max() < 1e-4,
         f"max p_ch = {pch.max():.6f} MW"),
        ("All p_dis ≈ 0",
         pdis.max() < 1e-4,
         f"max p_dis = {pdis.max():.6f} MW"),
        ("Total net position ≈ 0 (no open position)",
         float(np.abs(net).max()) < 1e-4,
         f"max |net| = {np.abs(net).max():.6f} MW"),
        ("Expected profit ≈ 0",
         abs(r.expected_profit) < 1.0,
         f"profit = €{r.expected_profit:.4f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Case 4 — Flat prices (no arbitrage)",
        description="DA = 80 €/MWh all 24 hours. No generation.",
        expected="Battery fully idle — arbitrage at flat prices loses money (RTE + deg cost).",
        real_world=(
            "On a mild autumn day with steady industrial load and moderate wind, DA prices "
            "may barely move across 24 hours. The optimizer submits zero-volume storage bids "
            "for every hour. This is the correct 'do nothing' baseline: any cycling incurs "
            "degradation cost (10 €/MWh) with zero offsetting revenue from a flat price "
            "profile. A naive rule-of-thumb strategy ('always cycle when price > 0') would "
            "destroy value here. In real markets, flat-price days are also opportunities to "
            "accumulate FCR-N ancillary service revenue by holding the battery at 50 % SOC "
            "without committing to any energy position."
        ),
        da_prices=da, res_gen=gen, result=r, checks=checks,
    )


def case_5_renewable_storage() -> CaseResult:
    """
    Excess wind generation (2 MW) in morning with low prices (40 €/MWh);
    no wind in evening with high prices (130 €/MWh).
    Battery absorbs morning surplus and sells into the expensive evening.

    Energy balance: q_da[t] + q_id[t] = gen[t] + p_dis[t] − p_ch[t]
    Morning (gen=2, p_ch=1): net = 2 − 1 = 1 MW to market  (less than full gen)
    Evening (gen=0, p_dis=1): net = 0 + 1 = 1 MW to market

    Expected:
      - Charging in morning (p_ch > 0 in h0-11)
      - Discharging in evening (p_dis > 0 in h12-23)
      - Morning net position < gen = 2 MW  (battery absorbs some wind)
      - Evening net position > 0  (selling stored energy)
    """
    da = np.array([40.0] * 12 + [130.0] * 12)
    gen = np.array([2.0] * 12 + [0.0] * 12)
    r = SOLVER.solve(_bundle(da, gen), CFG)

    pch  = r.charge[0]
    pdis = r.discharge[0]
    soc  = r.soc[0]
    net  = r.da_bids + r.id_trades[0]

    morning_charge    = pch[:12].sum()
    evening_discharge = pdis[12:].sum()
    morning_max_net   = float(net[:12].max())  # must be < 2 (battery absorbs wind)
    evening_max_net   = float(net[12:].max())  # must be > 0 (selling stored energy)
    soc_peak          = soc.max()

    checks = [
        ("Charging during wind morning (h0-11)",
         morning_charge > 0.05,
         f"Σ p_ch[0:12] = {morning_charge:.3f} MWh"),
        ("No discharging during wind morning (h0-11)",
         pdis[:12].sum() < 1e-4,
         f"Σ p_dis[0:12] = {pdis[:12].sum():.4f} MWh"),
        ("Discharging during calm evening (h12-23)",
         evening_discharge > 0.05,
         f"Σ p_dis[12:] = {evening_discharge:.3f} MWh"),
        # Battery charges for exactly the hours needed to fill headroom (≤ 1 hour).
        # The rest of morning the battery is idle (net = gen = 2 MW). Check that
        # at least one morning hour has the battery absorbing wind (net < gen).
        ("At least one morning hour absorbs wind (min net < gen)",
         float(net[:12].min()) < 2.0 - 0.05,
         f"min net[0:12] = {float(net[:12].min()):.3f} MW (gen=2)"),
        ("Evening net position > 0 (selling stored energy)",
         evening_max_net > 0.05,
         f"max net[12:] = {evening_max_net:.3f} MW"),
        ("SOC peaks above SOC_INIT (energy was stored)",
         soc_peak > SOC_INIT + 0.05,
         f"SOC_peak = {soc_peak:.3f} MWh"),
        ("Expected profit > 0",
         r.expected_profit > 0,
         f"profit = €{r.expected_profit:.2f}"),
        ("Solver optimal",
         r.solve_status in ("optimal", "feasible"),
         f"status = {r.solve_status}"),
    ]
    return CaseResult(
        name="Case 5 — Renewable storage: wind morning / calm evening",
        description="Gen = 2 MW h0-11 / 0 MW h12-23. DA = 40 €/MWh morning / 130 €/MWh evening.",
        expected="Charge from excess wind (morning), discharge into expensive evening.",
        real_world=(
            "The optimizer co-optimises the wind generation schedule and battery dispatch. "
            "During windy morning hours (40 €/MWh, gen = 2 MW), it charges the battery "
            "rather than selling all wind at a depressed price — the DA bid shows a reduced "
            "net position (less wind sold than available). In the calm, expensive evening "
            "(130 €/MWh, gen = 0), it discharges stored energy into the market. This "
            "wind-plus-storage co-optimisation is the commercial model behind offshore wind "
            "projects with embedded storage corridors and is why a standalone wind plant "
            "always earns less than a co-located wind+battery system."
        ),
        da_prices=da, res_gen=gen, result=r, checks=checks,
    )


# ── runner & reporter ─────────────────────────────────────────────────────────

def _hourly_table(cr: CaseResult) -> str:
    r = cr.result
    net  = r.da_bids + r.id_trades[0]
    soc  = r.soc[0]
    pch  = r.charge[0]
    pdis = r.discharge[0]
    da   = cr.da_prices
    gen  = cr.res_gen

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
        gen_tag = f" gen={gen[t]:.1f}" if gen[t] > 0.01 else ""
        lines.append(
            f"| {t:4d} | {da[t]:8.1f} | {net[t]:+12.3f} | {pch[t]:4.3f} | "
            f"{pdis[t]:5.3f} | {soc[t]:.3f} | {action}{gen_tag} |"
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
        case_1_two_block,
        case_2_single_spike,
        case_3_negative_prices,
        case_4_flat_prices,
        case_5_renewable_storage,
    ]

    sections = [
        "# storopt — Optimizer Sanity Cases\n",
        f"Run date: {TODAY}  |  Solver: HiGHS\n",
        f"BESS: {_bess_summary()}\n",
        "Break-even: p_high · RTE ≥ p_low + c_deg · (1 + RTE)  → "
        "at p_low=30: p_high must exceed 54.3 €/MWh.\n",
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

    # Summary table
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
    print("Running optimizer sanity cases...\n")
    report = run_and_report(output_path=Path(__file__).parent / "sanity_results.md")
    print(report)
