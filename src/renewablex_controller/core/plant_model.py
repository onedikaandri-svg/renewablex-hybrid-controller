"""Module 1: Validated operational plant configuration and capability helpers."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from renewablex_controller.core.units import CommLossAction, dt_seconds_to_hours


class PlantMetadataConfig(BaseModel):
    """Descriptive plant identifiers and interconnection metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    plant_id: str = Field(min_length=1)
    plant_name: str = Field(min_length=1)
    feeder_id: str | None = None
    nominal_voltage_kv: float | None = Field(default=None, gt=0.0)


class PVOperationalConfig(BaseModel):
    """Operational control envelope for the Solar PV subsystem."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    nameplate_kwp: float = Field(ge=0.0)
    rated_ac_kw: float = Field(ge=0.0)
    curtailment_enabled: bool = True
    available: bool = True


class BiomassOperationalConfig(BaseModel):
    """Operational limits and ramp constraints for the biomass generator."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    nameplate_kw: float = Field(ge=0.0)
    operating_max_kw: float = Field(ge=0.0)
    min_load_kw: float = Field(ge=0.0)
    ramp_up_kw_per_hour: float = Field(ge=0.0)
    ramp_down_kw_per_hour: float = Field(ge=0.0)
    startup_time_seconds: float = Field(ge=0.0)
    shutdown_time_seconds: float = Field(ge=0.0)
    min_up_time_seconds: float = Field(ge=0.0)
    min_down_time_seconds: float = Field(ge=0.0)
    available: bool = True

    @model_validator(mode="after")
    def _validate_power_hierarchy(self) -> BiomassOperationalConfig:
        if self.operating_max_kw > self.nameplate_kw:
            raise ValueError(
                f"operating_max_kw ({self.operating_max_kw}) must be <= "
                f"nameplate_kw ({self.nameplate_kw})"
            )
        if self.min_load_kw > self.operating_max_kw:
            raise ValueError(
                f"min_load_kw ({self.min_load_kw}) must be <= "
                f"operating_max_kw ({self.operating_max_kw})"
            )
        return self

    def max_ramp_up_kw(self, dt_seconds: float) -> float:
        """Return maximum allowed upward power change (kW) over dt_seconds."""
        return self.ramp_up_kw_per_hour * dt_seconds_to_hours(dt_seconds)

    def max_ramp_down_kw(self, dt_seconds: float) -> float:
        """Return maximum allowed downward power change (kW) over dt_seconds."""
        return self.ramp_down_kw_per_hour * dt_seconds_to_hours(dt_seconds)


class BESSOperationalConfig(BaseModel):
    """Operational energy, power, efficiency, and reserve limits for the BESS."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    nominal_capacity_kwh: float = Field(ge=0.0)
    effective_capacity_kwh: float = Field(ge=0.0)
    max_charge_kw: float = Field(ge=0.0)
    max_discharge_kw: float = Field(ge=0.0)
    soc_min_fraction: float = Field(ge=0.0, le=1.0)
    reserve_soc_fraction: float = Field(ge=0.0, le=1.0)
    soc_max_fraction: float = Field(ge=0.0, le=1.0)
    charge_efficiency: float = Field(gt=0.0, le=1.0)
    discharge_efficiency: float = Field(gt=0.0, le=1.0)
    allow_reserve_drawdown_in_islanded: bool = True
    available: bool = True

    @model_validator(mode="after")
    def _validate_capacity_and_soc_bounds(self) -> BESSOperationalConfig:
        if self.effective_capacity_kwh > self.nominal_capacity_kwh:
            raise ValueError(
                f"effective_capacity_kwh ({self.effective_capacity_kwh}) must be <= "
                f"nominal_capacity_kwh ({self.nominal_capacity_kwh})"
            )
        if not (self.soc_min_fraction <= self.reserve_soc_fraction <= self.soc_max_fraction):
            raise ValueError(
                "SoC bounds must satisfy soc_min_fraction <= reserve_soc_fraction "
                "<= soc_max_fraction"
            )
        return self

    def max_charge_kw_for_step(self, soc_fraction: float, dt_seconds: float) -> float:
        """Compute maximum AC charge power (kW) without exceeding soc_max_fraction."""
        dt_hours = dt_seconds_to_hours(dt_seconds)
        if not (0.0 <= soc_fraction <= 1.0):
            raise ValueError(f"soc_fraction must be in [0, 1], got {soc_fraction!r}")
        if not self.available or self.effective_capacity_kwh <= 0.0:
            return 0.0
        energy_headroom_kwh = max(0.0, (self.soc_max_fraction - soc_fraction) * self.effective_capacity_kwh)
        step_limited_kw = energy_headroom_kwh / (dt_hours * self.charge_efficiency)
        return min(self.max_charge_kw, step_limited_kw)

    def max_discharge_kw_for_step(
        self,
        soc_fraction: float,
        dt_seconds: float,
        soc_floor_fraction: float | None = None,
    ) -> float:
        """Compute maximum AC discharge power (kW) without violating soc_floor_fraction."""
        dt_hours = dt_seconds_to_hours(dt_seconds)
        if not (0.0 <= soc_fraction <= 1.0):
            raise ValueError(f"soc_fraction must be in [0, 1], got {soc_fraction!r}")
        if not self.available or self.effective_capacity_kwh <= 0.0:
            return 0.0
        floor = (
            self.soc_min_fraction
            if soc_floor_fraction is None
            else max(self.soc_min_fraction, soc_floor_fraction)
        )
        energy_available_kwh = max(0.0, (soc_fraction - floor) * self.effective_capacity_kwh)
        step_limited_kw = (energy_available_kwh * self.discharge_efficiency) / dt_hours
        return min(self.max_discharge_kw, step_limited_kw)


class GridOperationalConfig(BaseModel):
    """PCC interconnection limits and default import/export policies."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    interconnection_limit_kw: float = Field(ge=0.0)
    default_allow_import: bool = True
    default_allow_export: bool = True
    default_export_limit_kw: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _validate_export_limit(self) -> GridOperationalConfig:
        if (
            self.default_export_limit_kw is not None
            and self.default_export_limit_kw > self.interconnection_limit_kw
        ):
            raise ValueError(
                f"default_export_limit_kw ({self.default_export_limit_kw}) must be <= "
                f"interconnection_limit_kw ({self.interconnection_limit_kw})"
            )
        return self


class ControlPolicyConfig(BaseModel):
    """Supervisory control timers, safety tolerances, and fail-safe flags."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    default_dt_seconds: float = Field(default=3600.0, gt=0.0)
    momentary_outage_threshold_seconds: float = Field(default=60.0, ge=0.0)
    max_telemetry_age_seconds: float = Field(default=30.0, gt=0.0)
    power_balance_tolerance_kw: float = Field(default=1e-6, gt=0.0)
    require_operator_ack_for_restoration: bool = True
    require_operator_ack_for_fault_reset: bool = True
    comm_loss_action: CommLossAction = CommLossAction.EMERGENCY_FAULT
    hardware_commands_enabled: bool = False

    @model_validator(mode="after")
    def _validate_simulation_safety_lock(self) -> ControlPolicyConfig:
        if self.hardware_commands_enabled:
            raise ValueError(
                "hardware_commands_enabled must remain False in pure-Python simulation core"
            )
        return self


class PlantOperationalConfig(BaseModel):
    """Complete validated digital operational envelope of a hybrid plant."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        populate_by_name=True,
    )

    comment: str | None = Field(default=None, alias="_comment")
    plant_metadata: PlantMetadataConfig
    pv: PVOperationalConfig
    biomass: BiomassOperationalConfig
    bess: BESSOperationalConfig
    grid: GridOperationalConfig
    control_policy: ControlPolicyConfig = Field(default_factory=ControlPolicyConfig)

    @classmethod
    def from_json_file(cls, path: str | Path) -> PlantOperationalConfig:
        """Load and validate a plant operational configuration from a JSON file."""
        raw_text = Path(path).read_text(encoding="utf-8")
        return cls.model_validate(json.loads(raw_text))
