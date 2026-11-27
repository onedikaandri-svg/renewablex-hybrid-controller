"""Step input, intermediate proposal, step output, and inter-step state contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from renewablex_controller.core.units import (
    NUMERIC_ZERO_EPSILON,
    BiomassStatus,
    ConstraintStatus,
    OperatingMode,
    dt_seconds_to_hours,
)


class ControllerStepInput(BaseModel):
    """Deterministic per-step input contract aligned with RenewableX DispatchContext."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    step_idx: int = Field(ge=0)
    dt_seconds: float = Field(gt=0.0)
    hour_of_day: int = Field(ge=0, le=23)
    month: int = Field(ge=1, le=12)

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
    biomass_can_start: bool
    biomass_can_stop: bool
    biomass_fuel_remaining_tons: float | None = Field(default=None, ge=0.0)

    allow_grid_import: bool
    allow_grid_export: bool
    tariff_per_kwh: float = 0.0

    # Optional supervisory and SCADA extensions with safe defaults
    grid_connected: bool = True
    grid_outage_duration_seconds: float = Field(default=0.0, ge=0.0)
    grid_export_target_kw: float | None = Field(default=None, ge=0.0)
    grid_export_limit_kw: float | None = Field(default=None, ge=0.0)
    biomass_maintenance_mode: bool = False
    sync_permissive: bool = False
    comm_healthy: bool = True
    emergency_stop: bool = False
    telemetry_age_seconds: float = Field(default=0.0, ge=0.0)
    operator_ack_restoration: bool = False
    operator_ack_fault_reset: bool = False

    @property
    def step_hours(self) -> float:
        """Step duration in hours."""
        return dt_seconds_to_hours(self.dt_seconds)

    @property
    def total_demand_kw(self) -> float:
        """Total electrical demand (local load plus plant parasitic load) in kW."""
        return self.local_load_kw + self.parasitic_load_kw

    @property
    def net_load_kw(self) -> float:
        """Positive remaining demand after subtracting available PV in kW."""
        return max(0.0, self.total_demand_kw - self.pv_available_kw)

    @property
    def pv_surplus_kw(self) -> float:
        """Positive excess PV above total local and parasitic demand in kW."""
        return max(0.0, self.pv_available_kw - self.total_demand_kw)


class ProposedDispatch(BaseModel):
    """Unverified dispatch proposal produced by Module 3 before Module 5 supervision."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    operating_mode: OperatingMode
    sequence_state: str | None = None

    pv_command_kw: float = Field(ge=0.0)
    pv_curtailment_kw: float = Field(default=0.0, ge=0.0)
    bess_charge_kw: float = Field(default=0.0, ge=0.0)
    bess_discharge_kw: float = Field(default=0.0, ge=0.0)
    biomass_command_kw: float = Field(default=0.0, ge=0.0)
    biomass_start_request: bool = False
    biomass_stop_request: bool = False
    grid_import_kw: float = Field(default=0.0, ge=0.0)
    grid_export_kw: float = Field(default=0.0, ge=0.0)
    served_load_kw: float = Field(default=0.0, ge=0.0)
    unmet_load_kw: float = Field(default=0.0, ge=0.0)

    active_rule_ids: list[str] = Field(default_factory=list)
    trace_reason: str = ""


class ControllerStepOutput(BaseModel):
    """Verified per-step supervisory command output with closed active-power balance."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    operating_mode: OperatingMode
    sequence_state: str | None = None

    pv_command_kw: float = Field(ge=0.0)
    pv_curtailment_kw: float = Field(ge=0.0)
    bess_charge_kw: float = Field(ge=0.0)
    bess_discharge_kw: float = Field(ge=0.0)
    biomass_command_kw: float = Field(ge=0.0)
    biomass_start_request: bool
    biomass_stop_request: bool
    grid_import_kw: float = Field(ge=0.0)
    grid_export_kw: float = Field(ge=0.0)
    served_load_kw: float = Field(ge=0.0)
    unmet_load_kw: float = Field(ge=0.0)

    constraint_status: ConstraintStatus
    violations: list[str] = Field(default_factory=list)
    active_rule_ids: list[str] = Field(default_factory=list)
    trace_reason: str
    balance_residual_kw: float

    @model_validator(mode="after")
    def _validate_mutual_exclusion(self) -> ControllerStepOutput:
        if (
            self.bess_charge_kw > NUMERIC_ZERO_EPSILON
            and self.bess_discharge_kw > NUMERIC_ZERO_EPSILON
        ):
            raise ValueError(
                "BESS mutual-exclusion violated: bess_charge_kw and bess_discharge_kw "
                "cannot both be > 0"
            )
        if (
            self.grid_import_kw > NUMERIC_ZERO_EPSILON
            and self.grid_export_kw > NUMERIC_ZERO_EPSILON
        ):
            raise ValueError(
                "Grid mutual-exclusion violated: grid_import_kw and grid_export_kw "
                "cannot both be > 0"
            )
        return self

    @property
    def p_pcc_export_kw(self) -> float:
        """Net PCC export power in kW (positive = net export to grid)."""
        return self.grid_export_kw - self.grid_import_kw


class ModeTransitionEvent(BaseModel):
    """Audit record for an accepted or rejected FSM mode transition."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    step_idx: int = Field(ge=0)
    from_mode: OperatingMode
    to_mode: OperatingMode
    reason: str
    accepted: bool = True


class ControllerState(BaseModel):
    """Explicit serializable inter-step controller state (ADR-002)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    operating_mode: OperatingMode = OperatingMode.GRID_CONNECTED_NORMAL
    previous_grid_mode: OperatingMode = OperatingMode.GRID_CONNECTED_NORMAL
    outage_elapsed_seconds: float = Field(default=0.0, ge=0.0)
    active_sequence: str | None = None
    sequence_step: int = Field(default=0, ge=0)
    sequence_elapsed_seconds: float = Field(default=0.0, ge=0.0)
    last_biomass_command_kw: float | None = Field(default=None, ge=0.0)
    biomass_state_elapsed_seconds: float = Field(default=0.0, ge=0.0)
    event_log: list[ModeTransitionEvent] = Field(default_factory=list)
