from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class IngestionConfig(BaseModel):
    area: str = Field("DK1", description="Market area code (e.g. 'DK1')")
    entsoe_area: str = Field("10YDK-1--------W", description="ENTSO-E bidding zone EIC")
    plant_eic: str = Field("", description="ENTSO-E registered resource EIC for the plant")
    generation_file: str = Field(
        "",
        description="Path to a pre-fetched generation parquet (used when ENTSO-E REST unavailable).",
    )
    weather_lat: float = Field(0.0, description="Plant latitude for Open-Meteo NWP fetch")
    weather_lon: float = Field(0.0, description="Plant longitude for Open-Meteo NWP fetch")
    cache_dir: str = Field("./data/cache", description="Directory for Parquet cache files")
    history_days: int = Field(730, gt=30, description="Calendar days of history before target date")


class BESSConfig(BaseModel):
    power_charge_mw: float = Field(1.0, gt=0, description="Max charge power [MW]")
    power_discharge_mw: float = Field(1.0, gt=0, description="Max discharge power [MW]")
    energy_capacity_mwh: float = Field(2.0, gt=0, description="Nominal energy capacity [MWh]")

    soc_min_frac: float = Field(0.10, ge=0.0, le=1.0)
    soc_max_frac: float = Field(0.90, ge=0.0, le=1.0)
    soc_init_frac: float = Field(0.50, ge=0.0, le=1.0)

    eta_charge: float = Field(0.95, gt=0.0, le=1.0, description="One-way charging efficiency")
    eta_discharge: float = Field(0.95, gt=0.0, le=1.0, description="One-way discharging efficiency")

    deg_cost_eur_per_mwh: float = Field(10.0, ge=0.0, description="Throughput degradation cost [€/MWh]")
    max_cycles_per_day: float | None = Field(None, ge=0.0, description="EFC/day cap; None = uncapped")

    @model_validator(mode="after")
    def _soc_bounds_consistent(self) -> BESSConfig:
        assert self.soc_min_frac < self.soc_max_frac, "soc_min_frac must be < soc_max_frac"
        assert self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac, (
            "soc_init_frac must be within [soc_min_frac, soc_max_frac]"
        )
        return self

    @property
    def soc_min_mwh(self) -> float:
        return self.soc_min_frac * self.energy_capacity_mwh

    @property
    def soc_max_mwh(self) -> float:
        return self.soc_max_frac * self.energy_capacity_mwh

    @property
    def soc_init_mwh(self) -> float:
        return self.soc_init_frac * self.energy_capacity_mwh

    @property
    def rte(self) -> float:
        return self.eta_charge * self.eta_discharge

    @property
    def usable_energy_mwh(self) -> float:
        return (self.soc_max_frac - self.soc_min_frac) * self.energy_capacity_mwh


class MarketConfig(BaseModel):
    n_periods: int = Field(24, ge=1, description="Periods per trading day")
    dt_hours: float = Field(1.0, gt=0.0, description="Duration of one period [h]")
    da_price_floor_eur: float = Field(-500.0, description="DA price floor [€/MWh]")
    da_price_ceil_eur: float = Field(4000.0, description="DA price ceiling [€/MWh]")
    timezone: str = Field("Europe/Copenhagen", description="Local market timezone for day alignment")
    id_position_cap_mw: float | None = Field(
        None, ge=0.0, description="Hard cap on |q_id[s,t]| in MW"
    )
    id_linear_penalty_eur_per_mwh: float | None = Field(
        None, ge=0.0, description="Linear penalty per MWh of intraday trade"
    )


class ScenarioConfig(BaseModel):
    method: Literal["knn", "naive"] = Field("knn", description="Scenario generation method")
    n_scenarios: int = Field(20, ge=1, le=500)
    params: dict[str, Any] = Field(default_factory=dict, description="Method-specific parameters")


class OptimizerConfig(BaseModel):
    method: Literal["stochastic_milp", "deterministic"] = Field("stochastic_milp")
    cvar_enabled: bool = Field(False)
    cvar_alpha: float = Field(0.95, gt=0.0, lt=1.0, description="CVaR confidence level")
    cvar_weight: float = Field(0.0, ge=0.0, description="CVaR objective weight (0 = risk-neutral)")


class SolverConfig(BaseModel):
    name: Literal["highs", "gurobi"] = Field("highs")
    time_limit_s: int = Field(300, gt=0)
    mip_gap: float = Field(0.001, gt=0.0, lt=1.0)
    threads: int | None = Field(None, ge=1, description="Solver threads; None = all available")
    verbose: bool = Field(False)


class BacktestConfig(BaseModel):
    vss_enabled: bool = Field(True, description="Compute Value of Stochastic Solution each day")
    evpi_enabled: bool = Field(
        False, description="Compute EVPI (expensive: one solve per scenario)"
    )


class RunConfig(BaseModel):
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    bess: BESSConfig = Field(default_factory=BESSConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    scenarios: ScenarioConfig = Field(default_factory=ScenarioConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    solver: SolverConfig = Field(default_factory=SolverConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
