"""
Deterministic optimizer: collapses the scenario bundle to its probability-weighted
mean, then solves a single-scenario MILP. Used for sanity checks and EEV computation.
"""

from __future__ import annotations

import numpy as np

from storopt.config.schema import RunConfig
from storopt.optimizer.base import Optimizer
from storopt.optimizer.milp import StochasticMILP
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle


class DeterministicOptimizer(Optimizer):
    """Solve the mean-scenario (EEV) problem."""

    def solve(self, bundle: ScenarioBundle, config: RunConfig) -> OptimizationResult:
        weights = bundle.probabilities
        mean_bundle = ScenarioBundle(
            da_prices=np.average(bundle.da_prices, axis=0, weights=weights)[np.newaxis, :],
            id_prices=np.average(bundle.id_prices, axis=0, weights=weights)[np.newaxis, :],
            res_generation=np.average(bundle.res_generation, axis=0, weights=weights)[np.newaxis, :],
            probabilities=np.array([1.0]),
            target_date=bundle.target_date,
        )
        return StochasticMILP().solve(mean_bundle, config)
