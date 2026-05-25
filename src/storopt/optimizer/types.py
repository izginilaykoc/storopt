from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class OptimizationResult:
    # First-stage (non-anticipative)
    da_bids: np.ndarray          # shape (T,) — DA net position [MW]

    # Second-stage (scenario-indexed)
    id_trades: np.ndarray        # shape (S, T)
    charge: np.ndarray           # shape (S, T) — charge power [MW]
    discharge: np.ndarray        # shape (S, T) — discharge power [MW]
    soc: np.ndarray              # shape (S, T) — state of charge [MWh]

    # Financials
    scenario_profits: np.ndarray  # shape (S,) — profit per scenario [€]
    probabilities: np.ndarray     # shape (S,) — scenario probabilities (sum=1)
    expected_profit: float        # probability-weighted expected profit [€]

    # Solver metadata
    solve_status: str
    solve_time_s: float

    # Optional extensions (present only when enabled in config)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def n_scenarios(self) -> int:
        return self.id_trades.shape[0]

    @property
    def n_periods(self) -> int:
        return self.da_bids.shape[-1]
