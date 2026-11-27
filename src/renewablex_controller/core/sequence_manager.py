"""Module 6: Simulated multi-step supervisory operating sequences."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.units import BiomassStatus


class SequenceKind(StrEnum):
    """Supported simulated supervisory transition sequences."""

    ISLANDING_PREP = "islanding_prep"
    BIOMASS_MAINTENANCE_ENTRY = "biomass_maintenance_entry"
    BIOMASS_MAINTENANCE_EXIT = "biomass_maintenance_exit"
    GRID_RESTORATION = "grid_restoration"


class SequenceStepResult(BaseModel):
    """Outcome of advancing a supervisory sequence by one step."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    active_sequence: str | None
    sequence_step: int = Field(ge=0)
    sequence_elapsed_seconds: float = Field(ge=0.0)
    sequence_state: str
    completed: bool
    permissive_blocks: list[str] = Field(default_factory=list)


class SequenceManager:
    """Simulates multi-step mode transition sequences without hardware actuation."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        *,
        islanding_prep_steps: int = 1,
    ) -> None:
        if islanding_prep_steps < 1:
            raise ValueError("islanding_prep_steps must be >= 1")
        self._config = config
        self._islanding_prep_steps = islanding_prep_steps

    def advance(
        self,
        sequence_kind: SequenceKind,
        step_in: ControllerStepInput,
        *,
        current_step: int,
        elapsed_seconds: float,
    ) -> SequenceStepResult:
        """Advance a simulated supervisory sequence by one control step."""
        next_step = current_step + 1
        next_elapsed = elapsed_seconds + step_in.dt_seconds
        blocks: list[str] = []

        if sequence_kind == SequenceKind.ISLANDING_PREP:
            if step_in.grid_connected:
                blocks.append("GRID_STILL_CONNECTED")
                return SequenceStepResult(
                    active_sequence=sequence_kind.value,
                    sequence_step=next_step,
                    sequence_elapsed_seconds=next_elapsed,
                    sequence_state="islanding_prep_aborted_grid_present",
                    completed=False,
                    permissive_blocks=blocks,
                )
            if next_step >= self._islanding_prep_steps:
                return SequenceStepResult(
                    active_sequence=None,
                    sequence_step=0,
                    sequence_elapsed_seconds=0.0,
                    sequence_state="islanding_prep_complete",
                    completed=True,
                    permissive_blocks=[],
                )
            return SequenceStepResult(
                active_sequence=sequence_kind.value,
                sequence_step=next_step,
                sequence_elapsed_seconds=next_elapsed,
                sequence_state=f"islanding_prep_step_{next_step}",
                completed=False,
                permissive_blocks=[],
            )

        if sequence_kind == SequenceKind.BIOMASS_MAINTENANCE_ENTRY:
            if (
                step_in.biomass_status == BiomassStatus.RUNNING
                and step_in.biomass_current_power_kw > 0.0
            ):
                blocks.append("BIOMASS_STILL_RUNNING")
                return SequenceStepResult(
                    active_sequence=sequence_kind.value,
                    sequence_step=next_step,
                    sequence_elapsed_seconds=next_elapsed,
                    sequence_state="biomass_maintenance_cooldown",
                    completed=False,
                    permissive_blocks=blocks,
                )
            return SequenceStepResult(
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="biomass_maintenance_ready",
                completed=True,
                permissive_blocks=[],
            )

        if sequence_kind == SequenceKind.BIOMASS_MAINTENANCE_EXIT:
            if step_in.biomass_maintenance_mode:
                blocks.append("MAINTENANCE_FLAG_ACTIVE")
                return SequenceStepResult(
                    active_sequence=sequence_kind.value,
                    sequence_step=next_step,
                    sequence_elapsed_seconds=next_elapsed,
                    sequence_state="biomass_maintenance_locked",
                    completed=False,
                    permissive_blocks=blocks,
                )
            return SequenceStepResult(
                active_sequence=None,
                sequence_step=0,
                sequence_elapsed_seconds=0.0,
                sequence_state="biomass_maintenance_exited",
                completed=True,
                permissive_blocks=[],
            )

        # SequenceKind.GRID_RESTORATION
        if not step_in.grid_connected:
            blocks.append("GRID_DISCONNECTED")
        if not step_in.sync_permissive:
            blocks.append("SYNC_PERMISSIVE_MISSING")
        if (
            self._config.control_policy.require_operator_ack_for_restoration
            and not step_in.operator_ack_restoration
        ):
            blocks.append("OPERATOR_ACK_RESTORATION_MISSING")

        if blocks:
            return SequenceStepResult(
                active_sequence=sequence_kind.value,
                sequence_step=next_step,
                sequence_elapsed_seconds=next_elapsed,
                sequence_state="grid_restoration_waiting_permissive",
                completed=False,
                permissive_blocks=blocks,
            )

        return SequenceStepResult(
            active_sequence=None,
            sequence_step=0,
            sequence_elapsed_seconds=0.0,
            sequence_state="grid_restoration_complete",
            completed=True,
            permissive_blocks=[],
        )
