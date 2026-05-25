"""
Simple Parquet cache keyed by adapter name + date range.

Cache hit logic: if any existing file covers the full requested [start, end],
slice and return it — no re-fetch needed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class DataCache:
    def __init__(self, cache_dir: str | Path, adapter_name: str) -> None:
        self.cache_dir = Path(cache_dir)
        self.adapter_name = adapter_name
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, start: date, end: date) -> pd.DataFrame | None:
        """Return cached data covering [start, end], or None if no covering file exists."""
        for path in sorted(self.cache_dir.glob(f"{self.adapter_name}_*.parquet")):
            try:
                df = pd.read_parquet(path)
            except Exception:
                continue
            if "delivery_ts_utc" not in df.columns:
                continue
            df["delivery_ts_utc"] = pd.to_datetime(df["delivery_ts_utc"], utc=True)
            df_start = df["delivery_ts_utc"].min().date()
            df_end = df["delivery_ts_utc"].max().date()
            if df_start <= start and df_end >= end:
                mask = (
                    (df["delivery_ts_utc"].dt.date >= start)
                    & (df["delivery_ts_utc"].dt.date <= end)
                )
                return df.loc[mask].reset_index(drop=True)
        return None

    def put(self, df: pd.DataFrame, start: date, end: date) -> Path:
        """Write DataFrame to cache. Returns the path written."""
        path = self.cache_dir / f"{self.adapter_name}_{start}_{end}.parquet"
        df.to_parquet(path, index=False)
        return path
