"""Unified HybridController facade orchestrating Modules 1 to 6."""

from __future__ import annotations

from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
)
from renewablex_controller.core.dispatch_engine import PowerDispatchEngine
from renewablex_controller.core.ems_rules import EMSRuleSet
from renewablex_controller.core.mode_manager import OperatingModeManager
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.safety_supervisor import SafetySupervisor
from renewablex_controller.core.sequence_manager import SequenceManager
from renewablex_controller.core.units import OperatingMode


class HybridController:
    """Deterministic supervisory controller coordinating Modules 1 to 6."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        rule_set: EMSRuleSet | None = None,
        initial_state: ControllerState | None = None,
        *,
        islanding_prep_steps: int = 1,
    ) -> None:
        self._config = config
        self._rule_set = rule_set
        self._sequence_manager = SequenceManager(
            config,
            islanding_prep_steps=islanding_prep_steps,
        )
        self._mode_manager = OperatingModeManager(
            config,
            sequence_manager=self._sequence_manager,
        )
        self._dispatch_engine = PowerDispatchEngine(config, rule_set=rule_set)
        self._supervisor = SafetySupervisor(config)
        self._state = initial_state if initial_state is not None else ControllerState()

    @property
    def config(self) -> PlantOperationalConfig:
        """Plant operational configuration."""
        return self._config

    @property
    def rule_set(self) -> EMSRuleSet | None:
        """Optional active JSON EMS rule set."""
        return self._rule_set

    @property
    def state(self) -> ControllerState:
        """Current immutable controller state."""
        return self._state

    def reset(self, initial_state: ControllerState | None = None) -> ControllerState:
        """Reset internal controller state and return the active state."""
        self._state = initial_state if initial_state is not None else ControllerState()
        return self._state

    def evaluate_step(
        self,
        step_in: ControllerStepInput,
        state: ControllerState,
        *,
        requested_mode: OperatingMode | None = None,
    ) -> tuple[ControllerStepOutput, ControllerState]:
        """Pure functional transition: (step_in, state) -> (step_out, next_state)."""
        mode_res = self._mode_manager.evaluate(
            step_in,
            state,
            requested_mode=requested_mode,
        )
        proposed = self._dispatch_engine.propose(
            step_in,
            mode_res.operating_mode,
            sequence_state=mode_res.sequence_state,
        )
        step_out = self._supervisor.supervise(step_in, proposed, state=state)

        if mode_res.violations:
            merged_violations = list(step_out.violations)
            for code in mode_res.violations:
                if code not in merged_violations:
                    merged_violations.append(code)
            step_out = step_out.model_copy(update={"violations": merged_violations})

        same_mode = mode_res.operating_mode == state.operating_mode
        bio_elapsed = (
            state.biomass_state_elapsed_seconds + step_in.dt_seconds
            if same_mode
            else step_in.dt_seconds
        )

        next_state = ControllerState(
            operating_mode=mode_res.operating_mode,
            previous_grid_mode=mode_res.previous_grid_mode,
            outage_elapsed_seconds=mode_res.outage_elapsed_seconds,
            active_sequence=mode_res.active_sequence,
            sequence_step=mode_res.sequence_step,
            sequence_elapsed_seconds=mode_res.sequence_elapsed_seconds,
            last_biomass_command_kw=step_out.biomass_command_kw,
            biomass_state_elapsed_seconds=bio_elapsed,
            event_log=[*state.event_log, *mode_res.events],
        )
        return step_out, next_state

    def step(
        self,
        step_in: ControllerStepInput,
        *,
        requested_mode: OperatingMode | None = None,
    ) -> ControllerStepOutput:
        """Execute one deterministic control step and advance internal state."""
        step_out, next_state = self.evaluate_step(
            step_in,
            self._state,
            requested_mode=requested_mode,
        )
        self._state = next_state
        return step_out
