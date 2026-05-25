from __future__ import annotations

from storopt.optimizer.base import Optimizer
from storopt.optimizer.deterministic import DeterministicOptimizer
from storopt.optimizer.milp import StochasticMILP

REGISTRY: dict[str, type[Optimizer]] = {
    "stochastic_milp": StochasticMILP,
    "deterministic": DeterministicOptimizer,
}


def get_optimizer(method: str) -> Optimizer:
    if method not in REGISTRY:
        raise KeyError(f"Unknown optimizer {method!r}. Available: {list(REGISTRY)}")
    return REGISTRY[method]()
