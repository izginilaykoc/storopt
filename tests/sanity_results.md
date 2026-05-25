# storopt — Optimizer Sanity Cases

Run date: 2026-05-09  |  Solver: HiGHS

BESS: Power: 1.0 MW / 1.0 MW  |  Capacity: 2.0 MWh  |  SOC init/min/max: 1.0/0.2/1.8 MWh  |  η_ch=0.95 η_dis=0.95 RTE=0.9025  |  Deg: 10.0 €/MWh

Break-even: p_high · RTE ≥ p_low + c_deg · (1 + RTE)  → at p_low=30: p_high must exceed 54.3 €/MWh.

> **Note on q_da vs net position:** when DA price = ID price the solver is indifferent
> about how to split a position between the two legs. All checks use the **total net
> position = q_da + q_id** or physical variables (p_ch, p_dis, SOC), never q_da alone.

---

## Case 1 — Two-block: morning cheap / evening expensive

**Description:** DA = 30 €/MWh h0-11 / 150 €/MWh h12-23. No generation.

**Expected:** Charge in morning (net ≤ 0), discharge in evening (net ≥ 0).

**Profit:** €72.72  |  **Solve time:** 0.029s  |  **Status:** optimal

**Real-world context:** The optimizer submits a buy bid (negative net position) for cheap morning hours and a sell bid for the expensive evening, locked in by 12:00 CET the day before delivery. In DK1 this pattern matches buying overnight wind surplus and selling into the evening demand peak. The €72.72 profit for a 1 MW/2 MWh battery is the textbook price-spread arbitrage revenue stream for grid-scale BESS.

### Checks

  ✓ Charging occurs in morning (h0-11) — Σ p_ch[0:12] = 0.842 MWh
  ✓ No discharging in morning (h0-11) — Σ p_dis[0:12] = 0.0000 MWh
  ✓ Discharging occurs in evening (h12-23) — Σ p_dis[12:] = 0.760 MWh
  ✓ No charging in evening (h12-23) — Σ p_ch[12:] = 0.0000 MWh
  ✓ Net position ≤ 0 in morning (buying / idle) — max net[0:12] = 0.0000 MW
  ✓ Net position ≥ 0 in evening (selling / idle) — min net[12:] = 0.0000 MW
  ✓ SOC peaks after morning charge — SOC_peak = 1.800 MWh
  ✓ Expected profit > 0 — profit = €72.72
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    1 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    2 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    3 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    4 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    5 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    6 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    7 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    8 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    9 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   10 |     30.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   11 |     30.0 |       -0.842 | 0.842 | 0.000 | 1.000 | CHARGE |
|   12 |    150.0 |       +0.760 | 0.000 | 0.760 | 1.800 | DISCHARGE |
|   13 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   14 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   15 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   16 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   17 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   18 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   19 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   20 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   21 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   22 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   23 |    150.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |

---

## Case 2 — Single price spike at hour 22

**Description:** DA = 50 €/MWh all hours, h22 = 500 €/MWh. No generation.

**Expected:** Pre-charge before h22, full discharge at h22.

**Profit:** €423.52  |  **Solve time:** 0.014s  |  **Status:** optimal

**Real-world context:** A price spike at h22 (21:00–22:00 UTC) could reflect a large plant outage or an unexpected cold snap. The optimizer pre-charges during flat hours (50 €/MWh) and fires a full sell bid into the 500 €/MWh clearing price. This 'peak-shaving arbitrage' is also the economic foundation for capacity market bids: the battery guarantees availability at the exact hour the system is stressed, which is worth far more than average-price arbitrage. Operators with balancing market access (FCR/mFRR) can layer ancillary service revenue on top of the same position.

### Checks

  ✓ Pre-charging before h22 — Σ p_ch[0:22] = 0.842 MWh
  ✓ SOC at spike hour is above SOC_INIT (pre-loaded) — SOC[22] = 1.800 MWh (init=1.0)
  ✓ Discharging at spike hour h22 — p_dis[22] = 1.000 MW
  ✓ Not charging at spike hour — p_ch[22] = 0.0000 MW
  ✓ Net position at h22 ≥ 0 (selling) — net[22] = 1.000 MW
  ✓ Expected profit > 0 — profit = €423.52
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
|   12 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   17 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |     50.0 |       -0.842 | 0.842 | 0.000 | 1.000 | CHARGE |
|   21 |     50.0 |       +0.000 | 0.000 | 0.000 | 1.800 | idle |
|   22 |    500.0 |       +1.000 | 0.000 | 1.000 | 1.800 | DISCHARGE |
|   23 |     50.0 |       -0.266 | 0.266 | 0.000 | 0.747 | CHARGE |

---

## Case 3 — Negative price window (hours 0-5)

**Description:** DA = −200 €/MWh h0-5, +80 €/MWh h6-23. No generation.

**Expected:** Buy (charge) during negative hours; sell or idle during positive.

**Profit:** €214.25  |  **Solve time:** 0.032s  |  **Status:** optimal

**Real-world context:** Negative prices occur in the Nordic pool during storm events when wind is high and demand is low. Energinet allows prices down to −500 €/MWh. At negative prices, market participants get PAID to consume power, so the optimizer submits a negative-priced buy bid to charge the battery — turning a curtailment penalty into revenue. After h6 prices turn positive (80 €/MWh), stored energy is sold. This is the primary economic rationale for co-locating battery storage with offshore wind: negative-price hours that would otherwise represent a cost become a charging opportunity.

### Checks

  ✓ Charging occurs during negative-price hours (h0-5) — Σ p_ch[0:6] = 3.058 MWh
  ✓ SOC at or above SOC_INIT after negative-price window — SOC[5] = 1.595 MWh (init=1.0)
  ✓ Net consumption > net production during h0-5 (more buying than selling) — Σ p_ch[0:6]=3.058 > Σ p_dis[0:6]=2.000 MWh
  ✓ No charging at positive prices (h6-23) — unprofitable — Σ p_ch[6:] = 0.0000 MWh
  ✓ Net position ≥ 0 at positive-price hours (selling or idle) — min net[6:] = 0.0000 MW
  ✓ Expected profit > 0 — profit = €214.25
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |   -200.0 |       -0.842 | 0.842 | -0.000 | 1.000 | CHARGE |
|    1 |   -200.0 |       +1.000 | -0.000 | 1.000 | 1.800 | DISCHARGE |
|    2 |   -200.0 |       -1.000 | 1.000 | -0.000 | 0.747 | CHARGE |
|    3 |   -200.0 |       +1.000 | -0.000 | 1.000 | 1.697 | DISCHARGE |
|    4 |   -200.0 |       -1.000 | 1.000 | -0.000 | 0.645 | CHARGE |
|    5 |   -200.0 |       -0.216 | 0.216 | -0.000 | 1.595 | CHARGE |
|    6 |     80.0 |       +0.760 | 0.000 | 0.760 | 1.800 | DISCHARGE |
|    7 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    8 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    9 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   10 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   11 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   12 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   17 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   21 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   22 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   23 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |

---

## Case 4 — Flat prices (no arbitrage)

**Description:** DA = 80 €/MWh all 24 hours. No generation.

**Expected:** Battery fully idle — arbitrage at flat prices loses money (RTE + deg cost).

**Profit:** €0.00  |  **Solve time:** 0.010s  |  **Status:** optimal

**Real-world context:** On a mild autumn day with steady industrial load and moderate wind, DA prices may barely move across 24 hours. The optimizer submits zero-volume storage bids for every hour. This is the correct 'do nothing' baseline: any cycling incurs degradation cost (10 €/MWh) with zero offsetting revenue from a flat price profile. A naive rule-of-thumb strategy ('always cycle when price > 0') would destroy value here. In real markets, flat-price days are also opportunities to accumulate FCR-N ancillary service revenue by holding the battery at 50 % SOC without committing to any energy position.

### Checks

  ✓ Total throughput ≈ 0 (no trading) — throughput = 0.000000 MWh
  ✓ All p_ch ≈ 0 — max p_ch = 0.000000 MW
  ✓ All p_dis ≈ 0 — max p_dis = 0.000000 MW
  ✓ Total net position ≈ 0 (no open position) — max |net| = 0.000000 MW
  ✓ Expected profit ≈ 0 — profit = €0.0000
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    1 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    2 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    3 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    4 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    5 |     80.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|    6 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    7 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    8 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|    9 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   10 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   11 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   12 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   13 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   14 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   15 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   16 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   17 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   18 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   19 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   20 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   21 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   22 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |
|   23 |     80.0 |       +0.000 | 0.000 | 0.000 | 1.000 | idle |

---

## Case 5 — Renewable storage: wind morning / calm evening

**Description:** Gen = 2 MW h0-11 / 0 MW h12-23. DA = 40 €/MWh morning / 130 €/MWh evening.

**Expected:** Charge from excess wind (morning), discharge into expensive evening.

**Profit:** €1009.09  |  **Solve time:** 0.011s  |  **Status:** optimal

**Real-world context:** The optimizer co-optimises the wind generation schedule and battery dispatch. During windy morning hours (40 €/MWh, gen = 2 MW), it charges the battery rather than selling all wind at a depressed price — the DA bid shows a reduced net position (less wind sold than available). In the calm, expensive evening (130 €/MWh, gen = 0), it discharges stored energy into the market. This wind-plus-storage co-optimisation is the commercial model behind offshore wind projects with embedded storage corridors and is why a standalone wind plant always earns less than a co-located wind+battery system.

### Checks

  ✓ Charging during wind morning (h0-11) — Σ p_ch[0:12] = 0.842 MWh
  ✓ No discharging during wind morning (h0-11) — Σ p_dis[0:12] = 0.0000 MWh
  ✓ Discharging during calm evening (h12-23) — Σ p_dis[12:] = 0.760 MWh
  ✓ At least one morning hour absorbs wind (min net < gen) — min net[0:12] = 1.158 MW (gen=2)
  ✓ Evening net position > 0 (selling stored energy) — max net[12:] = 0.760 MW
  ✓ SOC peaks above SOC_INIT (energy was stored) — SOC_peak = 1.800 MWh
  ✓ Expected profit > 0 — profit = €1009.09
  ✓ Solver optimal — status = optimal

  **Overall: PASS**

### Hourly dispatch

| Hour | DA price | Net pos (MW) | p_ch | p_dis | SOC | Action |
|------|----------|--------------|------|-------|-----|--------|
|    0 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    1 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    2 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    3 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    4 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    5 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    6 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    7 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    8 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|    9 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|   10 |     40.0 |       +2.000 | 0.000 | -0.000 | 1.000 | idle gen=2.0 |
|   11 |     40.0 |       +1.158 | 0.842 | 0.000 | 1.000 | CHARGE gen=2.0 |
|   12 |    130.0 |       +0.760 | 0.000 | 0.760 | 1.800 | DISCHARGE |
|   13 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   14 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   15 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   16 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   17 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   18 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   19 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   20 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   21 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   22 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |
|   23 |    130.0 |       +0.000 | 0.000 | -0.000 | 1.000 | idle |

---


## Summary

| Case | Profit | Status |
|------|--------|--------|
| Case 1 — Two-block: morning cheap / evening expensive | €72.72 | PASS ✓ |
| Case 2 — Single price spike at hour 22 | €423.52 | PASS ✓ |
| Case 3 — Negative price window (hours 0-5) | €214.25 | PASS ✓ |
| Case 4 — Flat prices (no arbitrage) | €0.00 | PASS ✓ |
| Case 5 — Renewable storage: wind morning / calm evening | €1009.09 | PASS ✓ |


**All cases PASSED ✓**
