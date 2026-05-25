from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from storopt.config.schema import RunConfig

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG = _PACKAGE_ROOT / "configs" / "default.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} must be a YAML mapping, got {type(data).__name__}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_dot_overrides(raw: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply dot-notation overrides, e.g. {"scenarios.method": "naive"}."""
    result = dict(raw)
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return result


def load_config(path: str | Path | None = None, **overrides: Any) -> RunConfig:
    """
    Load and validate a RunConfig.

    Merges default.yaml → case YAML (if given) → dot-notation keyword overrides.

    Parameters
    ----------
    path:
        Path to a case-specific YAML (e.g. "configs/horns_rev1.yaml").
        Only keys present in the file override the defaults.
    **overrides:
        Dot-notation overrides, e.g. scenarios__method="naive" or pass as a dict
        with load_config(path, **{"scenarios.method": "naive"}).

    Examples
    --------
    >>> cfg = load_config("configs/horns_rev1.yaml")
    >>> cfg = load_config("configs/horns_rev1.yaml", **{"scenarios.method": "naive"})
    """
    raw = _load_yaml(_DEFAULT_CONFIG)

    if path is not None:
        case_raw = _load_yaml(Path(path))
        raw = _deep_merge(raw, case_raw)

    if overrides:
        # Accept both "scenarios.method" and "scenarios__method" notation
        normalised = {k.replace("__", "."): v for k, v in overrides.items()}
        raw = _apply_dot_overrides(raw, normalised)

    return RunConfig.model_validate(raw)
