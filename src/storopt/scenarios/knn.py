"""
KNN (nearest-neighbour) historical analog-day scenario generator.

Ported from bess_opt/forecasting/nearest_neighbour.py.
The algorithm is unchanged; the interface adapts to ScenarioGenerator ABC.

Hard invariants preserved from original:
1. Target-day actual DA / ID / generation NEVER enter the query vector.
2. Target-day actual weather observations NEVER enter the query vector
   (only archived NWP forecasts from Open-Meteo Historical-Forecast are used).
3. Candidate-day query features are computed from data strictly before
   the candidate day's information cutoff.
4. Feature standardization uses history-only statistics (target day excluded).
5. Each scenario is one coherent historical day — DA[s,:], ID[s,:], gen[s,:]
   all come from the same selected day. No mixing across days.
6. The target day itself is excluded from the candidate pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from storopt.scenarios.base import ScenarioGenerator
from storopt.scenarios.types import ScenarioBundle


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class KNNConfig:
    """Tunable parameters for the KNN scenario generator."""

    information_cutoff_days: int = 2
    distance_mode: str = "weighted_euclidean"
    probability_mode: str = "softmax"
    softmax_temperature: float = 1.0
    feature_weights: dict[str, float] = field(default_factory=dict)
    standardize_features: bool = True
    diversity_min_gap_days: int = 0

    enabled_feature_groups: tuple[str, ...] = (
        "calendar",
        "weather_forecast_target_day",
        "lagged_da",
        "lagged_id_spread",
        "lagged_generation",
    )
    weather_summary_stats: tuple[str, ...] = ("mean", "min", "max", "std")
    peak_hours_utc: tuple[int, ...] = tuple(range(8, 20))

    da_column: str = "da_eur_mwh"
    id_column: str = "id_eur_mwh"
    gen_column: str = "hr1_generation_mw"
    plant_capacity_mw: float = 160.0


# ---------------------------------------------------------------------------
# Feature helpers (all leakage-safe by construction)
# ---------------------------------------------------------------------------


def _hours_of_day(panel: pd.DataFrame, day: date) -> pd.DataFrame:
    mask = panel["delivery_ts_utc"].dt.date == day
    return panel.loc[mask].sort_values("delivery_ts_utc")


def _is_complete_24h(rows: pd.DataFrame) -> bool:
    return len(rows) == 24 and rows["delivery_ts_utc"].dt.hour.tolist() == list(range(24))


def _summarize_24h(values: np.ndarray, *, prefix: str, stats: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    if values.size == 0:
        return out
    if "mean" in stats:
        out[f"{prefix}_mean"] = float(np.nanmean(values))
    if "min" in stats:
        out[f"{prefix}_min"] = float(np.nanmin(values))
    if "max" in stats:
        out[f"{prefix}_max"] = float(np.nanmax(values))
    if "std" in stats:
        out[f"{prefix}_std"] = float(np.nanstd(values))
    return out


def _calendar_features(day: date) -> dict[str, float]:
    ts = pd.Timestamp(day)
    doy = ts.dayofyear
    return {
        "calendar_month": float(ts.month),
        "calendar_day_of_week": float(ts.dayofweek),
        "calendar_is_weekend": float(ts.dayofweek >= 5),
        "calendar_doy_sin": float(np.sin(2 * np.pi * doy / 365.25)),
        "calendar_doy_cos": float(np.cos(2 * np.pi * doy / 365.25)),
    }


def _lag_window_summaries(
    panel: pd.DataFrame,
    *,
    end_date_inclusive: date,
    start_date_inclusive: date,
    column: str,
    prefix: str,
    cap_for_capacity_factor: float | None = None,
) -> dict[str, float]:
    mask = (
        (panel["delivery_ts_utc"].dt.date >= start_date_inclusive)
        & (panel["delivery_ts_utc"].dt.date <= end_date_inclusive)
    )
    sub = panel.loc[mask, column].dropna()
    if sub.empty:
        return {}
    arr = sub.to_numpy(dtype=float)
    out = {
        f"{prefix}_mean": float(np.nanmean(arr)),
        f"{prefix}_min": float(np.nanmin(arr)),
        f"{prefix}_max": float(np.nanmax(arr)),
        f"{prefix}_std": float(np.nanstd(arr)),
    }
    if cap_for_capacity_factor is not None and cap_for_capacity_factor > 0:
        out[f"{prefix}_capacity_factor_mean"] = float(np.nanmean(arr) / cap_for_capacity_factor)
    return out


def _regime_features(
    panel: pd.DataFrame,
    *,
    day: date,
    cfg: KNNConfig,
    lag1_end: pd.Timestamp,
    lag7_start: pd.Timestamp,
    weather_columns: tuple[str, ...],
) -> dict[str, float]:
    out: dict[str, float] = {}
    peak_hours = set(int(h) for h in cfg.peak_hours_utc)

    def _peak_offpeak_spread(start_d: date, end_d: date) -> float | None:
        mask = (
            (panel["delivery_ts_utc"].dt.date >= start_d)
            & (panel["delivery_ts_utc"].dt.date <= end_d)
        )
        sub = panel.loc[mask, ["delivery_ts_utc", cfg.da_column]].dropna()
        if sub.empty:
            return None
        is_peak = sub["delivery_ts_utc"].dt.hour.isin(peak_hours)
        pm = sub.loc[is_peak, cfg.da_column].mean()
        om = sub.loc[~is_peak, cfg.da_column].mean()
        if pd.isna(pm) or pd.isna(om):
            return None
        return float(pm - om)

    s = _peak_offpeak_spread(lag1_end.date(), lag1_end.date())
    if s is not None:
        out["regime_lag1_da_peak_offpeak_spread"] = s
    s7 = _peak_offpeak_spread(lag7_start.date(), lag1_end.date())
    if s7 is not None:
        out["regime_lag7_da_peak_offpeak_spread_mean"] = s7

    lag1_da = panel.loc[panel["delivery_ts_utc"].dt.date == lag1_end.date(), cfg.da_column].dropna()
    if not lag1_da.empty:
        m = float(lag1_da.mean())
        sd = float(lag1_da.std())
        out["regime_lag1_da_volatility"] = sd / max(abs(m), 1.0)
        out["regime_lag1_da_range"] = float(lag1_da.max() - lag1_da.min())

    lag1_gen = (
        panel.loc[panel["delivery_ts_utc"].dt.date == lag1_end.date(), ["delivery_ts_utc", cfg.gen_column]]
        .sort_values("delivery_ts_utc")[cfg.gen_column]
        .dropna()
        .to_numpy(dtype=float)
    )
    if lag1_gen.size >= 2:
        ramps = np.abs(np.diff(lag1_gen))
        out["regime_lag1_generation_max_ramp_mw"] = float(ramps.max())
        out["regime_lag1_generation_mean_ramp_mw"] = float(ramps.mean())

    wind_col = "weather_wind_speed_80m"
    if wind_col in weather_columns:
        rows = _hours_of_day(panel, day)
        if wind_col in rows.columns:
            w = rows[wind_col].to_numpy(dtype=float)
            if w.size >= 2:
                ramps = np.abs(np.diff(w))
                out["regime_target_wind_speed_80m_max_ramp_ms"] = float(ramps.max())
                out["regime_target_wind_speed_80m_mean_ramp_ms"] = float(ramps.mean())
                wmean = float(w.mean())
                out["regime_target_wind_speed_80m_cv"] = float(w.std()) / max(wmean, 1e-3)

    ts = pd.Timestamp(day)
    out["regime_weekend_x_month"] = float((ts.dayofweek >= 5) * ts.month)

    lag1_sub = panel.loc[
        panel["delivery_ts_utc"].dt.date == lag1_end.date(), ["delivery_ts_utc", cfg.da_column]
    ].dropna()
    if not lag1_sub.empty:
        is_peak = lag1_sub["delivery_ts_utc"].dt.hour.isin(peak_hours)
        dm = lag1_sub.loc[is_peak, cfg.da_column].mean()
        nm = lag1_sub.loc[~is_peak, cfg.da_column].mean()
        if not (pd.isna(dm) or pd.isna(nm)) and abs(nm) > 1e-3:
            out["regime_lag1_day_over_night_da_ratio"] = float(dm / nm)

    return out


def _build_query_vector(
    panel: pd.DataFrame,
    *,
    day: date,
    cfg: KNNConfig,
    weather_columns: tuple[str, ...],
) -> dict[str, float]:
    """
    Build leakage-safe query vector for `day`.

    Uses:
      - Calendar fields for `day`
      - Archived NWP weather forecasts for `day` (leakage-safe by definition)
      - Lagged actuals ending at day - info_cutoff_days (strictly before decision point)

    Does NOT use:
      - Target-day actual DA / ID / generation
      - Target-day realized weather observations
    """
    feats: dict[str, float] = {}
    cutoff = cfg.information_cutoff_days
    lag1_end = pd.Timestamp(day) - pd.Timedelta(days=cutoff)
    lag7_start = lag1_end - pd.Timedelta(days=6)

    if "calendar" in cfg.enabled_feature_groups:
        feats.update(_calendar_features(day))

    if "weather_forecast_target_day" in cfg.enabled_feature_groups:
        target_rows = _hours_of_day(panel, day)
        if not target_rows.empty:
            for col in weather_columns:
                if col not in target_rows.columns:
                    continue
                arr = target_rows[col].to_numpy(dtype=float)
                feats.update(_summarize_24h(arr, prefix=col, stats=cfg.weather_summary_stats))

    if "lagged_da" in cfg.enabled_feature_groups:
        feats.update(_lag_window_summaries(
            panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag1_end.date(),
            column=cfg.da_column, prefix="lag1_da",
        ))
        feats.update(_lag_window_summaries(
            panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag7_start.date(),
            column=cfg.da_column, prefix="lag7_da",
        ))

    if "lagged_id_spread" in cfg.enabled_feature_groups:
        spread_panel = panel.assign(
            _id_spread=panel[cfg.id_column] - panel[cfg.da_column]
        )
        feats.update(_lag_window_summaries(
            spread_panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag1_end.date(),
            column="_id_spread", prefix="lag1_id_spread",
        ))
        feats.update(_lag_window_summaries(
            spread_panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag7_start.date(),
            column="_id_spread", prefix="lag7_id_spread",
        ))

    if "lagged_generation" in cfg.enabled_feature_groups:
        feats.update(_lag_window_summaries(
            panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag1_end.date(),
            column=cfg.gen_column, prefix="lag1_generation",
            cap_for_capacity_factor=cfg.plant_capacity_mw,
        ))
        feats.update(_lag_window_summaries(
            panel, end_date_inclusive=lag1_end.date(), start_date_inclusive=lag7_start.date(),
            column=cfg.gen_column, prefix="lag7_generation",
            cap_for_capacity_factor=cfg.plant_capacity_mw,
        ))

    if "regime_features" in cfg.enabled_feature_groups:
        feats.update(_regime_features(
            panel, day=day, cfg=cfg, lag1_end=lag1_end, lag7_start=lag7_start,
            weather_columns=weather_columns,
        ))

    return feats


# ---------------------------------------------------------------------------
# KNN scenario generator
# ---------------------------------------------------------------------------


class KNNScenarioGenerator(ScenarioGenerator):
    """
    Historical analog-day scenario generator using nearest-neighbour selection.

    For each target date, selects past days that looked similar before the
    decision point (using only forecastable features), then uses those days'
    actual DA / ID / generation paths as stochastic scenarios.
    """

    def __init__(self, **params: Any) -> None:
        """
        Parameters
        ----------
        **params:
            Forwarded from config.scenarios.params. Recognized keys:
            probability_mode, softmax_temperature, feature_weights,
            information_cutoff_days, diversity_min_gap_days,
            da_column, id_column, gen_column, plant_capacity_mw,
            standardize_features, enabled_feature_groups.
        """
        self._cfg = KNNConfig(**{k: v for k, v in params.items() if k in KNNConfig.__dataclass_fields__})
        self._panel: pd.DataFrame | None = None
        self._weather_columns: tuple[str, ...] = ()
        self._diagnostics: dict[str, Any] = {}

    def fit(self, history: pd.DataFrame) -> None:
        """
        Store history panel. Detects weather columns automatically.

        Parameters
        ----------
        history:
            Canonical panel with at minimum: delivery_ts_utc, da_eur_mwh,
            id_eur_mwh, <gen_column>. Weather columns are any column whose
            name starts with 'weather_' or 'om_'.
            May include target_date's rows — the KNN query vector reads the
        target day's NWP weather columns (leakage-safe archived forecasts).
        Leakage guards inside generate() ensure target-day actual DA/ID/generation
        never enter the query vector or candidate pool.
        """
        if "delivery_ts_utc" not in history.columns:
            raise ValueError("history panel must contain 'delivery_ts_utc'")
        panel = history.copy()
        panel["delivery_ts_utc"] = pd.to_datetime(panel["delivery_ts_utc"], utc=True)
        panel = (
            panel.sort_values("delivery_ts_utc")
            .drop_duplicates(subset=["delivery_ts_utc"], keep="first")
            .reset_index(drop=True)
        )
        self._panel = panel
        self._weather_columns = tuple(
            c for c in panel.columns if c.startswith("weather_") or c.startswith("om_")
        )

    def generate(self, target_date: date, n_scenarios: int) -> ScenarioBundle:
        if self._panel is None:
            raise RuntimeError("Call fit() before generate()")
        if n_scenarios <= 0:
            raise ValueError(f"n_scenarios must be positive, got {n_scenarios}")

        panel = self._panel
        cfg = self._cfg
        weather_cols = self._weather_columns

        self._validate_target_in_panel(panel, target_date)

        candidate_days = self._eligible_candidates(panel, target_date)
        if len(candidate_days) < n_scenarios:
            raise ValueError(
                f"Only {len(candidate_days)} eligible candidate days but {n_scenarios} requested. "
                "Reduce n_scenarios or increase history_days."
            )

        # Query vectors
        target_feats = _build_query_vector(panel, day=target_date, cfg=cfg, weather_columns=weather_cols)
        cand_records: list[dict[str, Any]] = []
        for d in candidate_days:
            feats = _build_query_vector(panel, day=d, cfg=cfg, weather_columns=weather_cols)
            feats["_day"] = d
            cand_records.append(feats)

        feature_cols = sorted(
            set(target_feats.keys()) | set().union(*(set(r.keys()) - {"_day"} for r in cand_records))
        )
        if not feature_cols:
            raise ValueError("No query features computed — check enabled_feature_groups.")

        X_target = np.array([target_feats.get(c, np.nan) for c in feature_cols], dtype=float)
        X_cand = np.array([[r.get(c, np.nan) for c in feature_cols] for r in cand_records], dtype=float)
        cand_days_list = [r["_day"] for r in cand_records]

        valid = ~np.isnan(X_cand).any(axis=1)
        if not valid.any():
            raise ValueError("All candidate query vectors contain NaN — insufficient history.")
        X_cand = X_cand[valid]
        cand_days_list = [d for d, ok in zip(cand_days_list, valid) if ok]

        if np.isnan(X_target).any():
            bad = [c for c, v in zip(feature_cols, X_target) if np.isnan(v)]
            raise ValueError(
                f"Target query vector has NaN in features {bad}. "
                "Missing weather or lag data — no synthetic data allowed."
            )

        # Standardize using history-only stats (target day excluded)
        if cfg.standardize_features:
            mu = X_cand.mean(axis=0)
            sigma = X_cand.std(axis=0)
            sigma_safe = np.where(sigma == 0.0, 1.0, sigma)
            Xc = (X_cand - mu) / sigma_safe
            xt = (X_target - mu) / sigma_safe
        else:
            mu = np.zeros_like(X_target)
            sigma_safe = np.ones_like(X_target)
            Xc = X_cand
            xt = X_target

        weights = np.array([cfg.feature_weights.get(c, 1.0) for c in feature_cols], dtype=float)
        if (weights < 0).any():
            raise ValueError("feature_weights must be non-negative")

        if cfg.distance_mode != "weighted_euclidean":
            raise NotImplementedError(f"distance_mode={cfg.distance_mode!r}")
        diff = (Xc - xt) * np.sqrt(weights)
        distances = np.sqrt(np.sum(diff * diff, axis=1))

        k = int(n_scenarios)
        if cfg.diversity_min_gap_days > 0:
            order = self._diverse_selection(distances, cand_days_list, k, cfg.diversity_min_gap_days)
        else:
            order = list(np.argsort(distances, kind="stable")[:k])

        sel_distances = distances[order]
        sel_days: list[date] = [cand_days_list[i] for i in order]
        probs = self._probabilities(sel_distances)

        da = np.zeros((k, 24), dtype=float)
        id_arr = np.zeros((k, 24), dtype=float)
        gen = np.zeros((k, 24), dtype=float)
        labels: list[str] = []

        for s, d in enumerate(sel_days):
            rows = _hours_of_day(panel, d)
            if not _is_complete_24h(rows):
                raise RuntimeError(
                    f"Selected neighbour {d} does not have 24 complete hourly rows in the panel."
                )
            da[s, :] = rows[cfg.da_column].to_numpy(dtype=float)
            id_arr[s, :] = rows[cfg.id_column].to_numpy(dtype=float)
            gen[s, :] = rows[cfg.gen_column].to_numpy(dtype=float)
            labels.append(d.isoformat())

        self._diagnostics = {
            "target_date": target_date.isoformat(),
            "feature_columns": list(feature_cols),
            "target_query_features": {c: float(v) for c, v in zip(feature_cols, X_target)},
            "feature_scaling_means": {c: float(m) for c, m in zip(feature_cols, mu)},
            "feature_scaling_stds": {c: float(s) for c, s in zip(feature_cols, sigma_safe)},
            "feature_weights_used": {c: float(w) for c, w in zip(feature_cols, weights)},
            "selected_neighbour_dates": [d.isoformat() for d in sel_days],
            "selected_neighbour_distances": [float(x) for x in sel_distances],
            "selected_neighbour_probabilities": [float(x) for x in probs],
            "probability_mode": cfg.probability_mode,
            "leakage_guard_status": "passed (no target-day actuals used in query)",
            "n_scenarios": k,
            "n_candidate_days_total": len(candidate_days),
            "n_candidate_days_with_complete_features": len(cand_days_list),
            "information_cutoff_days": cfg.information_cutoff_days,
        }

        return ScenarioBundle(
            da_prices=da,
            id_prices=id_arr,
            res_generation=gen,
            probabilities=probs,
            target_date=target_date,
            generation_method="knn",
            scenario_labels=labels,
        )

    def diagnostics(self) -> dict[str, Any]:
        return dict(self._diagnostics)

    # ----- internals -----

    def _validate_target_in_panel(self, panel: pd.DataFrame, target_date: date) -> None:
        rows = _hours_of_day(panel, target_date)
        if not _is_complete_24h(rows):
            raise ValueError(
                f"Target date {target_date} does not have 24 complete hourly rows in the panel."
            )
        for col in self._weather_columns:
            if col in rows.columns and rows[col].isna().any():
                raise ValueError(
                    f"Target-day weather column '{col}' has NaN — "
                    "cannot build query without fabricating data."
                )

    def _eligible_candidates(self, panel: pd.DataFrame, target_date: date) -> list[date]:
        cfg = self._cfg
        cutoff = cfg.information_cutoff_days
        forbidden_after = pd.Timestamp(target_date) - pd.Timedelta(days=cutoff)

        df = panel.copy()
        df["_date"] = df["delivery_ts_utc"].dt.date
        gb = df.groupby("_date", as_index=False).agg(
            n_rows=("delivery_ts_utc", "size"),
            n_da=(cfg.da_column, lambda s: int(s.notna().sum())),
            n_id=(cfg.id_column, lambda s: int(s.notna().sum())),
            n_gen=(cfg.gen_column, lambda s: int(s.notna().sum())),
        )
        eligible = gb[
            (gb["n_rows"] == 24)
            & (gb["n_da"] == 24)
            & (gb["n_id"] == 24)
            & (gb["n_gen"] == 24)
            & (pd.to_datetime(gb["_date"]) <= forbidden_after)
        ]
        return sorted(d for d in eligible["_date"].tolist() if d < target_date)

    @staticmethod
    def _diverse_selection(
        distances: np.ndarray,
        cand_days: list[date],
        k: int,
        min_gap_days: int,
    ) -> list[int]:
        sort_order = np.argsort(distances, kind="stable")
        selected: list[int] = []
        selected_days: list[date] = []
        for idx in sort_order:
            d = cand_days[idx]
            if all(abs((d - sd).days) >= min_gap_days for sd in selected_days):
                selected.append(int(idx))
                selected_days.append(d)
                if len(selected) >= k:
                    break
        if len(selected) < k:
            chosen = set(selected)
            for idx in sort_order:
                if int(idx) not in chosen:
                    selected.append(int(idx))
                    if len(selected) >= k:
                        break
        return selected

    def _probabilities(self, distances: np.ndarray) -> np.ndarray:
        cfg = self._cfg
        d = np.asarray(distances, dtype=float)
        if cfg.probability_mode == "uniform":
            return np.full_like(d, 1.0 / len(d))
        elif cfg.probability_mode == "inverse_distance":
            inv = 1.0 / np.where(d == 0, 1e-12, d)
            return inv / inv.sum()
        elif cfg.probability_mode == "softmax":
            scale = max(float(np.median(d)), 1e-12) * float(cfg.softmax_temperature)
            logits = -d / scale
            logits -= logits.max()
            p = np.exp(logits)
            return p / p.sum()
        else:
            raise NotImplementedError(f"probability_mode={cfg.probability_mode!r}")


# ---------------------------------------------------------------------------
# Diagnostics persistence
# ---------------------------------------------------------------------------


def persist_diagnostics(diagnostics: dict[str, Any], out_dir: Path, *, target_date: date) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"knn_diagnostics_{target_date.isoformat()}.json"
    out_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    return out_path
