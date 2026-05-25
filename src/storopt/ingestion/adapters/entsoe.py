"""
ENTSO-E generation adapter.

Supports two fetch modes, selected automatically by whether generation_file is set:

1. Pre-fetched file:
   Set config.ingestion.generation_file to a parquet file path or a directory of
   monthly files named HR1_<YYYY-MM>_PT15M.parquet (or any *.parquet).

2. Live REST API (when generation_file is empty or None):
   Fetches actual generation per plant (document type A73, process type A16) from
   the ENTSO-E Transparency Platform. Requires ENTSOE_SECURITY_TOKEN in .env or
   the shell environment. Results are cached as parquet in cache_dir so that the
   API is only hit once per date range.

Resolution: 15-minute data from the API is resampled to hourly mean.

Output columns:
  delivery_ts_utc    datetime64[ns, UTC]
  hr1_generation_mw  float64
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from storopt.ingestion.base import DataAdapter
from storopt.ingestion.cache import DataCache

_PLANT_EIC = "45W000000000023U"    # Horns Rev 1 registered resource EIC
_PSR_TYPE_OFFSHORE = "B18"          # ENTSO-E PSR code for offshore wind
_ENTSOE_AREA = "DK_1"              # entsoe-py area code for West Denmark / DK1
_MAX_CHUNK_DAYS = 364               # ENTSO-E A73 max range per request (< 1 year)

# Patterns to match the HR1 column in the ENTSO-E per-plant response.
# The platform uses the plant's registered name, which may vary slightly.
_HR1_NAME_PATTERNS = (
    "45w000000000023u",    # EIC (lower)
    "horns rev a",
    "horns rev 1",
    "horns reva",
    "hr1",
)


class EntsoeGenerationAdapter(DataAdapter):
    """
    Fetch HR1 actual generation from a pre-fetched parquet file or the ENTSO-E REST API.

    When source is None or an empty string, the adapter fetches live data from
    the ENTSO-E Transparency Platform (ENTSOE_SECURITY_TOKEN must be set in .env).
    Results are written to cache_dir as parquet after each successful API fetch,
    so subsequent calls for the same range are served from cache.

    Parameters
    ----------
    source:
        Path to a pre-fetched parquet file or directory of monthly parquet files.
        Pass None or "" to use the live REST API.
    cache_dir:
        Directory for the resampled hourly parquet cache.
    plant_eic:
        Registered resource EIC of the plant (default: HR1 = 45W000000000023U).
    gen_column:
        Output column name for generation (default: 'hr1_generation_mw').
    """

    def __init__(
        self,
        source: str | Path | None,
        cache_dir: str | Path,
        plant_eic: str = _PLANT_EIC,
        gen_column: str = "hr1_generation_mw",
    ) -> None:
        src_str = str(source).strip() if source is not None else ""
        self._source = Path(src_str) if src_str else None
        self._cache = DataCache(cache_dir, "entsoe_gen")
        self._plant_eic = plant_eic
        self._gen_column = gen_column

    def fetch(self, start: date, end: date) -> pd.DataFrame:
        cached = self._cache.get(start, end)
        if cached is not None:
            return cached

        raw = self._load_raw(start, end)
        hourly = self._to_hourly(raw, start, end)
        self._cache.put(hourly, start, end)
        return hourly

    # ----- internals -----

    def _load_raw(self, start: date, end: date) -> pd.DataFrame:
        if self._source is None:
            return self._fetch_live(start, end)

        if not self._source.exists():
            raise FileNotFoundError(
                f"ENTSO-E generation source not found: {self._source}\n"
                "Either set config.ingestion.generation_file to a valid parquet path, "
                "or leave it empty to fetch live data via ENTSOE_SECURITY_TOKEN."
            )

        if self._source.is_file():
            df = pd.read_parquet(self._source)
        else:
            files = sorted(self._source.glob("*.parquet"))
            if not files:
                raise FileNotFoundError(f"No parquet files found in {self._source}")
            frames = []
            for f in files:
                df_part = pd.read_parquet(f)
                if "delivery_ts_utc" in df_part.columns:
                    df_part["delivery_ts_utc"] = pd.to_datetime(df_part["delivery_ts_utc"], utc=True)
                    mask = (
                        (df_part["delivery_ts_utc"].dt.date >= start)
                        & (df_part["delivery_ts_utc"].dt.date <= end)
                    )
                    if mask.any():
                        frames.append(df_part.loc[mask])
            if not frames:
                raise RuntimeError(
                    f"No ENTSO-E generation data found for {start}–{end} in {self._source}"
                )
            df = pd.concat(frames, ignore_index=True)

        if "delivery_ts_utc" not in df.columns:
            raise ValueError("ENTSO-E parquet missing 'delivery_ts_utc' column")

        gen_col = next(
            (c for c in ["generation_mw", "ActualGenerationOutput", "quantity"] if c in df.columns),
            None,
        )
        if gen_col is None:
            raise ValueError(f"Cannot find generation column in {list(df.columns)}")

        df["delivery_ts_utc"] = pd.to_datetime(df["delivery_ts_utc"], utc=True)
        df = df[["delivery_ts_utc", gen_col]].rename(columns={gen_col: "generation_mw"})
        df["generation_mw"] = pd.to_numeric(df["generation_mw"], errors="coerce")
        return df

    # ----- live REST API -----

    def _fetch_live(self, start: date, end: date) -> pd.DataFrame:
        """Fetch from ENTSO-E REST API, chunked into ≤1-year requests."""
        try:
            from entsoe import EntsoePandasClient  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "entsoe-py is required for live ENTSO-E fetching but is not installed.\n"
                "Run: pip install entsoe-py"
            ) from exc

        token = os.environ.get("ENTSOE_SECURITY_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "ENTSOE_SECURITY_TOKEN is not set.\n"
                "Add it to your .env file or set it in the shell, or set "
                "config.ingestion.generation_file to a pre-fetched parquet file."
            )

        client = EntsoePandasClient(api_key=token)
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=_MAX_CHUNK_DAYS))
            print(f"    ENTSO-E: fetching {cursor} → {chunk_end} ...", end=" ", flush=True)
            chunk = self._fetch_chunk(client, cursor, chunk_end)
            print(f"{len(chunk)} rows")
            frames.append(chunk)
            cursor = chunk_end + timedelta(days=1)

        df = pd.concat(frames, ignore_index=True)
        return (
            df.drop_duplicates(subset=["delivery_ts_utc"])
            .sort_values("delivery_ts_utc")
            .reset_index(drop=True)
        )

    def _fetch_chunk(self, client, start: date, end: date) -> pd.DataFrame:
        """Fetch one chunk (≤1 year) of generation data from the ENTSO-E API."""
        # entsoe-py wants tz-aware Timestamps; end is inclusive so add 23 h
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)

        try:
            df_raw = client.query_generation_per_plant(
                country_code=_ENTSOE_AREA,
                start=start_ts,
                end=end_ts,
                psr_type=_PSR_TYPE_OFFSHORE,
            )
        except Exception as exc:
            raise RuntimeError(
                f"ENTSO-E query_generation_per_plant failed for {start}–{end}: {exc}"
            ) from exc

        hr1_col = self._find_plant_column(df_raw)

        # entsoe-py may return a MultiIndex column DataFrame; pick the right level
        series = df_raw[hr1_col]
        if isinstance(series, pd.DataFrame):
            # MultiIndex case: take the first matching sub-column (Actual vs. Forecast)
            series = series.iloc[:, 0]

        df = series.rename("generation_mw").reset_index()
        df.columns = pd.Index(["delivery_ts_utc", "generation_mw"])
        df["delivery_ts_utc"] = pd.to_datetime(df["delivery_ts_utc"], utc=True)
        df["generation_mw"] = pd.to_numeric(df["generation_mw"], errors="coerce")
        return df

    def _find_plant_column(self, df: pd.DataFrame):
        """
        Identify the column corresponding to our plant EIC or name.

        entsoe-py returns columns either as strings (plant names) or as tuples
        (psr_type, plant_name) depending on the document structure.
        """
        cols = df.columns.tolist()
        eic_lower = self._plant_eic.lower()

        for col in cols:
            col_str = str(col).lower()
            if eic_lower in col_str or any(p in col_str for p in _HR1_NAME_PATTERNS):
                return col

        raise RuntimeError(
            f"Could not find column for plant EIC '{self._plant_eic}' in ENTSO-E response.\n"
            f"Available columns: {cols}\n"
            "Update _HR1_NAME_PATTERNS in entsoe.py if the platform uses a different name."
        )

    def _to_hourly(self, df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        """Resample 15-min data to hourly mean, filter to [start, end]."""
        df = df.set_index("delivery_ts_utc").sort_index()
        df.index = df.index.floor("h")
        hourly = df.groupby(level=0)["generation_mw"].mean().reset_index()
        hourly.columns = pd.Index(["delivery_ts_utc", self._gen_column])
        mask = (
            (hourly["delivery_ts_utc"].dt.date >= start)
            & (hourly["delivery_ts_utc"].dt.date <= end)
        )
        return hourly.loc[mask].reset_index(drop=True)
