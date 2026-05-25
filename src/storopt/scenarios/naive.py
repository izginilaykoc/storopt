"""
Naive (deterministic mean) scenario generator.

Collapses history into a single scenario representing the probability-weighted
mean of the historical distribution for each period. Useful as a sanity check
baseline and for computing EEV in VSS analysis.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from storopt.scenarios.base import ScenarioGenerator
from storopt.scenarios.types import ScenarioBundle

_DA_COL = "da_eur_mwh"
_ID_COL = "id_eur_mwh"


class NaiveScenarioGenerator(ScenarioGenerator):
    """
    Single-scenario generator: per-period mean of history.

    Always produces n_scenarios=1 regardless of the requested count,
    since there is no distribution to sample from — it IS the expectation.
    """

    def __init__(self, **params: Any) -> None:
        self._da_col: str = params.get("da_column", _DA_COL)
        self._id_col: str = params.get("id_column", _ID_COL)
        self._gen_col: str = params.get("gen_column", "hr1_generation_mw")
        self._panel: pd.DataFrame | None = None

    def fit(self, history: pd.DataFrame) -> None:
        if "delivery_ts_utc" not in history.columns:
            raise ValueError("history panel must contain 'delivery_ts_utc'")
        panel = history.copy()
        panel["delivery_ts_utc"] = pd.to_datetime(panel["delivery_ts_utc"], utc=True)
        self._panel = panel

    def generate(self, target_date: date, n_scenarios: int) -> ScenarioBundle:
        if self._panel is None:
            raise RuntimeError("Call fit() before generate()")

        # Exclude target_date to prevent leakage (fit() may have received the full panel)
        panel = self._panel[self._panel["delivery_ts_utc"].dt.date < target_date].copy()
        panel["_hour"] = panel["delivery_ts_utc"].dt.hour

        # Per-hour mean over history days only
        hourly = panel.groupby("_hour", as_index=True)[[self._da_col, self._id_col, self._gen_col]].mean()
        hourly = hourly.sort_index()

        da_mean = hourly[self._da_col].to_numpy(dtype=float)
        id_mean = hourly[self._id_col].to_numpy(dtype=float)
        gen_mean = hourly[self._gen_col].to_numpy(dtype=float)

        return ScenarioBundle(
            da_prices=da_mean[np.newaxis, :],
            id_prices=id_mean[np.newaxis, :],
            res_generation=gen_mean[np.newaxis, :],
            probabilities=np.array([1.0]),
            target_date=target_date,
            generation_method="naive",
            scenario_labels=["history_mean"],
        )
