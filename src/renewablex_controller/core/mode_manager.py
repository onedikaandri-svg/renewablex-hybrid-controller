"""Module 2: Deterministic 8-mode Operating Mode Finite State Machine."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ModeTransitionEvent,
)
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.sequence_manager import SequenceKind, SequenceManager
from renewablex_controller.core.units import (
    BiomassStatus,
    CommLossAction,
    OperatingMode,
)

_ALLOWED_TRANSITIONS: dict[OperatingMode, frozenset[OperatingMode]] = {
    OperatingMode.GRID_CONNECTED_NORMAL: frozenset(
        {
            OperatingMode.PEAK_CUT,
            OperatingMode.BIOMASS_MAINTENANCE,
            OperatingMode.MOMENTARY_OUTAGE,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.PEAK_CUT: frozenset(
        {
            OperatingMode.GRID_CONNECTED_NORMAL,
            OperatingMode.BIOMASS_MAINTENANCE,
            OperatingMode.MOMENTARY_OUTAGE,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.BIOMASS_MAINTENANCE: frozenset(
        {
            OperatingMode.GRID_CONNECTED_NORMAL,
            OperatingMode.PEAK_CUT,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.MOMENTARY_OUTAGE: frozenset(
        {
            OperatingMode.GRID_CONNECTED_NORMAL,
            OperatingMode.PEAK_CUT,
            OperatingMode.PROLONGED_OUTAGE,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.PROLONGED_OUTAGE: frozenset(
        {
            OperatingMode.ISLANDED,
            OperatingMode.GRID_RESTORATION,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.ISLANDED: frozenset(
        {
            OperatingMode.GRID_RESTORATION,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.GRID_RESTORATION: frozenset(
        {
            OperatingMode.GRID_CONNECTED_NORMAL,
            OperatingMode.PEAK_CUT,
            OperatingMode.PROLONGED_OUTAGE,
            OperatingMode.EMERGENCY_FAULT,
        }
    ),
    OperatingMode.EMERGENCY_FAULT: frozenset(
        {
            OperatingMode.GRID_RESTORATION,
            OperatingMode.PROLONGED_OUTAGE,
        }
    ),
}


class ModeEvaluationResult(BaseModel):
    """Deterministic FSM evaluation result for one step."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    operating_mode: OperatingMode
    previous_grid_mode: OperatingMode
    outage_elapsed_seconds: float = Field(ge=0.0)
    active_sequence: str | None = None
    sequence_step: int = Field(default=0, ge=0)
    sequence_elapsed_seconds: float = Field(default=0.0, ge=0.0)
    sequence_state: str | None = None
    events: list[ModeTransitionEvent] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class OperatingModeManager:
    """Deterministic 8-state Operating Mode FSM with guards, timers, and audit log."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        sequence_manager: SequenceManager | None = None,
    ) -> None:
        self._config = config
        self._sequence_manager = (
            sequence_manager if sequence_manager is not None else SequenceManager(config)
        )

    @staticmethod
    def validate_transition(from_mode: OperatingMode, to_mode: OperatingMode) -> bool:
        """Return True if transitioning from from_mode to to_mode is permitted."""
        if from_mode == to_mode:
            return True
        return to_mode in _ALLOWED_TRANSITIONS.get(from_mode, frozenset())

    def evaluate(
        self,
        step_in: ControllerStepInput,
        state: ControllerState,
        *,
        requested_mode: OperatingMode | None = None,
    ) -> ModeEvaluationResult:
        """Evaluate FSM guards, outage timers, sequences, and explicit requests."""
        policy = self._config.control_policy
        current_mode = state.operating_mode
        prev_grid_mode = state.previous_grid_mode
        outage_elapsed = state.outage_elapsed_seconds
        active_seq = state.active_sequence
        seq_step = state.sequence_step
        seq_elapsed = state.sequence_elapsed_seconds
        seq_state: str | None = None
        events: list[ModeTransitionEvent] = []
        violations: list[str] = []

        # 1. Validate explicit external mode request if supplied
        if requested_mode is not None and requested_mode != current_mode:
            if not self.validate_transition(current_mode, requested_mode):
                reason = f"INVALID_MODE_TRANSITION:{current_mode.value}->{requested_mode.value}"
                violations.append(reason)
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=requested_mode,
                        reason=reason,
                        accepted=False,
                    )
                )

        # 2. Highest-priority fault guard (emergency stop or comm/stale fault)
        telemetry_stale = step_in.telemetry_age_seconds > policy.max_telemetry_age_seconds
        comm_fault = (not step_in.comm_healthy) or telemetry_stale
        fault_triggered = step_in.emergency_stop or (
            comm_fault and policy.comm_loss_action == CommLossAction.EMERGENCY_FAULT
        )

        if fault_triggered:
            if current_mode != OperatingMode.EMERGENCY_FAULT:
                fault_reason = (
                    "emergency_stop"
                    if step_in.emergency_stop
                    else "telemetry_comm_fault"
                )
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.EMERGENCY_FAULT,
                        reason=fault_reason,
                        accepted=True,
                    )
                )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.EMERGENCY_FAULT,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=outage_elapsed,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="emergency_fault_active",
                events=events,
                violations=violations,
            )

        # 3. Mode-specific deterministic transitions
        if current_mode == OperatingMode.EMERGENCY_FAULT:
            ack_ok = (
                not policy.require_operator_ack_for_fault_reset
                or step_in.operator_ack_fault_reset
            )
            if ack_ok:
                target = (
                    OperatingMode.GRID_RESTORATION
                    if step_in.grid_connected
                    else OperatingMode.PROLONGED_OUTAGE
                )
                next_seq = (
                    SequenceKind.GRID_RESTORATION.value
                    if target == OperatingMode.GRID_RESTORATION
                    else SequenceKind.ISLANDING_PREP.value
                )
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=target,
                        reason="fault_cleared_and_acknowledged",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=target,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=0.0 if step_in.grid_connected else outage_elapsed,
                    active_sequence=next_seq,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state=f"{next_seq}_started",
                    events=events,
                    violations=violations,
                )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.EMERGENCY_FAULT,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=outage_elapsed,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="emergency_fault_waiting_reset_ack",
                events=events,
                violations=violations,
            )

        if current_mode in {OperatingMode.GRID_CONNECTED_NORMAL, OperatingMode.PEAK_CUT}:
            prev_grid_mode = current_mode
            if not step_in.grid_connected:
                eff_outage = max(step_in.dt_seconds, step_in.grid_outage_duration_seconds)
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.MOMENTARY_OUTAGE,
                        reason="grid_connection_lost",
                        accepted=True,
                    )
                )
                if eff_outage >= policy.momentary_outage_threshold_seconds:
                    events.append(
                        ModeTransitionEvent(
                            step_idx=step_in.step_idx,
                            from_mode=OperatingMode.MOMENTARY_OUTAGE,
                            to_mode=OperatingMode.PROLONGED_OUTAGE,
                            reason="outage_threshold_exceeded",
                            accepted=True,
                        )
                    )
                    return ModeEvaluationResult(
                        operating_mode=OperatingMode.PROLONGED_OUTAGE,
                        previous_grid_mode=prev_grid_mode,
                        outage_elapsed_seconds=eff_outage,
                        active_sequence=SequenceKind.ISLANDING_PREP.value,
                        sequence_step=0,
                        sequence_elapsed_seconds=0.0,
                        sequence_state="islanding_prep_started",
                        events=events,
                        violations=violations,
                    )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.MOMENTARY_OUTAGE,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=eff_outage,
                    active_sequence=None,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="momentary_outage_monitoring",
                    events=events,
                    violations=violations,
                )

            if step_in.biomass_maintenance_mode:
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.BIOMASS_MAINTENANCE,
                        reason="biomass_maintenance_requested",
                        accepted=True,
                    )
                )
                if step_in.biomass_status == BiomassStatus.RUNNING:
                    seq_res = self._sequence_manager.advance(
                        SequenceKind.BIOMASS_MAINTENANCE_ENTRY,
                        step_in,
                        current_step=0,
                        elapsed_seconds=0.0,
                    )
                    active_seq = seq_res.active_sequence
                    seq_step = seq_res.sequence_step
                    seq_elapsed = seq_res.sequence_elapsed_seconds
                    seq_state = seq_res.sequence_state
                else:
                    active_seq = None
                    seq_step = 0
                    seq_elapsed = 0.0
                    seq_state = "biomass_maintenance_active"
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.BIOMASS_MAINTENANCE,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=0.0,
                    active_sequence=active_seq,
                    sequence_step=seq_step,
                    sequence_elapsed_seconds=seq_elapsed,
                    sequence_state=seq_state,
                    events=events,
                    violations=violations,
                )

            target_grid = self._target_grid_mode(step_in)
            if target_grid != current_mode:
                reason = (
                    "export_limit_active"
                    if target_grid == OperatingMode.PEAK_CUT
                    else "export_limit_cleared"
                )
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=target_grid,
                        reason=reason,
                        accepted=True,
                    )
                )
            return ModeEvaluationResult(
                operating_mode=target_grid,
                previous_grid_mode=target_grid,
                outage_elapsed_seconds=0.0,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state=None,
                events=events,
                violations=violations,
            )

        if current_mode == OperatingMode.BIOMASS_MAINTENANCE:
            if not step_in.biomass_maintenance_mode and step_in.grid_connected:
                seq_res = self._sequence_manager.advance(
                    SequenceKind.BIOMASS_MAINTENANCE_EXIT,
                    step_in,
                    current_step=seq_step,
                    elapsed_seconds=seq_elapsed,
                )
                if seq_res.completed:
                    target_grid = self._target_grid_mode(step_in)
                    events.append(
                        ModeTransitionEvent(
                            step_idx=step_in.step_idx,
                            from_mode=current_mode,
                            to_mode=target_grid,
                            reason="biomass_maintenance_completed",
                            accepted=True,
                        )
                    )
                    return ModeEvaluationResult(
                        operating_mode=target_grid,
                        previous_grid_mode=target_grid,
                        outage_elapsed_seconds=0.0,
                        active_sequence=None,
                        sequence_step=0,
                        sequence_elapsed_seconds=0.0,
                        sequence_state=seq_res.sequence_state,
                        events=events,
                        violations=violations,
                    )
            elif step_in.biomass_status == BiomassStatus.RUNNING:
                seq_res = self._sequence_manager.advance(
                    SequenceKind.BIOMASS_MAINTENANCE_ENTRY,
                    step_in,
                    current_step=seq_step,
                    elapsed_seconds=seq_elapsed,
                )
                active_seq = seq_res.active_sequence
                seq_step = seq_res.sequence_step
                seq_elapsed = seq_res.sequence_elapsed_seconds
                seq_state = seq_res.sequence_state
            else:
                active_seq = None
                seq_step = 0
                seq_elapsed = 0.0
                seq_state = "biomass_maintenance_active"
            return ModeEvaluationResult(
                operating_mode=OperatingMode.BIOMASS_MAINTENANCE,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=0.0,
                active_sequence=active_seq,
                sequence_step=seq_step,
                sequence_elapsed_seconds=seq_elapsed,
                sequence_state=seq_state,
                events=events,
                violations=violations,
            )

        if current_mode == OperatingMode.MOMENTARY_OUTAGE:
            if step_in.grid_connected:
                eff_outage = max(outage_elapsed, step_in.grid_outage_duration_seconds)
                if eff_outage < policy.momentary_outage_threshold_seconds:
                    target_grid = self._target_grid_mode(step_in)
                    events.append(
                        ModeTransitionEvent(
                            step_idx=step_in.step_idx,
                            from_mode=current_mode,
                            to_mode=target_grid,
                            reason="momentary_outage_cleared",
                            accepted=True,
                        )
                    )
                    return ModeEvaluationResult(
                        operating_mode=target_grid,
                        previous_grid_mode=target_grid,
                        outage_elapsed_seconds=0.0,
                        active_sequence=None,
                        sequence_step=0,
                        sequence_elapsed_seconds=0.0,
                        sequence_state="grid_recovered_momentary",
                        events=events,
                        violations=violations,
                    )
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.PROLONGED_OUTAGE,
                        reason="outage_threshold_exceeded_before_recovery",
                        accepted=True,
                    )
                )
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=OperatingMode.PROLONGED_OUTAGE,
                        to_mode=OperatingMode.GRID_RESTORATION,
                        reason="grid_restored_awaiting_sync",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.GRID_RESTORATION,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=0.0,
                    active_sequence=SequenceKind.GRID_RESTORATION.value,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="grid_restoration_started",
                    events=events,
                    violations=violations,
                )

            eff_outage = max(
                outage_elapsed + step_in.dt_seconds,
                step_in.grid_outage_duration_seconds,
            )
            if eff_outage >= policy.momentary_outage_threshold_seconds:
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.PROLONGED_OUTAGE,
                        reason="outage_threshold_exceeded",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.PROLONGED_OUTAGE,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=eff_outage,
                    active_sequence=SequenceKind.ISLANDING_PREP.value,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="islanding_prep_started",
                    events=events,
                    violations=violations,
                )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.MOMENTARY_OUTAGE,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=eff_outage,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="momentary_outage_monitoring",
                events=events,
                violations=violations,
            )

        if current_mode == OperatingMode.PROLONGED_OUTAGE:
            if step_in.grid_connected:
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.GRID_RESTORATION,
                        reason="grid_restored_during_islanding_prep",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.GRID_RESTORATION,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=0.0,
                    active_sequence=SequenceKind.GRID_RESTORATION.value,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="grid_restoration_started",
                    events=events,
                    violations=violations,
                )

            eff_outage = max(
                outage_elapsed + step_in.dt_seconds,
                step_in.grid_outage_duration_seconds,
            )
            seq_res = self._sequence_manager.advance(
                SequenceKind.ISLANDING_PREP,
                step_in,
                current_step=seq_step,
                elapsed_seconds=seq_elapsed,
            )
            if seq_res.completed:
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.ISLANDED,
                        reason="islanding_prep_complete",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.ISLANDED,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=eff_outage,
                    active_sequence=None,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state=seq_res.sequence_state,
                    events=events,
                    violations=violations,
                )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.PROLONGED_OUTAGE,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=eff_outage,
                active_sequence=seq_res.active_sequence,
                sequence_step=seq_res.sequence_step,
                sequence_elapsed_seconds=seq_res.sequence_elapsed_seconds,
                sequence_state=seq_res.sequence_state,
                events=events,
                violations=violations,
            )

        if current_mode == OperatingMode.ISLANDED:
            if step_in.grid_connected:
                events.append(
                    ModeTransitionEvent(
                        step_idx=step_in.step_idx,
                        from_mode=current_mode,
                        to_mode=OperatingMode.GRID_RESTORATION,
                        reason="grid_restored_awaiting_sync",
                        accepted=True,
                    )
                )
                return ModeEvaluationResult(
                    operating_mode=OperatingMode.GRID_RESTORATION,
                    previous_grid_mode=prev_grid_mode,
                    outage_elapsed_seconds=0.0,
                    active_sequence=SequenceKind.GRID_RESTORATION.value,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="grid_restoration_started",
                    events=events,
                    violations=violations,
                )
            eff_outage = max(
                outage_elapsed + step_in.dt_seconds,
                step_in.grid_outage_duration_seconds,
            )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.ISLANDED,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=eff_outage,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="islanded_active",
                events=events,
                violations=violations,
            )

        # OperatingMode.GRID_RESTORATION
        if not step_in.grid_connected:
            events.append(
                ModeTransitionEvent(
                    step_idx=step_in.step_idx,
                    from_mode=current_mode,
                    to_mode=OperatingMode.PROLONGED_OUTAGE,
                    reason="grid_lost_during_restoration",
                    accepted=True,
                )
            )
            return ModeEvaluationResult(
                operating_mode=OperatingMode.PROLONGED_OUTAGE,
                previous_grid_mode=prev_grid_mode,
                outage_elapsed_seconds=step_in.dt_seconds,
                active_sequence=SequenceKind.ISLANDING_PREP.value,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="islanding_prep_started",
                events=events,
                violations=violations,
            )

        seq_res = self._sequence_manager.advance(
            SequenceKind.GRID_RESTORATION,
            step_in,
            current_step=seq_step,
            elapsed_seconds=seq_elapsed,
        )
        if seq_res.completed:
            target_grid = self._target_grid_mode(step_in)
            events.append(
                ModeTransitionEvent(
                    step_idx=step_in.step_idx,
                    from_mode=current_mode,
                    to_mode=target_grid,
                    reason="grid_synchronized_and_reconnected",
                    accepted=True,
                )
            )
            return ModeEvaluationResult(
                operating_mode=target_grid,
                previous_grid_mode=target_grid,
                outage_elapsed_seconds=0.0,
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state=seq_res.sequence_state,
                events=events,
                violations=violations,
            )

        return ModeEvaluationResult(
            operating_mode=OperatingMode.GRID_RESTORATION,
            previous_grid_mode=prev_grid_mode,
            outage_elapsed_seconds=0.0,
            active_sequence=seq_res.active_sequence,
            sequence_step=seq_res.sequence_step,
            sequence_elapsed_seconds=seq_res.sequence_elapsed_seconds,
            sequence_state=seq_res.sequence_state,
            events=events,
            violations=violations,
        )

    @staticmethod
    def _target_grid_mode(step_in: ControllerStepInput) -> OperatingMode:
        return (
            OperatingMode.PEAK_CUT
            if step_in.grid_export_limit_kw is not None
            else OperatingMode.GRID_CONNECTED_NORMAL
        )
