"""
Open-Meteo Historical-Forecast adapter.

Wraps the fetch-and-cache logic from the old repo, adapted to the DataAdapter
interface. Uses archived NWP forecasts (not actual observations) — this is
the correct leakage-safe choice for backtesting the KNN generator.

API: https://historical-forecast-api.open-meteo.com/v1/forecast

Output columns:
  delivery_ts_utc           datetime64[ns, UTC]
  weather_wind_speed_80m    float64
  weather_wind_speed_100m   float64
  weather_wind_direction_80m float64
  weather_temperature_2m    float64
  weather_surface_pressure  float64
  ... (one column per requested variable, prefixed with 'weather_')
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

_API_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
_TIMEOUT = 120
_RETRIES = 3
_CHUNK_DAYS = 366

# Default variables for wind-offshore plant (HR1)
WIND_VARIABLES: tuple[str, ...] = (
    "temperature_2m",
    "surface_pressure",
    "cloud_cover",
    "wind_speed_10m",
    "wind_speed_80m",
    "wind_speed_100m",
    "wind_speed_120m",
    "wind_direction_80m",
    "wind_gusts_10m",
)


class OpenMeteoAdapter(DataAdapter):
    """
    Fetch archived NWP weather for a fixed coordinate pair.

    Parameters
    ----------
    latitude, longitude:
        Plant coordinates.
    cache_dir:
        Where to store raw JSON chunk files and the combined parquet.
    variables:
        Tuple of Open-Meteo hourly variable names to fetch.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        cache_dir: str | Path,
        variables: tuple[str, ...] = WIND_VARIABLES,
    ) -> None:
        self._lat = float(latitude)
        self._lon = float(longitude)
        self._variables = tuple(variables)
        self._cache = DataCache(cache_dir, "open_meteo")
        self._raw_dir = Path(cache_dir) / "open_meteo_raw"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()

    def fetch(self, start: date, end: date) -> pd.DataFrame:
        cached = self._cache.get(start, end)
        if cached is not None:
            return cached

        chunks = list(self._iter_chunks(start, end))
        frames = [self._fetch_chunk(cs, ce) for cs, ce in chunks]
        df = pd.concat(frames, ignore_index=True)
        df = (
            df.sort_values("delivery_ts_utc")
            .drop_duplicates(subset=["delivery_ts_utc"], keep="first")
            .reset_index(drop=True)
        )

        # Prefix all weather columns
        weather_cols = [c for c in df.columns if c != "delivery_ts_utc"]
        df = df.rename(columns={c: f"weather_{c}" for c in weather_cols})

        # Filter to requested range
        mask = (
            (df["delivery_ts_utc"].dt.date >= start)
            & (df["delivery_ts_utc"].dt.date <= end)
        )
        df = df.loc[mask].reset_index(drop=True)

        self._cache.put(df, start, end)
        return df

    # ----- internals -----

    def _iter_chunks(self, start: date, end: date):
        cursor = pd.Timestamp(start)
        delta = pd.Timedelta(days=_CHUNK_DAYS)
        end_ts = pd.Timestamp(end)
        while cursor.date() <= end:
            chunk_end = min(end_ts, cursor + delta - pd.Timedelta(days=1))
            yield cursor.date(), chunk_end.date()
            cursor = chunk_end + pd.Timedelta(days=1)

    def _chunk_cache_path(self, start: date, end: date) -> Path:
        import hashlib

        var_tag = "_".join(self._variables)
        if len(var_tag) > 80:
            var_tag = hashlib.sha1(var_tag.encode()).hexdigest()[:12]
        coord_tag = f"{self._lat:.4f}N_{self._lon:.4f}E"
        return self._raw_dir / f"om_{coord_tag}_{start}_{end}_{var_tag}.json"

    def _fetch_chunk(self, start: date, end: date) -> pd.DataFrame:
        cache_path = self._chunk_cache_path(start, end)
        if cache_path.exists() and cache_path.stat().st_size > 256:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            params = {
                "latitude": f"{self._lat:.6f}",
                "longitude": f"{self._lon:.6f}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": ",".join(self._variables),
                "timezone": "UTC",
                "windspeed_unit": "ms",
            }
            last_exc: Exception | None = None
            for attempt in range(_RETRIES + 1):
                try:
                    resp = self._session.get(_API_URL, params=params, timeout=_TIMEOUT)
                    resp.raise_for_status()
                    payload = resp.json()
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < _RETRIES:
                        time.sleep(3 * (attempt + 1))
            else:
                raise RuntimeError(f"Open-Meteo fetch failed: {last_exc}") from last_exc
            cache_path.write_text(json.dumps(payload), encoding="utf-8")

        return self._parse(payload)

    def _parse(self, payload: dict) -> pd.DataFrame:
        hourly = payload.get("hourly") or {}
        if "time" not in hourly:
            raise RuntimeError(f"Open-Meteo response missing hourly.time: {list(payload.keys())}")
        df = pd.DataFrame(hourly)
        df["delivery_ts_utc"] = pd.to_datetime(df["time"], utc=True)
        df = df.drop(columns=["time"])
        for col in df.columns:
            if col != "delivery_ts_utc" and df[col].dtype != "float64":
                df[col] = df[col].astype("float64")
        return df
