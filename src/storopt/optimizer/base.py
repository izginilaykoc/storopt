from __future__ import annotations

from abc import ABC, abstractmethod

from storopt.config.schema import RunConfig
from storopt.optimizer.types import OptimizationResult
from storopt.scenarios.types import ScenarioBundle


class Optimizer(ABC):
    @abstractmethod
    def solve(self, bundle: ScenarioBundle, config: RunConfig) -> OptimizationResult:
        """
        Solve the dispatch optimization for one day.

        Parameters
        ----------
        bundle:
            Scenario bundle produced by a ScenarioGenerator.
        config:
            Full run configuration (bess, market, solver, optimizer sub-configs used).

        Returns
        -------
        OptimizationResult with dispatch, SOC, financials, and solve metadata.
        """
