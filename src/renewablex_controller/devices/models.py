"""Device-layer telemetry snapshot, quality flags, and command audit models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput
from renewablex_controller.core.units import BiomassStatus, ConstraintStatus, OperatingMode


class DataQuality(StrEnum):
    """Standardized telemetry measurement quality classification."""

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"
    COMM_LOST = "comm_lost"


class PlantTelemetrySnapshot(BaseModel):
    """Protocol-independent plant telemetry snapshot mapped to ControllerStepInput."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    timestamp_utc: datetime
    measurement_age_seconds: float = Field(default=0.0, ge=0.0)
    data_quality: DataQuality = DataQuality.GOOD
    comm_healthy: bool = True
    source_adapter: str = "unknown"

    hour_of_day: int = Field(default=0, ge=0, le=23)
    month: int = Field(default=1, ge=1, le=12)

    local_load_kw: float = Field(ge=0.0)
    parasitic_load_kw: float = Field(default=0.0, ge=0.0)
    pv_available_kw: float = Field(ge=0.0)

    bess_soc_fraction: float = Field(ge=0.0, le=1.0)
    bess_max_charge_kw: float = Field(ge=0.0)
    bess_max_discharge_kw: float = Field(ge=0.0)

    biomass_status: BiomassStatus
    biomass_available_kw: float = Field(ge=0.0)
    biomass_current_power_kw: float = Field(ge=0.0)
    biomass_min_load_kw: float = Field(ge=0.0)
    biomass_can_start: bool = False
    biomass_can_stop: bool = False
    biomass_fuel_remaining_tons: float | None = Field(default=None, ge=0.0)

    allow_grid_import: bool = True
    allow_grid_export: bool = True
    tariff_per_kwh: float = 0.0

    grid_connected: bool = True
    grid_outage_duration_seconds: float = Field(default=0.0, ge=0.0)
    grid_export_target_kw: float | None = Field(default=None, ge=0.0)
    grid_export_limit_kw: float | None = Field(default=None, ge=0.0)
    biomass_maintenance_mode: bool = False
    sync_permissive: bool = False
    emergency_stop: bool = False
    operator_ack_restoration: bool = False
    operator_ack_fault_reset: bool = False

    def to_step_input(self, *, step_idx: int, dt_seconds: float) -> ControllerStepInput:
        """Translate telemetry snapshot into a core ControllerStepInput."""
        effective_comm_healthy = self.comm_healthy and (
            self.data_quality
            not in {DataQuality.BAD, DataQuality.COMM_LOST, DataQuality.STALE}
        )
        return ControllerStepInput(
            step_idx=step_idx,
            dt_seconds=dt_seconds,
            hour_of_day=self.hour_of_day,
            month=self.month,
            local_load_kw=self.local_load_kw,
            parasitic_load_kw=self.parasitic_load_kw,
            pv_available_kw=self.pv_available_kw,
            bess_soc_fraction=self.bess_soc_fraction,
            bess_max_charge_kw=self.bess_max_charge_kw,
            bess_max_discharge_kw=self.bess_max_discharge_kw,
            biomass_status=self.biomass_status,
            biomass_available_kw=self.biomass_available_kw,
            biomass_current_power_kw=self.biomass_current_power_kw,
            biomass_min_load_kw=self.biomass_min_load_kw,
            biomass_can_start=self.biomass_can_start,
            biomass_can_stop=self.biomass_can_stop,
            biomass_fuel_remaining_tons=self.biomass_fuel_remaining_tons,
            allow_grid_import=self.allow_grid_import,
            allow_grid_export=self.allow_grid_export,
            tariff_per_kwh=self.tariff_per_kwh,
            grid_connected=self.grid_connected,
            grid_outage_duration_seconds=self.grid_outage_duration_seconds,
            grid_export_target_kw=self.grid_export_target_kw,
            grid_export_limit_kw=self.grid_export_limit_kw,
            biomass_maintenance_mode=self.biomass_maintenance_mode,
            sync_permissive=self.sync_permissive,
            comm_healthy=effective_comm_healthy,
            emergency_stop=self.emergency_stop,
            telemetry_age_seconds=self.measurement_age_seconds,
            operator_ack_restoration=self.operator_ack_restoration,
            operator_ack_fault_reset=self.operator_ack_fault_reset,
        )


class CommandAuditRecord(BaseModel):
    """Immutable audit log entry for every supervisory command evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    recorded_at_utc: datetime
    step_idx: int = Field(ge=0)
    operating_mode: OperatingMode
    constraint_status: ConstraintStatus
    execution_status: Literal["dry_run_only", "blocked_unsafe", "dispatched_simulated"]
    write_enabled: bool = False
    sink_name: str
    pv_command_kw: float = Field(ge=0.0)
    bess_charge_kw: float = Field(ge=0.0)
    bess_discharge_kw: float = Field(ge=0.0)
    biomass_command_kw: float = Field(ge=0.0)
    grid_import_kw: float = Field(ge=0.0)
    grid_export_kw: float = Field(ge=0.0)
    violations: list[str] = Field(default_factory=list)
    active_rule_ids: list[str] = Field(default_factory=list)
    trace_reason: str
