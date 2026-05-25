from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataAdapter(ABC):
    """
    Fetch market/generation/weather data for a date range.

    Implementations handle caching internally. The returned DataFrame
    always contains a UTC-timezone 'delivery_ts_utc' column plus
    one or more value columns specific to the adapter.
    """

    @abstractmethod
    def fetch(self, start: date, end: date) -> pd.DataFrame:
        """
        Fetch data for the closed interval [start, end] (inclusive both ends).

        Parameters
        ----------
        start, end:
            UTC calendar dates. The returned DataFrame may contain rows
            outside this range if a cache file covers a wider window;
            callers must not assume the result is pre-filtered.

        Returns
        -------
        pd.DataFrame with at least 'delivery_ts_utc' (UTC-aware datetime).
        """
