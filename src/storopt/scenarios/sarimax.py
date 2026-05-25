"""
SARIMAX scenario generator — Method B Step-6 specification.

Implements the ScenarioGenerator interface (storopt.scenarios.base).

Produces S scenarios of DA prices, ID prices, and renewable generation for a
24-hour delivery window via:

  DA / ID:  Independent PI-Gaussian SARIMAX draws.
            Locked spec: AR([1,2,24,168]), d=0, MA([1,24,168]), trend='c'.
            Rolling 3-month (2160 h) training window, refitted each call.

  Wind:     Day-of-week-matched 24-hour block bootstrap from the historical
            hr1_generation_mw column.  Same target weekday → representative
            diurnal profiles; replace=True → distinct paths when history is
            short.

All locked model constants are exposed as constructor kwargs so they can be
overridden via config.scenarios.params.  Default values match the Step-6
winner.  Unknown kwargs are silently absorbed (**kwargs).
"""
from __future__ import annotations

from datetime import date, timedelta
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from storopt.scenarios.base import ScenarioGenerator
from storopt.scenarios.types import ScenarioBundle

# ── Locked Step-6 defaults ────────────────────────────────────────────────────
_AR_LAGS_DEFAULT         = [1, 2, 24, 168]
_DIFF_ORDER_DEFAULT      = 0
_MA_LAGS_DEFAULT         = [1, 24, 168]
_SEASONAL_ORDER_DEFAULT  = (0, 0, 0, 0)
_TREND_DEFAULT           = "c"
_TRAINING_HOURS_DEFAULT  = 2160   # 3 months
_MIN_TRAINING_HOURS      = 1008   # lag-168 floor
_MAX_FIT_ITER_DEFAULT    = 200    # hard cap; model typically converges in 20-50 iterations
_CONVERGENCE_TOL_DEFAULT = 1e-4   # L-BFGS pgtol: projected gradient norm at which fitting stops
                                   # Empirically measured on real DK1 prices: gradient drops from
                                   # ~0.096 at start to ~5.5e-5 at convergence; 1e-4 exits at
                                   # iteration ~19, essentially identical to full convergence.
_LOCAL_TZ_DEFAULT        = "Europe/Copenhagen"
_HORIZON                 = 24


class SarimaxScenarioGenerator(ScenarioGenerator):
    """
    PI-Gaussian SARIMAX price scenarios + day-of-week bootstrap wind scenarios.

    Parameters
    ----------
    ar_lags : list[int]
        AR lag indices (list-form SARIMAX order[0]).
    diff_order : int
        Integration order (SARIMAX order[1]).
    ma_lags : list[int]
        MA lag indices (list-form SARIMAX order[2]).
    seasonal_order : tuple[int, int, int, int]
        (P, D, Q, s) seasonal component — (0,0,0,0) because seasonality is
        handled via the native lags above.
    trend : str
        SARIMAX trend specification.  'c' adds a constant that prevents
        long-horizon paths from decaying toward 0 EUR/MWh.
    training_hours : int
        Rolling training window length [hours].
    min_training_hours : int
        Minimum hours of history required before generate() will proceed.
    max_fit_iter : int
        L-BFGS iteration cap for SARIMAX fitting.
    convergence_tol : float
        L-BFGS projected gradient norm threshold (pgtol). Fitting stops when
        max(|proj gradient|) < convergence_tol. Calibrated on real DK1 price
        data: gradient norm falls from ~0.096 at start to ~5.5e-5 at full
        convergence; 1e-4 exits at iteration ~19 with negligible accuracy loss.
        Default 1e-4.
    local_tz : str
        Timezone for the forecast origin (midnight of target_date).
    da_col : str
        Column name for DA prices in the history panel.
    id_col : str
        Column name for ID/imbalance prices in the history panel.
    gen_col : str
        Column name for plant generation in the history panel.
    res_clip_max : float | None
        Upper clip for bootstrapped generation [MW].  None = no upper clip.
    **kwargs
        Unknown keys are silently absorbed (spec requirement).
    """

    def __init__(
        self,
        ar_lags: list[int] | None = None,
        diff_order: int = _DIFF_ORDER_DEFAULT,
        ma_lags: list[int] | None = None,
        seasonal_order: tuple | list = _SEASONAL_ORDER_DEFAULT,
        trend: str = _TREND_DEFAULT,
        training_hours: int = _TRAINING_HOURS_DEFAULT,
        min_training_hours: int = _MIN_TRAINING_HOURS,
        max_fit_iter: int = _MAX_FIT_ITER_DEFAULT,
        convergence_tol: float = _CONVERGENCE_TOL_DEFAULT,
        local_tz: str = _LOCAL_TZ_DEFAULT,
        da_col: str = "da_eur_mwh",
        id_col: str = "id_eur_mwh",
        gen_col: str = "hr1_generation_mw",
        res_clip_max: float | None = None,
        **kwargs,
    ) -> None:
        ar = list(ar_lags) if ar_lags is not None else list(_AR_LAGS_DEFAULT)
        ma = list(ma_lags) if ma_lags is not None else list(_MA_LAGS_DEFAULT)
        self._sarimax_order          = (ar, int(diff_order), ma)
        self._sarimax_seasonal_order = tuple(int(x) for x in seasonal_order)
        self._trend                  = trend
        self._training_hours         = int(training_hours)
        self._min_training_hours     = int(min_training_hours)
        self._max_fit_iter           = int(max_fit_iter)
        self._convergence_tol        = float(convergence_tol)
        self._local_tz               = local_tz
        self._da_col                 = da_col
        self._id_col                 = id_col
        self._gen_col                = gen_col
        self._res_clip_max           = res_clip_max
        self._panel: pd.DataFrame | None = None
        self._fit_cache: dict[tuple, object] = {}  # (col, start_ts, end_ts) → fitted result

    # ── ScenarioGenerator interface ───────────────────────────────────────────

    def fit(self, history: pd.DataFrame) -> None:
        panel = history.copy()
        panel["delivery_ts_utc"] = pd.to_datetime(panel["delivery_ts_utc"], utc=True)
        self._panel = panel.sort_values("delivery_ts_utc").reset_index(drop=True)
        self._fit_cache.clear()

    def generate(self, target_date: date, n_scenarios: int) -> ScenarioBundle:
        if self._panel is None:
            raise RuntimeError("Call fit() before generate()")

        rng = np.random.default_rng(None)

        # Leakage guard: exclude target-date rows (prices and generation are
        # unknown at gate-closure; weather columns are safe but not used here)
        panel_hist = self._panel[
            self._panel["delivery_ts_utc"].dt.date < target_date
        ].copy()

        # Forecast origin: midnight local time on target_date
        forecast_origin = pd.Timestamp(target_date, tz=self._local_tz).tz_convert("UTC")

        # Build UTC hourly series for DA and ID
        da_series = self._build_price_series(panel_hist, self._da_col)
        id_series = self._build_price_series(panel_hist, self._id_col)

        # Slice rolling training window
        train_da = self._slice_train(da_series, forecast_origin, self._training_hours)
        train_id = self._slice_train(id_series, forecast_origin, self._training_hours)

        if len(train_da) < self._min_training_hours or len(train_id) < self._min_training_hours:
            raise RuntimeError(
                f"Insufficient history for {target_date}: "
                f"DA {len(train_da)}h, ID {len(train_id)}h "
                f"(minimum {self._min_training_hours}h required for lag-168 SARIMAX spec)."
            )

        # Fit independent SARIMAX models on DA and ID (cached by training slice)
        res_da = self._fit_sarimax_cached(train_da)
        res_id = self._fit_sarimax_cached(train_id)

        # Draw PI-Gaussian scenarios → shape (horizon, S)
        scen_da = self._simulate_pi_gaussian(res_da, n_scenarios, _HORIZON, rng)
        scen_id = self._simulate_pi_gaussian(res_id, n_scenarios, _HORIZON, rng)

        # Day-of-week block bootstrap for generation → shape (horizon, S)
        scen_gen = self._bootstrap_generation(panel_hist, target_date, n_scenarios, rng)

        # Transpose to (S, 24) per ScenarioBundle contract
        da_prices = scen_da.T.astype(float)
        id_prices = scen_id.T.astype(float)
        res_gen   = np.clip(scen_gen.T.astype(float), 0.0, self._res_clip_max)

        probs = np.full(n_scenarios, 1.0 / n_scenarios)
        probs /= probs.sum()  # guard against float drift

        bundle = ScenarioBundle(
            da_prices=da_prices,
            id_prices=id_prices,
            res_generation=res_gen,
            probabilities=probs,
            target_date=target_date,
            generation_method="sarimax",
            scenario_labels=[f"s{s:03d}" for s in range(n_scenarios)],
        )
        bundle.validate()
        return bundle

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_price_series(self, panel: pd.DataFrame, col: str) -> pd.Series:
        s = (
            panel.set_index("delivery_ts_utc")[col]
            .sort_index()
            .astype(float)
        )
        grid = pd.date_range(s.index.min(), s.index.max(), freq="1h", tz="UTC")
        s = s.reindex(grid)
        n_missing = int(s.isna().sum())
        if n_missing > 24:
            raise RuntimeError(
                f"Column '{col}': {n_missing} missing hours in panel "
                f"(limit is 24 for time-interpolation)."
            )
        if n_missing:
            s = s.interpolate(method="time", limit=24)
        s.name = col
        return s

    @staticmethod
    def _slice_train(
        series: pd.Series, origin: pd.Timestamp, n_hours: int
    ) -> pd.Series:
        end_pos   = series.index.searchsorted(origin)
        start_pos = max(end_pos - n_hours, 0)
        return series.iloc[start_pos:end_pos]

    def _fit_sarimax_cached(self, series: pd.Series):
        key = (series.name, series.index[0], series.index[-1])
        if key not in self._fit_cache:
            self._fit_cache[key] = self._fit_sarimax(series)
        return self._fit_cache[key]

    def _fit_sarimax(self, series: pd.Series):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = SARIMAX(
                series,
                order=self._sarimax_order,
                seasonal_order=self._sarimax_seasonal_order,
                trend=self._trend,
                enforce_stationarity=True,
                enforce_invertibility=True,
            )
            try:
                res = mod.fit(
                    disp=False, method="lbfgs",
                    maxiter=self._max_fit_iter, pgtol=self._convergence_tol,
                )
            except np.linalg.LinAlgError:
                # Stationary Lyapunov solve fails for large (lag-168) state vectors when
                # the covariance matrix is ill-conditioned. Retry with diffuse initialization,
                # which avoids the discrete Lyapunov equation entirely.
                mod = SARIMAX(
                    series,
                    order=self._sarimax_order,
                    seasonal_order=self._sarimax_seasonal_order,
                    trend=self._trend,
                    enforce_stationarity=True,
                    enforce_invertibility=True,
                    initialization="approximate_diffuse",
                )
                res = mod.fit(
                    disp=False, method="lbfgs",
                    maxiter=self._max_fit_iter, pgtol=self._convergence_tol,
                )
        sigma2 = float(res.params["sigma2"])
        if not np.isfinite(sigma2) or sigma2 <= 0:
            raise RuntimeError(f"SARIMAX returned invalid sigma2={sigma2!r}")
        # Stability check: simulated paths must stay within ±10 000 EUR/MWh
        test_sim = np.asarray(res.simulate(nsimulations=24, anchor="end", repetitions=4))
        if not np.isfinite(test_sim).all() or abs(test_sim).max() > 1e4:
            raise RuntimeError(
                f"SARIMAX fit unstable on this slice "
                f"(simulate max={abs(test_sim).max():.2e}); refusing to generate scenarios."
            )
        return res

    @staticmethod
    def _simulate_pi_gaussian(
        res,
        n_scenarios: int,
        horizon: int,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """
        Draw (horizon, n_scenarios) PI-Gaussian paths from a fitted SARIMAX result.

        Three load-bearing choices (see src/method_b/scenarios_step6.py for full
        derivation and empirical verification):
          - anchor=t0  (not 'end'): aligns the state-space recursion deterministically.
          - initial_state=filtered_state[:, -1]: deterministic posterior mean at t0.
            Without this, statsmodels samples a random initial state that, on 168-dim
            states with near-singular covariance, produces paths centred at -15 EUR/MWh
            with std 140.
          - shocks_ext pads one extra zero shock so shape is (horizon+1, 1), matching
            nsimulations=horizon+1; sim[0] is the anchor value and is discarded.
        """
        sigma = float(np.sqrt(res.params["sigma2"]))
        t0    = pd.DatetimeIndex(res.model.data.row_labels)[-1]
        init  = np.asarray(res.filtered_state[:, -1], dtype=float)
        nsim  = horizon + 1
        zero_meas = np.zeros((nsim, 1), dtype=float)
        out = np.zeros((horizon, n_scenarios))
        for s in range(n_scenarios):
            shocks = (rng.standard_normal(horizon) * sigma).reshape(horizon, 1)
            shocks_ext = np.vstack([shocks, np.zeros((1, 1))])
            sim_s = res.simulate(
                nsimulations=nsim,
                anchor=t0,
                state_shocks=shocks_ext,
                measurement_shocks=zero_meas,
                initial_state=init,
            )
            out[:, s] = np.asarray(sim_s)[1:]  # drop anchor row (sim[0] == y[t0])
        return out

    def _bootstrap_generation(
        self,
        panel: pd.DataFrame,
        target_date: date,
        n_scenarios: int,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """
        Sample n_scenarios 24-hour generation blocks by day-of-week-matched
        block bootstrap.

        Returns array of shape (horizon, n_scenarios).

        Strategy: prefer historical days whose weekday matches target_date
        (captures weekday diurnal wind patterns); fall back to all days if no
        same-weekday complete blocks are available.
        """
        panel = panel.copy()
        panel["_date"] = panel["delivery_ts_utc"].dt.date
        panel["_hour"] = panel["delivery_ts_utc"].dt.hour

        target_dow = target_date.weekday()
        panel["_dow"] = pd.to_datetime(panel["_date"].astype(str)).dt.weekday

        def _complete_days(df: pd.DataFrame) -> np.ndarray:
            return (
                df.groupby("_date")
                .filter(lambda g: len(g) == _HORIZON)["_date"]
                .unique()
            )

        candidate_days = _complete_days(panel[panel["_dow"] == target_dow])
        if len(candidate_days) == 0:
            candidate_days = _complete_days(panel)
        if len(candidate_days) == 0:
            raise RuntimeError(
                "No complete 24-hour daily blocks found in the history panel "
                "for generation bootstrap."
            )

        chosen_indices = rng.choice(len(candidate_days), size=n_scenarios, replace=True)
        out = np.zeros((_HORIZON, n_scenarios))
        for s, idx in enumerate(chosen_indices):
            day = candidate_days[idx]
            block = (
                panel[panel["_date"] == day]
                .sort_values("_hour")[self._gen_col]
                .to_numpy(dtype=float)
            )
            out[:, s] = block
        return out
