"""Module 8: Lightweight controller simulation and invariant validation harness."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.bess_manager import BESSSupervisoryManager
from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ModeTransitionEvent,
)
from renewablex_controller.core.controller import HybridController
from renewablex_controller.core.ems_rules import EMSRuleSet
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.power_balance import verify_step_output
from renewablex_controller.core.units import (
    BiomassStatus,
    ConstraintStatus,
    OperatingMode,
    dt_seconds_to_hours,
)


class ScenarioStepProfile(BaseModel):
    """Input profile for a single simulation step in a validation scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    dt_seconds: float | None = Field(default=None, gt=0.0)
    hour_of_day: int = Field(ge=0, le=23)
    month: int = Field(default=6, ge=1, le=12)
    local_load_kw: float = Field(ge=0.0)
    parasitic_load_kw: float = Field(default=40.0, ge=0.0)
    pv_available_kw: float = Field(ge=0.0)
    grid_connected: bool = True
    allow_grid_import: bool = True
    allow_grid_export: bool = True
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
    tariff_per_kwh: float = 0.12


class ScenarioDefinition(BaseModel):
    """Complete deterministic validation scenario definition."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1)
    description: str = ""
    dt_seconds: float = Field(default=3600.0, gt=0.0)
    initial_soc_fraction: float = Field(default=0.50, ge=0.0, le=1.0)
    initial_biomass_status: BiomassStatus = BiomassStatus.RUNNING
    initial_biomass_power_kw: float = Field(default=1400.0, ge=0.0)
    biomass_fuel_remaining_tons: float | None = Field(default=100.0, ge=0.0)
    steps: list[ScenarioStepProfile] = Field(min_length=1)


class SimulationStepRecord(BaseModel):
    """Per-step trace, power flow, SoC, and invariant verification record."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    step_idx: int = Field(ge=0)
    dt_seconds: float = Field(gt=0.0)
    operating_mode: OperatingMode
    sequence_state: str | None = None
    soc_start_fraction: float = Field(ge=0.0, le=1.0)
    soc_end_fraction: float = Field(ge=0.0, le=1.0)
    local_load_kw: float = Field(ge=0.0)
    parasitic_load_kw: float = Field(ge=0.0)
    pv_available_kw: float = Field(ge=0.0)
    pv_command_kw: float = Field(ge=0.0)
    pv_curtailment_kw: float = Field(ge=0.0)
    biomass_status: BiomassStatus
    biomass_command_kw: float = Field(ge=0.0)
    bess_charge_kw: float = Field(ge=0.0)
    bess_discharge_kw: float = Field(ge=0.0)
    grid_import_kw: float = Field(ge=0.0)
    grid_export_kw: float = Field(ge=0.0)
    p_pcc_export_kw: float
    served_load_kw: float = Field(ge=0.0)
    unmet_load_kw: float = Field(ge=0.0)
    constraint_status: ConstraintStatus
    balance_residual_kw: float
    balanced: bool
    mutual_exclusion_ok: bool
    violations: list[str] = Field(default_factory=list)
    active_rule_ids: list[str] = Field(default_factory=list)
    trace_reason: str


class SimulationRunSummary(BaseModel):
    """Aggregated energy, SoC, mode, and invariant summary for a scenario run."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    scenario_name: str
    step_count: int = Field(ge=1)
    total_duration_hours: float = Field(ge=0.0)
    served_energy_kwh: float = Field(ge=0.0)
    unmet_energy_kwh: float = Field(ge=0.0)
    pv_energy_kwh: float = Field(ge=0.0)
    pv_curtailed_energy_kwh: float = Field(ge=0.0)
    biomass_energy_kwh: float = Field(ge=0.0)
    bess_charged_energy_kwh: float = Field(ge=0.0)
    bess_discharged_energy_kwh: float = Field(ge=0.0)
    grid_import_energy_kwh: float = Field(ge=0.0)
    grid_export_energy_kwh: float = Field(ge=0.0)
    min_soc_fraction: float = Field(ge=0.0, le=1.0)
    max_soc_fraction: float = Field(ge=0.0, le=1.0)
    max_abs_balance_residual_kw: float = Field(ge=0.0)
    modes_visited: list[str]
    rejected_steps: int = Field(ge=0)
    infeasible_steps: int = Field(ge=0)
    all_invariants_passed: bool


class SimulationRunResult(BaseModel):
    """Complete simulation output containing summary, step records, and events."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    scenario_name: str
    summary: SimulationRunSummary
    records: list[SimulationStepRecord]
    events: list[ModeTransitionEvent] = Field(default_factory=list)


class ControllerSimulationHarness:
    """Runs deterministic multi-step controller scenarios without physical hardware."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        rule_set: EMSRuleSet | None = None,
    ) -> None:
        self._config = config
        self._rule_set = rule_set
        self._bess_manager = BESSSupervisoryManager(config.bess)

    def run_scenario(
        self,
        scenario: ScenarioDefinition,
        *,
        initial_state: ControllerState | None = None,
    ) -> SimulationRunResult:
        """Execute all steps of a ScenarioDefinition and verify invariants."""
        controller = HybridController(
            self._config,
            rule_set=self._rule_set,
            initial_state=initial_state,
        )
        tol = self._config.control_policy.power_balance_tolerance_kw

        soc = scenario.initial_soc_fraction
        bio_status = scenario.initial_biomass_status
        bio_power = scenario.initial_biomass_power_kw
        fuel_tons = scenario.biomass_fuel_remaining_tons

        records: list[SimulationStepRecord] = []
        modes_visited: list[str] = []

        total_hours = 0.0
        served_kwh = 0.0
        unmet_kwh = 0.0
        pv_kwh = 0.0
        pv_curt_kwh = 0.0
        bio_kwh = 0.0
        bess_chg_kwh = 0.0
        bess_dis_kwh = 0.0
        grid_imp_kwh = 0.0
        grid_exp_kwh = 0.0
        min_soc = soc
        max_soc = soc
        max_abs_res = 0.0
        rejected_steps = 0
        infeasible_steps = 0
        all_invariants_passed = True

        for idx, profile in enumerate(scenario.steps):
            dt_sec = profile.dt_seconds if profile.dt_seconds is not None else scenario.dt_seconds
            dt_hours = dt_seconds_to_hours(dt_sec)

            bio_avail = (
                self._config.biomass.operating_max_kw
                if self._config.biomass.available and not profile.biomass_maintenance_mode
                else 0.0
            )
            step_in = ControllerStepInput(
                step_idx=idx,
                dt_seconds=dt_sec,
                hour_of_day=profile.hour_of_day,
                month=profile.month,
                local_load_kw=profile.local_load_kw,
                parasitic_load_kw=profile.parasitic_load_kw,
                pv_available_kw=profile.pv_available_kw,
                bess_soc_fraction=soc,
                bess_max_charge_kw=self._config.bess.max_charge_kw,
                bess_max_discharge_kw=self._config.bess.max_discharge_kw,
                biomass_status=bio_status,
                biomass_available_kw=bio_avail,
                biomass_current_power_kw=bio_power,
                biomass_min_load_kw=self._config.biomass.min_load_kw,
                biomass_can_start=(bio_status == BiomassStatus.OFF),
                biomass_can_stop=(bio_status == BiomassStatus.RUNNING),
                biomass_fuel_remaining_tons=fuel_tons,
                allow_grid_import=profile.allow_grid_import,
                allow_grid_export=profile.allow_grid_export,
                tariff_per_kwh=profile.tariff_per_kwh,
                grid_connected=profile.grid_connected,
                grid_outage_duration_seconds=profile.grid_outage_duration_seconds,
                grid_export_target_kw=profile.grid_export_target_kw,
                grid_export_limit_kw=profile.grid_export_limit_kw,
                biomass_maintenance_mode=profile.biomass_maintenance_mode,
                sync_permissive=profile.sync_permissive,
                comm_healthy=profile.comm_healthy,
                emergency_stop=profile.emergency_stop,
                telemetry_age_seconds=profile.telemetry_age_seconds,
                operator_ack_restoration=profile.operator_ack_restoration,
                operator_ack_fault_reset=profile.operator_ack_fault_reset,
            )

            out = controller.step(step_in)
            report = verify_step_output(step_in, out, tolerance_kw=tol)
            mutual_ok = report.bess_mutual_exclusion_ok and report.grid_mutual_exclusion_ok

            next_soc = self._bess_manager.project_next_soc(
                soc,
                charge_kw=out.bess_charge_kw,
                discharge_kw=out.bess_discharge_kw,
                dt_seconds=dt_sec,
            )
            soc_ok = (
                self._config.bess.soc_min_fraction - 1e-9
                <= next_soc
                <= self._config.bess.soc_max_fraction + 1e-9
            )

            if out.constraint_status == ConstraintStatus.REJECTED:
                rejected_steps += 1
                step_balanced = abs(out.balance_residual_kw + step_in.parasitic_load_kw) < tol
            elif out.constraint_status == ConstraintStatus.INFEASIBLE:
                infeasible_steps += 1
                step_balanced = report.is_balanced
            else:
                step_balanced = report.is_balanced
                max_abs_res = max(max_abs_res, abs(report.net_residual_kw))

            balanced_or_infeasible = (
                step_balanced or out.constraint_status == ConstraintStatus.INFEASIBLE
            )
            if not (mutual_ok and soc_ok and balanced_or_infeasible):
                all_invariants_passed = False

            if out.operating_mode.value not in modes_visited:
                modes_visited.append(out.operating_mode.value)

            records.append(
                SimulationStepRecord(
                    step_idx=idx,
                    dt_seconds=dt_sec,
                    operating_mode=out.operating_mode,
                    sequence_state=controller.state.active_sequence,
                    soc_start_fraction=soc,
                    soc_end_fraction=next_soc,
                    local_load_kw=step_in.local_load_kw,
                    parasitic_load_kw=step_in.parasitic_load_kw,
                    pv_available_kw=step_in.pv_available_kw,
                    pv_command_kw=out.pv_command_kw,
                    pv_curtailment_kw=out.pv_curtailment_kw,
                    biomass_status=bio_status,
                    biomass_command_kw=out.biomass_command_kw,
                    bess_charge_kw=out.bess_charge_kw,
                    bess_discharge_kw=out.bess_discharge_kw,
                    grid_import_kw=out.grid_import_kw,
                    grid_export_kw=out.grid_export_kw,
                    p_pcc_export_kw=out.p_pcc_export_kw,
                    served_load_kw=out.served_load_kw,
                    unmet_load_kw=out.unmet_load_kw,
                    constraint_status=out.constraint_status,
                    balance_residual_kw=out.balance_residual_kw,
                    balanced=step_balanced,
                    mutual_exclusion_ok=mutual_ok,
                    violations=list(out.violations),
                    active_rule_ids=list(out.active_rule_ids),
                    trace_reason=out.trace_reason,
                )
            )

            total_hours += dt_hours
            served_kwh += out.served_load_kw * dt_hours
            unmet_kwh += out.unmet_load_kw * dt_hours
            pv_kwh += out.pv_command_kw * dt_hours
            pv_curt_kwh += out.pv_curtailment_kw * dt_hours
            bio_kwh += out.biomass_command_kw * dt_hours
            bess_chg_kwh += out.bess_charge_kw * dt_hours
            bess_dis_kwh += out.bess_discharge_kw * dt_hours
            grid_imp_kwh += out.grid_import_kw * dt_hours
            grid_exp_kwh += out.grid_export_kw * dt_hours
            min_soc = min(min_soc, next_soc)
            max_soc = max(max_soc, next_soc)

            # Lightweight next-step plant stub updates
            soc = next_soc
            bio_status, bio_power = self._advance_biomass_stub(
                bio_status=bio_status,
                bio_command_kw=out.biomass_command_kw,
                start_req=out.biomass_start_request,
                stop_req=out.biomass_stop_request,
                maintenance=profile.biomass_maintenance_mode,
            )

        summary = SimulationRunSummary(
            scenario_name=scenario.name,
            step_count=len(records),
            total_duration_hours=total_hours,
            served_energy_kwh=served_kwh,
            unmet_energy_kwh=unmet_kwh,
            pv_energy_kwh=pv_kwh,
            pv_curtailed_energy_kwh=pv_curt_kwh,
            biomass_energy_kwh=bio_kwh,
            bess_charged_energy_kwh=bess_chg_kwh,
            bess_discharged_energy_kwh=bess_dis_kwh,
            grid_import_energy_kwh=grid_imp_kwh,
            grid_export_energy_kwh=grid_exp_kwh,
            min_soc_fraction=min_soc,
            max_soc_fraction=max_soc,
            max_abs_balance_residual_kw=max_abs_res,
            modes_visited=modes_visited,
            rejected_steps=rejected_steps,
            infeasible_steps=infeasible_steps,
            all_invariants_passed=all_invariants_passed,
        )

        return SimulationRunResult(
            scenario_name=scenario.name,
            summary=summary,
            records=records,
            events=list(controller.state.event_log),
        )

    def _advance_biomass_stub(
        self,
        *,
        bio_status: BiomassStatus,
        bio_command_kw: float,
        start_req: bool,
        stop_req: bool,
        maintenance: bool,
    ) -> tuple[BiomassStatus, float]:
        if maintenance or stop_req:
            if bio_command_kw <= 0.0:
                return BiomassStatus.OFF, 0.0
            return BiomassStatus.STOPPING, bio_command_kw
        if bio_status == BiomassStatus.OFF and start_req:
            return BiomassStatus.STARTING, 0.0
        if bio_status == BiomassStatus.STARTING:
            return BiomassStatus.RUNNING, self._config.biomass.min_load_kw
        if bio_status == BiomassStatus.STOPPING:
            if bio_command_kw <= 0.0:
                return BiomassStatus.OFF, 0.0
            return BiomassStatus.STOPPING, bio_command_kw
        return bio_status, bio_command_kw
