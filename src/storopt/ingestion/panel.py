"""
Canonical panel builder — inner-joins data from all adapters on delivery_ts_utc.

Output schema (one row per UTC hour):
  delivery_ts_utc     datetime64[ns, UTC]
  da_eur_mwh          float64
  id_eur_mwh          float64
  hr1_generation_mw   float64
  weather_*           float64   (one per Open-Meteo variable, prefixed)

Generation source (selected automatically):
  - If config.ingestion.generation_file is set: read from pre-fetched parquet (exact HR1 data).
  - Otherwise: fetch live from Energinet GenerationProdTypeExchange (DK1 offshore aggregate
    scaled to HR1 by capacity share 160/1592 MW). No token required.
  Note: ENTSO-E per-plant A73 data is not published for Danish plants.

Strict-alignment rule: inner join on delivery_ts_utc. Any hour missing from
any source is dropped. We never forward-fill or fabricate values.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from storopt.config.schema import IngestionConfig
from storopt.ingestion.adapters.energinet import (
    EnerginetDAAdapter,
    EnerginetGenerationAdapter,
    EnerginetIDAdapter,
)
from storopt.ingestion.adapters.entsoe import EntsoeGenerationAdapter
from storopt.ingestion.adapters.open_meteo import OpenMeteoAdapter, WIND_VARIABLES


def build_panel(start: date, end: date, config: IngestionConfig) -> pd.DataFrame:
    """
    Fetch and join all data sources into the canonical hourly UTC panel.

    Parameters
    ----------
    start, end:
        Inclusive UTC date range.
    config:
        IngestionConfig with API / path / cache settings.

    Returns
    -------
    pd.DataFrame with delivery_ts_utc, da_eur_mwh, id_eur_mwh,
    hr1_generation_mw, weather_* columns.
    Rows with any missing value across sources are dropped (inner join).
    """
    cache_dir = Path(config.cache_dir)

    # 1. Fetch each source
    da_df = EnerginetDAAdapter(cache_dir, config.area).fetch(start, end)
    id_df = EnerginetIDAdapter(cache_dir, config.area).fetch(start, end)

    weather_df = OpenMeteoAdapter(
        latitude=config.weather_lat,
        longitude=config.weather_lon,
        cache_dir=cache_dir,
        variables=WIND_VARIABLES,
    ).fetch(start, end)

    if config.generation_file:
        # Read from pre-fetched parquet (exact plant data)
        gen_df = EntsoeGenerationAdapter(
            source=config.generation_file,
            cache_dir=cache_dir,
        ).fetch(start, end)
    else:
        # Live fetch: Energinet DK1 offshore aggregate scaled to HR1 capacity share.
        # ENTSO-E per-plant (A73) data is not published for Danish plants.
        gen_df = EnerginetGenerationAdapter(cache_dir=cache_dir).fetch(start, end)

    # 2. Ensure all timestamps are UTC
    for df in (da_df, id_df, weather_df, gen_df):
        df["delivery_ts_utc"] = pd.to_datetime(df["delivery_ts_utc"], utc=True)

    # 3. Keep only full UTC hours (floor any sub-hourly timestamps)
    for df in (da_df, id_df, weather_df, gen_df):
        df["delivery_ts_utc"] = df["delivery_ts_utc"].dt.floor("h")

    # 4. Inner join — any missing hour from any source is dropped
    panel = da_df.merge(id_df, on="delivery_ts_utc", how="inner")
    panel = panel.merge(weather_df, on="delivery_ts_utc", how="inner")
    panel = panel.merge(gen_df, on="delivery_ts_utc", how="inner")

    panel = (
        panel.sort_values("delivery_ts_utc")
        .drop_duplicates(subset=["delivery_ts_utc"], keep="first")
        .reset_index(drop=True)
    )

    return panel
