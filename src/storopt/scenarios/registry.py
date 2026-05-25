from __future__ import annotations

from typing import Any

from storopt.scenarios.base import ScenarioGenerator
from storopt.scenarios.knn import KNNScenarioGenerator
from storopt.scenarios.naive import NaiveScenarioGenerator
from storopt.scenarios.sarimax import SarimaxScenarioGenerator

REGISTRY: dict[str, type[ScenarioGenerator]] = {
    "knn": KNNScenarioGenerator,
    "naive": NaiveScenarioGenerator,
    "sarimax": SarimaxScenarioGenerator,
}


def get_generator(method: str, **params: Any) -> ScenarioGenerator:
    """
    Instantiate a ScenarioGenerator by name.

    Parameters
    ----------
    method:
        Registry key (e.g. "knn", "naive").
    **params:
        Forwarded to the generator constructor (from config.scenarios.params).

    Example
    -------
    >>> gen = get_generator("knn", probability_mode="softmax", softmax_temperature=1.0)
    """
    if method not in REGISTRY:
        raise KeyError(f"Unknown scenario method {method!r}. Available: {list(REGISTRY)}")
    return REGISTRY[method](**params)
