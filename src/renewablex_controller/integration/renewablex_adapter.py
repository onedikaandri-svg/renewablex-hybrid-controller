"""Thin adapter translating between RenewableX DispatchContext/Decision and controller core."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
)
from renewablex_controller.core.controller import HybridController
from renewablex_controller.core.dispatch_engine import PowerDispatchEngine
from renewablex_controller.core.ems_rules import EMSRuleSet
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.safety_supervisor import SafetySupervisor
from renewablex_controller.core.units import BiomassStatus, ConstraintStatus, OperatingMode


class RenewableXDispatchContextProtocol(Protocol):
    """Duck-typed protocol matching RenewableX engine DispatchContext fields."""

    step_idx: int
    hour_of_day: int
    month: int
    load_kw: float
    parasitic_load_kw: float
    pv_available_kw: float
    bess_soc: float
    bess_max_charge_kw: float
    bess_max_discharge_kw: float
    biomass_status: str
    biomass_available_kw: float
    biomass_current_power_kw: float
    biomass_min_load_kw: float
    biomass_can_start: bool
    biomass_can_stop: bool
    biomass_fuel_remaining_tons: float | None
    allow_grid_import: bool
    allow_grid_export: bool
    tariff_per_kwh: float


class RenewableXDispatchDecision(BaseModel):
    """Translated output aligned with RenewableX DispatchDecision sign conventions."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    pv_command_kw: float = Field(ge=0.0)
    curtailment_kw: float = Field(ge=0.0)
    bess_command_kw: float
    biomass_command_kw: float = Field(ge=0.0)
    biomass_start_request: bool
    biomass_stop_request: bool
    grid_import_kw: float = Field(ge=0.0)
    grid_export_kw: float = Field(ge=0.0)
    served_load_kw: float = Field(ge=0.0)
    unmet_load_kw: float = Field(ge=0.0)
    operating_mode: OperatingMode
    constraint_status: ConstraintStatus
    violations: list[str] = Field(default_factory=list)
    active_rule_ids: list[str] = Field(default_factory=list)
    trace_reason: str


def from_renewablex_context(
    ctx: RenewableXDispatchContextProtocol,
    *,
    dt_seconds: float = 3600.0,
    grid_connected: bool | None = None,
    grid_outage_duration_seconds: float = 0.0,
    grid_export_target_kw: float | None = None,
    grid_export_limit_kw: float | None = None,
    biomass_maintenance_mode: bool = False,
    sync_permissive: bool = False,
    comm_healthy: bool = True,
    emergency_stop: bool = False,
) -> ControllerStepInput:
    """Translate a RenewableX DispatchContext into a validated ControllerStepInput."""
    resolved_grid_connected = (
        bool(ctx.allow_grid_import or ctx.allow_grid_export)
        if grid_connected is None
        else grid_connected
    )
    return ControllerStepInput(
        step_idx=ctx.step_idx,
        dt_seconds=dt_seconds,
        hour_of_day=ctx.hour_of_day,
        month=ctx.month,
        local_load_kw=ctx.load_kw,
        parasitic_load_kw=ctx.parasitic_load_kw,
        pv_available_kw=ctx.pv_available_kw,
        bess_soc_fraction=ctx.bess_soc,
        bess_max_charge_kw=ctx.bess_max_charge_kw,
        bess_max_discharge_kw=ctx.bess_max_discharge_kw,
        biomass_status=BiomassStatus(ctx.biomass_status),
        biomass_available_kw=ctx.biomass_available_kw,
        biomass_current_power_kw=ctx.biomass_current_power_kw,
        biomass_min_load_kw=ctx.biomass_min_load_kw,
        biomass_can_start=ctx.biomass_can_start,
        biomass_can_stop=ctx.biomass_can_stop,
        biomass_fuel_remaining_tons=ctx.biomass_fuel_remaining_tons,
        allow_grid_import=ctx.allow_grid_import,
        allow_grid_export=ctx.allow_grid_export,
        tariff_per_kwh=ctx.tariff_per_kwh,
        grid_connected=resolved_grid_connected,
        grid_outage_duration_seconds=grid_outage_duration_seconds,
        grid_export_target_kw=grid_export_target_kw,
        grid_export_limit_kw=grid_export_limit_kw,
        biomass_maintenance_mode=biomass_maintenance_mode,
        sync_permissive=sync_permissive,
        comm_healthy=comm_healthy,
        emergency_stop=emergency_stop,
    )


def to_renewablex_decision(out: ControllerStepOutput) -> RenewableXDispatchDecision:
    """Translate ControllerStepOutput to RenewableX signed BESS decision format."""
    # RenewableX uses positive = charge, negative = discharge
    bess_signed_kw = out.bess_charge_kw - out.bess_discharge_kw
    return RenewableXDispatchDecision(
        pv_command_kw=out.pv_command_kw,
        curtailment_kw=out.pv_curtailment_kw,
        bess_command_kw=bess_signed_kw,
        biomass_command_kw=out.biomass_command_kw,
        biomass_start_request=out.biomass_start_request,
        biomass_stop_request=out.biomass_stop_request,
        grid_import_kw=out.grid_import_kw,
        grid_export_kw=out.grid_export_kw,
        served_load_kw=out.served_load_kw,
        unmet_load_kw=out.unmet_load_kw,
        operating_mode=out.operating_mode,
        constraint_status=out.constraint_status,
        violations=list(out.violations),
        active_rule_ids=list(out.active_rule_ids),
        trace_reason=out.trace_reason,
    )


class RenewableXEMSAdapter:
    """In-process adapter executing one RenewableX simulation dispatch step."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        rule_set: EMSRuleSet | None = None,
        *,
        use_fsm: bool = False,
    ) -> None:
        self._config = config
        self._use_fsm = use_fsm
        self._engine = PowerDispatchEngine(config, rule_set=rule_set)
        self._supervisor = SafetySupervisor(config)
        self._controller = HybridController(config, rule_set=rule_set)

    @property
    def controller_state(self) -> ControllerState:
        """Current internal FSM state when use_fsm is enabled."""
        return self._controller.state

    def reset(self, initial_state: ControllerState | None = None) -> None:
        """Reset internal FSM state for a fresh simulation sweep."""
        self._controller.reset(initial_state)

    def step_from_context(
        self,
        ctx: RenewableXDispatchContextProtocol,
        *,
        dt_seconds: float = 3600.0,
        operating_mode: OperatingMode | None = None,
        state: ControllerState | None = None,
        grid_connected: bool | None = None,
        grid_export_target_kw: float | None = None,
        grid_export_limit_kw: float | None = None,
        biomass_maintenance_mode: bool = False,
    ) -> tuple[RenewableXDispatchDecision, ControllerStepOutput]:
        """Run one deterministic dispatch step directly from a RenewableX context."""
        step_in = from_renewablex_context(
            ctx,
            dt_seconds=dt_seconds,
            grid_connected=grid_connected,
            grid_export_target_kw=grid_export_target_kw,
            grid_export_limit_kw=grid_export_limit_kw,
            biomass_maintenance_mode=biomass_maintenance_mode,
        )
        if self._use_fsm and operating_mode is None:
            if state is not None:
                step_out, _ = self._controller.evaluate_step(step_in, state)
            else:
                step_out = self._controller.step(step_in)
            return to_renewablex_decision(step_out), step_out

        if operating_mode is not None:
            mode = operating_mode
        elif not step_in.grid_connected:
            mode = OperatingMode.ISLANDED
        elif step_in.biomass_maintenance_mode:
            mode = OperatingMode.BIOMASS_MAINTENANCE
        elif step_in.grid_export_limit_kw is not None:
            mode = OperatingMode.PEAK_CUT
        else:
            mode = OperatingMode.GRID_CONNECTED_NORMAL

        step_out = self._engine.dispatch_step(
            step_in,
            mode,
            supervisor=self._supervisor,
            state=state,
        )
        return to_renewablex_decision(step_out), step_out
