from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from storopt.scenarios.types import ScenarioBundle


class ScenarioGenerator(ABC):
    """
    Abstract base for all scenario generation methods.

    Workflow
    --------
    generator = get_generator("knn", **params)
    generator.fit(history_df)               # panel up to (not incl.) target_date
    bundle = generator.generate(date, n)    # returns ScenarioBundle (S, T)
    """

    @abstractmethod
    def fit(self, history: pd.DataFrame) -> None:
        """
        Learn from historical panel data.

        Parameters
        ----------
        history:
            Canonical panel DataFrame with at minimum columns:
            delivery_ts_utc, da_eur_mwh, id_eur_mwh, <generation_col>
            plus any weather columns used as features.
            May include target_date rows (the KNN generator reads target-day
            NWP weather for its query vector; leakage guards inside generate()
            exclude target-day actual prices/generation from candidates).
        """

    @abstractmethod
    def generate(self, target_date: date, n_scenarios: int) -> ScenarioBundle:
        """
        Generate n_scenarios scenarios for target_date.

        The target date's actual DA/ID/generation must NOT be used as input
        (only NWP weather forecasts for that date are leakage-safe).
        """
