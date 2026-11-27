"""Hardware-independent device adapter and audit store interfaces (Phase 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from renewablex_controller.core.contracts import ControllerStepOutput
from renewablex_controller.devices.models import CommandAuditRecord, PlantTelemetrySnapshot


class TelemetryAdapterProtocol(Protocol):
    """Protocol for read-only plant telemetry sources."""

    @property
    def adapter_name(self) -> str:
        """Unique adapter identifier."""

    @property
    def read_only(self) -> bool:
        """True when the adapter strictly performs read-only polling."""

    def read_snapshot(self, now_utc: datetime) -> PlantTelemetrySnapshot:
        """Read and tag the latest plant telemetry snapshot."""


class CommandSinkProtocol(Protocol):
    """Protocol for supervisory command sinks (dry-run by default)."""

    @property
    def sink_name(self) -> str:
        """Unique command sink identifier."""

    @property
    def write_enabled(self) -> bool:
        """False when hardware/field writes are disabled."""

    def emit(
        self,
        step_idx: int,
        step_out: ControllerStepOutput,
        now_utc: datetime,
    ) -> CommandAuditRecord:
        """Record or emit a validated ControllerStepOutput and return an audit entry."""


class AuditTrailStoreProtocol(Protocol):
    """Protocol for persisting supervisory command audit records."""

    def append(self, record: CommandAuditRecord) -> None:
        """Append a single CommandAuditRecord."""

    def list_recent(self, limit: int = 50) -> list[CommandAuditRecord]:
        """Return up to limit most recent audit records in reverse chronological order."""


class InMemoryAuditStore:
    """Thread-unsafe in-memory audit store for testing and lightweight edge runs."""

    def __init__(self, max_records: int = 1000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be >= 1")
        self._max_records = max_records
        self._records: list[CommandAuditRecord] = []

    def append(self, record: CommandAuditRecord) -> None:
        """Append a record while honoring max_records capacity."""
        self._records.append(record)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records :]

    def list_recent(self, limit: int = 50) -> list[CommandAuditRecord]:
        """Return up to limit most recent audit records."""
        if limit <= 0:
            return []
        return list(reversed(self._records[-limit:]))
