"""
Energinet EDS adapter — DK1 day-ahead prices, imbalance prices, and wind generation.

All three sources are public open data (no API token required).

Data sources:
  DA:         Energinet `Elspotprices`               → da_eur_mwh
  ID:         Energinet `RegulatingBalancePowerdata`  → id_eur_mwh
  Generation: Energinet `GenerationProdTypeExchange`  → hr1_generation_mw
              OffshoreWindPower (DK1 aggregate) scaled to HR1 capacity.

API base: https://api.energidataservice.dk/dataset/<dataset>

Generation scaling note:
  ENTSO-E per-plant generation data (document type A73) is not published for
  Danish plants. The `GenerationProdTypeExchange` dataset provides total DK1
  offshore wind production. We scale it to Horns Rev 1 by capacity share:
    HR1_CAPACITY_MW / DK1_OFFSHORE_CAPACITY_MW = 160 / 1592 ≈ 0.1005
  This assumes all DK1 offshore turbines operate at the same capacity factor,
  which is a reasonable approximation given the geographic proximity of the farms.

Output columns:
  delivery_ts_utc     datetime64[ns, UTC]
  da_eur_mwh          float64   (DA adapter only)
  id_eur_mwh          float64   (ID adapter only)
  hr1_generation_mw   float64   (generation adapter only)
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from storopt.ingestion.base import DataAdapter
from storopt.ingestion.cache import DataCache

_BASE_URL = "https://api.energidataservice.dk/dataset"
_TIMEOUT = 60
_RETRIES = 2


def _fetch_energinet(
    dataset: str,
    start: date,
    end: date,
    area: str = "DK1",
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch records from Energinet EDS. Returns list of raw record dicts."""
    sess = session or requests.Session()
    # end + 1 day because the API end parameter is exclusive
    end_exclusive = pd.Timestamp(end) + pd.Timedelta(days=1)
    params = {
        "start": pd.Timestamp(start).strftime("%Y-%m-%dT%H:%M"),
        "end": end_exclusive.strftime("%Y-%m-%dT%H:%M"),
        "timezone": "UTC",
        "limit": 0,
        "filter": json.dumps({"PriceArea": [area]}, separators=(",", ":")),
    }
    url = f"{_BASE_URL}/{dataset}"
    last_exc: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            resp = sess.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, list):
                return payload
            return payload.get("records", [])
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRIES:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Energinet fetch failed for {dataset}: {last_exc}") from last_exc


class EnerginetDAAdapter(DataAdapter):
    """Fetch DK1 day-ahead price from Energinet Elspotprices."""

    def __init__(self, cache_dir: str | Path, area: str = "DK1") -> None:
        self._cache = DataCache(cache_dir, "energinet_da")
        self._area = area
        self._session = requests.Session()

    def fetch(self, start: date, end: date) -> pd.DataFrame:
        cached = self._cache.get(start, end)
        if cached is not None:
            return cached

        records = _fetch_energinet("Elspotprices", start, end, self._area, self._session)
        if not records:
            raise RuntimeError(f"Energinet Elspotprices returned no data for {start}–{end}")

        df = pd.DataFrame(records)
        # Normalize to standard schema
        ts_col = next((c for c in ["HourUTC", "TimestampUTC", "TimeUTC"] if c in df.columns), None)
        price_col = next((c for c in ["SpotPriceEUR", "DayAheadPriceEUR"] if c in df.columns), None)
        if ts_col is None or price_col is None:
            raise RuntimeError(f"Unexpected Elspotprices columns: {list(df.columns)}")

        out = pd.DataFrame()
        out["delivery_ts_utc"] = pd.to_datetime(df[ts_col], utc=True)
        out["da_eur_mwh"] = pd.to_numeric(df[price_col], errors="coerce")
        out = (
            out.sort_values("delivery_ts_utc")
            .drop_duplicates(subset=["delivery_ts_utc"], keep="last")
            .reset_index(drop=True)
        )
        self._cache.put(out, start, end)
        return out


class EnerginetGenerationAdapter(DataAdapter):
    """
    Fetch DK1 offshore wind generation from Energinet GenerationProdTypeExchange.

    Returns OffshoreWindPower scaled to the specified plant's capacity share.
    No API token required — public open data.

    Parameters
    ----------
    cache_dir:
        Where to store the cached parquet.
    area:
        Energinet price area (default: "DK1").
    plant_capacity_mw:
        Nameplate capacity of the specific plant [MW] (default: 160 for HR1).
    dk1_offshore_capacity_mw:
        Total installed DK1 offshore capacity used for scaling [MW].
        Default 1592 MW reflects the post-2023 fleet (HR1+HR2+HR3+Anholt+Rødsand1+2).
    gen_column:
        Output column name (default: "hr1_generation_mw").
    """

    # DK1 offshore wind fleet installed capacity (post-2023)
    _DK1_OFFSHORE_CAPACITY_MW = 1592.0
    _HR1_CAPACITY_MW = 160.0

    def __init__(
        self,
        cache_dir: str | Path,
        area: str = "DK1",
        plant_capacity_mw: float = _HR1_CAPACITY_MW,
        dk1_offshore_capacity_mw: float = _DK1_OFFSHORE_CAPACITY_MW,
        gen_column: str = "hr1_generation_mw",
    ) -> None:
        self._cache = DataCache(cache_dir, "energinet_gen")
        self._area = area
        self._scale = plant_capacity_mw / dk1_offshore_capacity_mw
        self._gen_column = gen_column
        self._session = requests.Session()

    def fetch(self, start: date, end: date) -> pd.DataFrame:
        cached = self._cache.get(start, end)
        if cached is not None:
            return cached

        records = _fetch_energinet(
            "GenerationProdTypeExchange", start, end, self._area, self._session
        )
        if not records:
            raise RuntimeError(
                f"Energinet GenerationProdTypeExchange returned no data for {start}–{end}"
            )

        df = pd.DataFrame(records)
        ts_col = next((c for c in ["HourUTC", "TimeUTC", "TimestampUTC"] if c in df.columns), None)
        gen_col = next((c for c in ["OffshoreWindPower"] if c in df.columns), None)
        if ts_col is None or gen_col is None:
            raise RuntimeError(
                f"Unexpected GenerationProdTypeExchange columns: {list(df.columns)}"
            )

        out = pd.DataFrame()
        out["delivery_ts_utc"] = pd.to_datetime(df[ts_col], utc=True)
        out[self._gen_column] = pd.to_numeric(df[gen_col], errors="coerce") * self._scale
        out = (
            out.sort_values("delivery_ts_utc")
            .drop_duplicates(subset=["delivery_ts_utc"], keep="last")
            .reset_index(drop=True)
        )
        self._cache.put(out, start, end)
        return out


class EnerginetIDAdapter(DataAdapter):
    """Fetch DK1 imbalance price (ID proxy) from Energinet RegulatingBalancePowerdata."""

    def __init__(self, cache_dir: str | Path, area: str = "DK1") -> None:
        self._cache = DataCache(cache_dir, "energinet_id")
        self._area = area
        self._session = requests.Session()

    def fetch(self, start: date, end: date) -> pd.DataFrame:
        cached = self._cache.get(start, end)
        if cached is not None:
            return cached

        records = _fetch_energinet(
            "RegulatingBalancePowerdata", start, end, self._area, self._session
        )
        if not records:
            raise RuntimeError(f"Energinet RegulatingBalancePowerdata returned no data for {start}–{end}")

        df = pd.DataFrame(records)
        ts_col = next((c for c in ["HourUTC", "TimestampUTC"] if c in df.columns), None)
        price_col = next(
            (c for c in ["ImbalancePriceEUR", "ImbalancePriceEURMWh", "BalancePriceEUR"] if c in df.columns),
            None,
        )
        if ts_col is None or price_col is None:
            raise RuntimeError(f"Unexpected RegulatingBalancePowerdata columns: {list(df.columns)}")

        out = pd.DataFrame()
        out["delivery_ts_utc"] = pd.to_datetime(df[ts_col], utc=True)
        out["id_eur_mwh"] = pd.to_numeric(df[price_col], errors="coerce")
        out = (
            out.sort_values("delivery_ts_utc")
            .drop_duplicates(subset=["delivery_ts_utc"], keep="last")
            .reset_index(drop=True)
        )
        self._cache.put(out, start, end)
        return out
