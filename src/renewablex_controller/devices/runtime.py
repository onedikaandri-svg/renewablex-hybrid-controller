"""Edge supervisory runtime wiring telemetry, core controller, sink, and audit store."""

from __future__ import annotations

from datetime import datetime

from renewablex_controller.core.contracts import ControllerStepOutput
from renewablex_controller.core.controller import HybridController
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.units import OperatingMode
from renewablex_controller.devices.interfaces import (
    AuditTrailStoreProtocol,
    CommandSinkProtocol,
    InMemoryAuditStore,
    TelemetryAdapterProtocol,
)
from renewablex_controller.devices.models import CommandAuditRecord, PlantTelemetrySnapshot


class EdgeSupervisoryRuntime:
    """Executes one read -> decide -> supervise -> audit-sink cycle."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        controller: HybridController,
        telemetry_adapter: TelemetryAdapterProtocol,
        command_sink: CommandSinkProtocol,
        audit_store: AuditTrailStoreProtocol | None = None,
    ) -> None:
        self._config = config
        self._controller = controller
        self._telemetry_adapter = telemetry_adapter
        self._command_sink = command_sink
        self._audit_store = audit_store if audit_store is not None else InMemoryAuditStore()

    @property
    def controller(self) -> HybridController:
        """Underlying HybridController instance."""
        return self._controller

    @property
    def audit_store(self) -> AuditTrailStoreProtocol:
        """Audit record store."""
        return self._audit_store

    def run_cycle(
        self,
        now_utc: datetime,
        *,
        step_idx: int,
        dt_seconds: float | None = None,
        requested_mode: OperatingMode | None = None,
    ) -> tuple[PlantTelemetrySnapshot, ControllerStepOutput, CommandAuditRecord]:
        """Run one deterministic read-only supervisory control cycle."""
        resolved_dt = (
            dt_seconds
            if dt_seconds is not None
            else self._config.control_policy.default_dt_seconds
        )
        snapshot = self._telemetry_adapter.read_snapshot(now_utc)
        step_in = snapshot.to_step_input(step_idx=step_idx, dt_seconds=resolved_dt)
        step_out = self._controller.step(step_in, requested_mode=requested_mode)
        audit_record = self._command_sink.emit(step_idx, step_out, now_utc)
        self._audit_store.append(audit_record)
        return snapshot, step_out, audit_record
