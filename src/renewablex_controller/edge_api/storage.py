"""Phase 7: Local SQLite persistence for command audit logs and active EMS rules."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from renewablex_controller.core.ems_rules import EMSRuleSet
from renewablex_controller.devices.models import CommandAuditRecord


class EdgeSQLiteStore:
    """SQLite-backed audit trail and active JSON EMS rule store."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at_utc TEXT NOT NULL,
                    step_idx INTEGER NOT NULL,
                    operating_mode TEXT NOT NULL,
                    constraint_status TEXT NOT NULL,
                    execution_status TEXT NOT NULL,
                    write_enabled INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_ems_rules (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    updated_at_utc TEXT NOT NULL,
                    rule_set_json TEXT NOT NULL
                )
                """
            )

    def append(self, record: CommandAuditRecord) -> None:
        """Persist a CommandAuditRecord to SQLite."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO command_audit (
                    recorded_at_utc,
                    step_idx,
                    operating_mode,
                    constraint_status,
                    execution_status,
                    write_enabled,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.recorded_at_utc.isoformat(),
                    record.step_idx,
                    record.operating_mode.value,
                    record.constraint_status.value,
                    record.execution_status,
                    int(record.write_enabled),
                    record.model_dump_json(),
                ),
            )

    def list_recent(self, limit: int = 50) -> list[CommandAuditRecord]:
        """Return up to limit most recent CommandAuditRecord rows."""
        if limit <= 0:
            return []
        cursor = self._conn.execute(
            "SELECT payload_json FROM command_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [CommandAuditRecord.model_validate_json(row[0]) for row in cursor.fetchall()]

    def save_rule_set(self, rule_set: EMSRuleSet, updated_at_utc: str) -> None:
        """Persist the active validated JSON EMS rule set."""
        payload = rule_set.model_dump_json()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO active_ems_rules (id, updated_at_utc, rule_set_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at_utc = excluded.updated_at_utc,
                    rule_set_json = excluded.rule_set_json
                """,
                (updated_at_utc, payload),
            )

    def get_active_rule_set(self) -> EMSRuleSet | None:
        """Reload the persisted active EMS rule set if present."""
        cursor = self._conn.execute(
            "SELECT rule_set_json FROM active_ems_rules WHERE id = 1"
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return EMSRuleSet.model_validate_json(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
