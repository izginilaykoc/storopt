from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np


@dataclass
class ScenarioBundle:
    """
    Optimizer-facing scenario set for one trading day.

    All price/generation arrays have shape (S, T) where S = n_scenarios,
    T = n_periods per day (24 for DK1 hourly).

    Each row s is one coherent historical day — DA[s,:], ID[s,:], gen[s,:]
    all originate from the same source day. No mixing across days.
    """

    da_prices: np.ndarray         # (S, T) — DA price per period [€/MWh]
    id_prices: np.ndarray         # (S, T) — ID proxy price per period [€/MWh]
    res_generation: np.ndarray    # (S, T) — renewable generation [MW]
    probabilities: np.ndarray     # (S,)   — scenario weights, must sum to 1
    target_date: date

    generation_method: str = ""
    scenario_labels: list[str] = field(default_factory=list)

    @property
    def n_scenarios(self) -> int:
        return self.da_prices.shape[0]

    @property
    def n_periods(self) -> int:
        return self.da_prices.shape[1]

    def validate(self) -> None:
        assert self.da_prices.shape == self.id_prices.shape, "da/id shape mismatch"
        assert self.da_prices.shape == self.res_generation.shape, "da/gen shape mismatch"
        assert len(self.probabilities) == self.n_scenarios, "probabilities length mismatch"
        total = self.probabilities.sum()
        assert abs(total - 1.0) < 1e-6, f"probabilities sum to {total}, not 1.0"
        assert not np.isnan(self.da_prices).any(), "NaN in da_prices"
        assert not np.isnan(self.id_prices).any(), "NaN in id_prices"
        assert not np.isnan(self.res_generation).any(), "NaN in res_generation"
