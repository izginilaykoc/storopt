# storopt — Optimizer Edge Cases

Run date: 2026-05-09  |  Solver: HiGHS

BESS: Power: 1.0 MW / 1.0 MW  |  Capacity: 2.0 MWh  |  SOC init/min/max: 1.0/0.2/1.8 MWh  |  η_ch=0.95 η_dis=0.95 RTE=0.9025  |  Deg: 10.0 €/MWh

Break-even thresholds: uniform_neg=195.1 €/MWh  |  two-block (p_low=50) p_high_be=76.48 €/MWh

> **Note on q_da vs net position:** when DA price = ID price the solver is indifferent
> about how to split a position between the two legs. All checks use the **total net
> position = q_da + q_id** or physical variables (p_ch, p_dis, SOC), never q_da alone.

---

## Edge 1 — All prices = 0 (no revenue)

**Description:** DA = 0 €/MWh all 24 hours. No generation.

**Expected:** Battery fully idle — any throughput incurs degradation with zero revenue.

**Profit:** €0.00  |  **Solve time:** 0.019s  |  **Status:** optimal

**Real-world context:** Zero clearing prices occur during extreme renewable surplus meeting very low demand (e.g., Easter Sunday in Denmark during a wind storm). With no price signal in either direction, there is no economic case for storage dispatch. The battery sits idle to avoid degradation cost for zero gain. In practice, operators in this situation shift focus to ancillary services (FCR-N, FCR-D) which pay a capacity fee independent of energy prices.

### Checks

  ✓ All p_ch = 0 — max p_ch = 0.000000 MW
  ✓ All p_dis = 0 — max p_dis = 0.000000 MW
  ✓ Total throughput = 0 — throughput = 0.000000 MWh
  ✓ Net position = 0 — max |net| = 0.000000 MW
  ✓ Expected profit = 0 — profit = €0.0000
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    1 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    2 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    3 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    4 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    5 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    6 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    7 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    8 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    9 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   10 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   11 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   12 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   17 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   21 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   22 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   23 |      0.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |

---

## Edge 2 — Uniform −100 €/MWh + 2 MW wind (forced selling, loss)

**Description:** DA = −100 €/MWh, gen = 2 MW all 24 hours. |p|=100 < P_BE=195.1. No curtailment.

**Expected:** Battery idle (cycling unprofitable), all generation sold at loss. Profit ≈ −€4800.

**Profit:** €-4800.00  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** Negative clearing prices are a real feature of the Nordic day-ahead market (EPEX Spot Nord Pool). At −100 €/MWh, an offshore wind plant owes the grid operator €100 per MWh it injects — a €4,800 bill for a 48 MWh day. Unlike gas plants, offshore turbines cannot be shut down quickly (minimum 4–6 hours notice, electrical safety constraints, PPA obligations). The battery cannot mitigate this: cycling is upside-down below the 195 €/MWh break-even. The real-world response is to either accept the loss or negotiate a negative-price suspension clause in the PPA that halts delivery obligations during sustained negative price windows.

### Checks

  ✓ Battery idle — cycling below break-even makes losses worse — throughput = 0.000000 MWh
  ✓ All generation forced to market (energy balance equality, no curtailment) — max |net − 2| = 0.0000 MW
  ✓ Profit ≈ −4800 € (2 MW × 24h × −100 €/MWh, no battery benefit) — profit = €-4800.00 (expected ≈ €-4800)
  ✓ Profit < 0 — negative prices with non-curtailable generation — profit = €-4800.00
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    1 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    2 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    3 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    4 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    5 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    6 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    7 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    8 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|    9 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   10 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   11 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   12 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |   -100.0 |       +2.000 | -0.000 | 0.000 | 1.000 | idle |
|   17 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   21 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   22 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |
|   23 |   -100.0 |       +2.000 | 0.000 | 0.000 | 1.000 | idle |

---

## Edge 3 — Uniform −300 €/MWh + 2 MW wind (cycling reduces losses)

**Description:** DA = −300 €/MWh, gen = 2 MW all 24 hours. |p|=300 > P_BE=195.1. No curtailment.

**Expected:** Battery cycles (profitable above break-even), but generation losses dominate. Profit < 0.

**Profit:** €-14275.37  |  **Solve time:** 0.030s  |  **Status:** optimal

**Real-world context:** Extreme negative prices (−300 €/MWh) occur during severe curtailment events — record wind output on a public holiday is a real example. At this level, battery cycling is profitable in isolation (|p| > 195 €/MWh break-even saves ~€125/day) but the unavoidable generation selling dominates: the plant is still €14,275 in the red. Events like this drive investment in demand-response partnerships (hydrogen electrolysers, aluminium smelters) that can absorb excess generation at an agreed offtake price, converting a trading loss into an industrial contract. The battery here is damage control, not a profit centre.

### Checks

  ✓ Battery cycles to reduce losses (p_ch > 0) — Σ p_ch = 12.188 MWh
  ✓ Battery discharges to return SOC (p_dis > 0) — Σ p_dis = 11.000 MWh
  ✓ Profit < 0 — generation selling losses dominate any cycling gains — profit = €-14275.37
  ✓ Profit > −14400 € — battery reduces losses vs. idle baseline — profit = €-14275.37 > base = €-14400
  ✓ Some hours net position < 2 MW (battery absorbing generation) — min net = 1.000 MW (gen = 2.0 MW)
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |   -300.0 |       +1.158 | 0.842 | 0.000 | 1.000 | CHARGE |
|    1 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.800 | DISCHARGE |
|    2 |   -300.0 |       +1.036 | 0.964 | 0.000 | 0.747 | CHARGE |
|    3 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.663 | DISCHARGE |
|    4 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.611 | CHARGE |
|    5 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.561 | DISCHARGE |
|    6 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.508 | CHARGE |
|    7 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.458 | DISCHARGE |
|    8 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.405 | CHARGE |
|    9 |   -300.0 |       +3.000 | -0.000 | 1.000 | 1.355 | DISCHARGE |
|   10 |   -300.0 |       +1.000 | 1.000 | -0.000 | 0.303 | CHARGE |
|   11 |   -300.0 |       +3.000 | -0.000 | 1.000 | 1.253 | DISCHARGE |
|   12 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.200 | CHARGE |
|   13 |   -300.0 |       +1.316 | 0.684 | -0.000 | 1.150 | CHARGE |
|   14 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.800 | DISCHARGE |
|   15 |   -300.0 |       +1.144 | 0.856 | -0.000 | 0.747 | CHARGE |
|   16 |   -300.0 |       +3.000 | -0.000 | 1.000 | 1.561 | DISCHARGE |
|   17 |   -300.0 |       +1.000 | 1.000 | -0.000 | 0.508 | CHARGE |
|   18 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.458 | DISCHARGE |
|   19 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.405 | CHARGE |
|   20 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.355 | DISCHARGE |
|   21 |   -300.0 |       +1.000 | 1.000 | 0.000 | 0.303 | CHARGE |
|   22 |   -300.0 |       +3.000 | 0.000 | 1.000 | 1.253 | DISCHARGE |
|   23 |   -300.0 |       +1.158 | 0.842 | 0.000 | 0.200 | CHARGE |

---

## Edge 4 — Two-block p_high=76.0 just below break-even 76.48

**Description:** DA = 50.0 €/MWh h0-11, 76.0 €/MWh h12-23. Spread 26 < 26.48 needed.

**Expected:** Battery idle — spread just below break-even, arbitrage unprofitable.

**Profit:** €0.00  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** A 26 €/MWh spread is visible in the DA market on many days, but the optimizer correctly ignores it because the round-trip losses (RTE = 0.9025) plus degradation (10 €/MWh) consume the entire spread. A naive rule-of-thumb trader — 'charge when price < 60, discharge when price > 76' — would cycle the battery and destroy €0.37/day in value. In competitive liquid markets, obvious spread opportunities are quickly priced away; precise arithmetic at the margin is exactly where algorithmic optimisation beats human heuristics.

### Checks

  ✓ All p_ch = 0 (below break-even spread) — max p_ch = 0.000000 MW
  ✓ All p_dis = 0 — max p_dis = 0.000000 MW
  ✓ Total throughput = 0 — throughput = 0.000000 MWh
  ✓ Expected profit = 0 — profit = €0.0000
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    1 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    2 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    3 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    4 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    5 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    6 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    7 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    8 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    9 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   10 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   11 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   12 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |     76.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   17 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   21 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   22 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   23 |     76.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |

---

## Edge 5 — Two-block p_high=77.0 just above break-even 76.48

**Description:** DA = 50.0 €/MWh h0-11, 77.0 €/MWh h12-23. Spread 27 > 26.48 needed.

**Expected:** Battery trades — spread just above break-even gives small positive profit.

**Profit:** €0.39  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** The €0.39 profit for a 1 MW/2 MWh battery is economically irrelevant (below transaction and scheduling costs in a real market). But at commercial scale — a 100 MW/200 MWh system — the same spread yields €3,900/day or ~€1.4M annually just from this recurrent pattern. This case also validates that the optimizer's threshold is exact: it dispatches only when genuinely profitable after accounting for physics (RTE) and costs (degradation). Operators relying on this marginal arbitrage in practice also layer in capacity market revenues and balancing reserve fees to improve the economics.

### Checks

  ✓ Charging occurs in morning (h0-11) — Σ p_ch[0:12] = 0.8421 MWh
  ✓ Discharging occurs in evening (h12-23) — Σ p_dis[12:] = 0.7600 MWh
  ✓ No charging in evening (buying expensive is suboptimal) — Σ p_ch[12:] = 0.000000 MWh
  ✓ No discharging in morning (selling cheap is suboptimal) — Σ p_dis[0:12] = 0.000000 MWh
  ✓ Net position ≤ 0 in morning (buying) — max net[0:12] = 0.0000 MW
  ✓ Net position ≥ 0 in evening (selling) — min net[12:] = 0.0000 MW
  ✓ Expected profit > 0 (spread exceeds break-even) — profit = €0.3937
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    1 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    2 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    3 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    4 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    5 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    6 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    7 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    8 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    9 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   10 |     50.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   11 |     50.0 |       -0.842 | 0.842 | 0.000 | 1.000 | CHARGE |
|   12 |     77.0 |       +0.760 | 0.000 | 0.760 | 1.800 | DISCHARGE |
|   13 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   14 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   15 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   16 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   17 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   18 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   19 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   20 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   21 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   22 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   23 |     77.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |

---

## Edge 6 — Alternating ±200 €/MWh (max cycling profit)

**Description:** DA alternates +200/−200 each hour (even=+200, odd=−200). No generation.

**Expected:** Discharge at positive hours, charge at negative hours, very high profit.

**Profit:** €4280.62  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** Hourly price swings of this magnitude are rare in the DA market but common in continuous intraday trading (EPEX IDA products). A storage operator with intraday access can in theory cycle the battery every two hours to exploit these swings. The €4,280 single-day profit for a 1 MW/2 MWh unit illustrates why high-frequency intraday strategies have become the primary revenue source for grid-scale BESS in liquid European markets — often outperforming capacity markets and ancillary services on a per-MW basis. In practice the battery is also simultaneously bidding into FCR/aFRR, so the intraday position sits on top of a reserve capacity obligation.

### Checks

  ✓ Discharging concentrated at positive-price hours — Σ p_dis[pos hrs] = 10.688 MWh
  ✓ Charging concentrated at negative-price hours — Σ p_ch[neg hrs] = 11.842 MWh
  ✓ No discharging at negative-price hours (loses money) — Σ p_dis[neg hrs] = 0.0000 MWh
  ✓ No charging at positive-price hours (loses money) — Σ p_ch[pos hrs] = 0.0000 MWh
  ✓ Throughput > 0 (multiple cycles) — throughput = 22.530 MWh
  ✓ Profit significantly > 0 (strong cycling opportunity) — profit = €4280.62
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |    200.0 |       +0.142 | 0.000 | 0.142 | 1.000 | DISCHARGE |
|    1 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.850 | CHARGE |
|    2 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.800 | DISCHARGE |
|    3 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.747 | CHARGE |
|    4 |    200.0 |       +0.805 | 0.000 | 0.805 | 1.697 | DISCHARGE |
|    5 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.850 | CHARGE |
|    6 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.800 | DISCHARGE |
|    7 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.747 | CHARGE |
|    8 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.697 | DISCHARGE |
|    9 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.645 | CHARGE |
|   10 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.595 | DISCHARGE |
|   11 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.542 | CHARGE |
|   12 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.492 | DISCHARGE |
|   13 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.439 | CHARGE |
|   14 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.389 | DISCHARGE |
|   15 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.337 | CHARGE |
|   16 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.287 | DISCHARGE |
|   17 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.234 | CHARGE |
|   18 |    200.0 |       +0.838 | 0.000 | 0.838 | 1.184 | DISCHARGE |
|   19 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.303 | CHARGE |
|   20 |    200.0 |       +1.000 | 0.000 | 1.000 | 1.253 | DISCHARGE |
|   21 |   -200.0 |       -1.000 | 1.000 | 0.000 | 0.200 | CHARGE |
|   22 |    200.0 |       +0.902 | 0.000 | 0.902 | 1.150 | DISCHARGE |
|   23 |   -200.0 |       -0.842 | 0.842 | 0.000 | 0.200 | CHARGE |

---

## Edge 7 — SOC starts at minimum (0.2 MWh), two-block prices

**Description:** soc_init=0.2 MWh (=SOC_min). DA=30 h0-11 / 150 h12-23. Full headroom available.

**Expected:** Full charge-discharge cycle, profit ≈ €145.

**Profit:** €145.43  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** Battery state at the start of each trading day depends on the previous day's dispatch and any overnight ancillary service obligations. Starting at minimum SOC (empty) gives maximum charge headroom and enables the full 1.6 MWh arbitrage cycle. Some operators deliberately deplete their BESS overnight into the balancing market — earning FCR-D down-regulation payments — to start each DA day maximally flexible. This case also shows the asymmetry: the same two-block prices earn €145 from empty but €0 from full (Edge 8), which is why multi-day rolling optimisation of terminal SOC is important in real operations.

### Checks

  ✓ Charging occurs in cheap morning (h0-11) — Σ p_ch[0:12] = 1.684 MWh
  ✓ No discharging in morning (SOC starts at min, nothing to discharge) — Σ p_dis[0:12] = 0.0000 MWh
  ✓ Discharging occurs in expensive evening (h12-23) — Σ p_dis[12:] = 1.520 MWh
  ✓ SOC peaks at or near SOC_max after charging — SOC_peak = 1.800 MWh (max=1.8)
  ✓ Terminal SOC returns to SOC_min — terminal SOC = 0.200 MWh (init=0.2)
  ✓ Profit approximately €145 (full cycle headroom) — profit = €145.43
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     30.0 |       -1.000 | 1.000 | 0.000 | 0.200 | CHARGE |
|    1 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    2 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    3 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    4 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    5 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    6 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    7 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.150 | idle |
|    8 |     30.0 |       -0.684 | 0.684 | 0.000 | 1.150 | CHARGE |
|    9 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   10 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   11 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   12 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   13 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   14 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   15 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   16 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   17 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   18 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   19 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   20 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   21 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   22 |    150.0 |       +0.520 | 0.000 | 0.520 | 1.800 | DISCHARGE |
|   23 |    150.0 |       +1.000 | 0.000 | 1.000 | 1.253 | DISCHARGE |

---

## Edge 8 — SOC starts at maximum (1.8 MWh), two-block prices

**Description:** soc_init=1.8 MWh (=SOC_max). DA=30 h0-11 / 150 h12-23. No profitable cycle exists.

**Expected:** Battery idle — discharging cheap then recharging expensive always loses money.

**Profit:** €0.00  |  **Solve time:** 0.012s  |  **Status:** optimal

**Real-world context:** If the battery ended the previous day fully charged (e.g., after providing down-regulation overnight), it has no room to absorb cheap morning power. Any trade requires first discharging at the cheap morning price (30 €/MWh) and then recharging at the expensive evening price (150 €/MWh) — a guaranteed loss. This is the 'carry-over constraint' in multi-day BESS operation: today's terminal SOC is tomorrow's initial constraint. Sophisticated operators run a rolling multi-day optimisation to avoid ending the day fully charged when the next morning is forecast to be cheap, and to avoid ending the day empty when the next morning is forecast to be expensive.

### Checks

  ✓ All p_ch = 0 (already at SOC_max, and recharging at 150 is expensive) — max p_ch = 0.000000 MW
  ✓ All p_dis = 0 (discharging at cheap morning prices then recharging at 150 is a loss) — max p_dis = 0.000000 MW
  ✓ Total throughput = 0 — throughput = 0.000000 MWh
  ✓ Expected profit = 0 (stuck at max, no profitable return path) — profit = €0.0000
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    1 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    2 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    3 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.800 | idle |
|    4 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    5 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    6 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.800 | idle |
|    7 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    8 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|    9 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   10 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   11 |     30.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   12 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.800 | idle |
|   13 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   14 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   15 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   16 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   17 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   18 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   19 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.800 | idle |
|   20 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   21 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   22 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   23 |    150.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |

---


## Summary

| Case | Profit | Status |
|------|--------|--------|
| Edge 1 — All prices = 0 (no revenue) | €0.00 | PASS ✓ |
| Edge 2 — Uniform −100 €/MWh + 2 MW wind (forced selling, loss) | €-4800.00 | PASS ✓ |
| Edge 3 — Uniform −300 €/MWh + 2 MW wind (cycling reduces losses) | €-14275.37 | PASS ✓ |
| Edge 4 — Two-block p_high=76.0 just below break-even 76.48 | €0.00 | PASS ✓ |
| Edge 5 — Two-block p_high=77.0 just above break-even 76.48 | €0.39 | PASS ✓ |
| Edge 6 — Alternating ±200 €/MWh (max cycling profit) | €4280.62 | PASS ✓ |
| Edge 7 — SOC starts at minimum (0.2 MWh), two-block prices | €145.43 | PASS ✓ |
| Edge 8 — SOC starts at maximum (1.8 MWh), two-block prices | €0.00 | PASS ✓ |


**All cases PASSED ✓**
