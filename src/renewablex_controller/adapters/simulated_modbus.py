"""Simulated read-only Modbus TCP device, adapter, and dry-run command sink."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput, ControllerStepOutput
from renewablex_controller.core.units import BiomassStatus, ConstraintStatus
from renewablex_controller.devices.models import (
    CommandAuditRecord,
    DataQuality,
    PlantTelemetrySnapshot,
)

_STATUS_TO_CODE: dict[BiomassStatus, int] = {
    BiomassStatus.OFF: 0,
    BiomassStatus.STARTING: 1,
    BiomassStatus.RUNNING: 2,
    BiomassStatus.STOPPING: 3,
    BiomassStatus.OUTAGE: 4,
}
_CODE_TO_STATUS: dict[int, BiomassStatus] = {v: k for k, v in _STATUS_TO_CODE.items()}


class ModbusRegisterSpec(BaseModel):
    """Mapping metadata for one simulated Modbus holding register."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    address: int = Field(ge=0)
    name: str = Field(min_length=1)
    scale: float = Field(default=1.0, gt=0.0)
    signed: bool = False


def default_register_map() -> dict[str, ModbusRegisterSpec]:
    """Return the default holding register map for a generic hybrid plant."""
    specs = [
        ModbusRegisterSpec(address=40001, name="local_load_kw", scale=1.0),
        ModbusRegisterSpec(address=40002, name="parasitic_load_kw", scale=1.0),
        ModbusRegisterSpec(address=40003, name="pv_available_kw", scale=1.0),
        ModbusRegisterSpec(address=40004, name="bess_soc_fraction", scale=0.0001),
        ModbusRegisterSpec(address=40005, name="bess_max_charge_kw", scale=1.0),
        ModbusRegisterSpec(address=40006, name="bess_max_discharge_kw", scale=1.0),
        ModbusRegisterSpec(address=40007, name="biomass_status_code", scale=1.0),
        ModbusRegisterSpec(address=40008, name="biomass_available_kw", scale=1.0),
        ModbusRegisterSpec(address=40009, name="biomass_current_power_kw", scale=1.0),
        ModbusRegisterSpec(address=40010, name="biomass_min_load_kw", scale=1.0),
        ModbusRegisterSpec(address=40011, name="biomass_can_start", scale=1.0),
        ModbusRegisterSpec(address=40012, name="biomass_can_stop", scale=1.0),
        ModbusRegisterSpec(address=40013, name="grid_connected", scale=1.0),
        ModbusRegisterSpec(address=40014, name="allow_grid_import", scale=1.0),
        ModbusRegisterSpec(address=40015, name="allow_grid_export", scale=1.0),
        ModbusRegisterSpec(address=40016, name="grid_export_target_kw", scale=1.0),
        ModbusRegisterSpec(address=40017, name="grid_export_limit_kw", scale=1.0),
        ModbusRegisterSpec(address=40018, name="biomass_maintenance_mode", scale=1.0),
        ModbusRegisterSpec(address=40019, name="sync_permissive", scale=1.0),
        ModbusRegisterSpec(address=40020, name="emergency_stop", scale=1.0),
        ModbusRegisterSpec(address=40021, name="operator_ack_restoration", scale=1.0),
        ModbusRegisterSpec(address=40022, name="operator_ack_fault_reset", scale=1.0),
        ModbusRegisterSpec(address=40023, name="has_export_target", scale=1.0),
        ModbusRegisterSpec(address=40024, name="has_export_limit", scale=1.0),
        ModbusRegisterSpec(address=40025, name="hour_of_day", scale=1.0),
        ModbusRegisterSpec(address=40026, name="month", scale=1.0),
    ]
    return {spec.name: spec for spec in specs}


class SimulatedModbusDevice:
    """In-memory simulated Modbus TCP holding-register device."""

    def __init__(
        self,
        now_utc: datetime,
        *,
        register_map: dict[str, ModbusRegisterSpec] | None = None,
    ) -> None:
        self._register_map = (
            register_map if register_map is not None else default_register_map()
        )
        self._registers: dict[int, int] = {
            spec.address: 0 for spec in self._register_map.values()
        }
        self._last_updated_utc = now_utc
        self.comm_online: bool = True

    @property
    def last_updated_utc(self) -> datetime:
        """Timestamp of the last simulated device update."""
        return self._last_updated_utc

    @property
    def register_map(self) -> dict[str, ModbusRegisterSpec]:
        """Configured register map."""
        return self._register_map

    def set_register(self, name: str, raw_value: int, now_utc: datetime) -> None:
        """Write an integer register value in the simulated device."""
        spec = self._register_map[name]
        self._registers[spec.address] = int(raw_value)
        self._last_updated_utc = now_utc

    def set_engineering_value(self, name: str, value: float, now_utc: datetime) -> None:
        """Encode an engineering float using the register scale factor."""
        spec = self._register_map[name]
        raw_int = round(value / spec.scale)
        self._registers[spec.address] = raw_int
        self._last_updated_utc = now_utc

    def read_holding_registers(self, addresses: list[int]) -> dict[int, int]:
        """Read simulated holding registers; raises ConnectionError when offline."""
        if not self.comm_online:
            raise ConnectionError("Simulated Modbus TCP link offline")
        return {addr: self._registers.get(addr, 0) for addr in addresses}

    @classmethod
    def from_step_input(
        cls,
        step_in: ControllerStepInput,
        now_utc: datetime,
    ) -> SimulatedModbusDevice:
        """Populate a simulated Modbus device from a ControllerStepInput."""
        dev = cls(now_utc)
        dev.set_engineering_value("local_load_kw", step_in.local_load_kw, now_utc)
        dev.set_engineering_value("parasitic_load_kw", step_in.parasitic_load_kw, now_utc)
        dev.set_engineering_value("pv_available_kw", step_in.pv_available_kw, now_utc)
        dev.set_engineering_value("bess_soc_fraction", step_in.bess_soc_fraction, now_utc)
        dev.set_engineering_value("bess_max_charge_kw", step_in.bess_max_charge_kw, now_utc)
        dev.set_engineering_value("bess_max_discharge_kw", step_in.bess_max_discharge_kw, now_utc)
        dev.set_register("biomass_status_code", _STATUS_TO_CODE[step_in.biomass_status], now_utc)
        dev.set_engineering_value("biomass_available_kw", step_in.biomass_available_kw, now_utc)
        dev.set_engineering_value(
            "biomass_current_power_kw",
            step_in.biomass_current_power_kw,
            now_utc,
        )
        dev.set_engineering_value("biomass_min_load_kw", step_in.biomass_min_load_kw, now_utc)
        dev.set_register("biomass_can_start", int(step_in.biomass_can_start), now_utc)
        dev.set_register("biomass_can_stop", int(step_in.biomass_can_stop), now_utc)
        dev.set_register("grid_connected", int(step_in.grid_connected), now_utc)
        dev.set_register("allow_grid_import", int(step_in.allow_grid_import), now_utc)
        dev.set_register("allow_grid_export", int(step_in.allow_grid_export), now_utc)
        dev.set_register(
            "has_export_target",
            int(step_in.grid_export_target_kw is not None),
            now_utc,
        )
        dev.set_engineering_value(
            "grid_export_target_kw",
            step_in.grid_export_target_kw or 0.0,
            now_utc,
        )
        dev.set_register("has_export_limit", int(step_in.grid_export_limit_kw is not None), now_utc)
        dev.set_engineering_value(
            "grid_export_limit_kw",
            step_in.grid_export_limit_kw or 0.0,
            now_utc,
        )
        dev.set_register("biomass_maintenance_mode", int(step_in.biomass_maintenance_mode), now_utc)
        dev.set_register("sync_permissive", int(step_in.sync_permissive), now_utc)
        dev.set_register("emergency_stop", int(step_in.emergency_stop), now_utc)
        dev.set_register("operator_ack_restoration", int(step_in.operator_ack_restoration), now_utc)
        dev.set_register("operator_ack_fault_reset", int(step_in.operator_ack_fault_reset), now_utc)
        dev.set_register("hour_of_day", step_in.hour_of_day, now_utc)
        dev.set_register("month", step_in.month, now_utc)
        return dev


class SimulatedModbusAdapter:
    """Read-only telemetry adapter polling a SimulatedModbusDevice."""

    def __init__(
        self,
        device: SimulatedModbusDevice,
        *,
        max_telemetry_age_seconds: float = 30.0,
        adapter_name: str = "simulated_modbus_tcp",
    ) -> None:
        self._device = device
        self._max_telemetry_age_seconds = max_telemetry_age_seconds
        self._adapter_name = adapter_name

    @property
    def adapter_name(self) -> str:
        """Adapter identifier."""
        return self._adapter_name

    @property
    def read_only(self) -> bool:
        """Strictly read-only in Phase 5."""
        return True

    def read_snapshot(self, now_utc: datetime) -> PlantTelemetrySnapshot:
        """Poll simulated registers and tag data quality and measurement age."""
        age_seconds = max(0.0, (now_utc - self._device.last_updated_utc).total_seconds())
        addresses = [spec.address for spec in self._device.register_map.values()]

        try:
            raw_map = self._device.read_holding_registers(addresses)
        except ConnectionError:
            return PlantTelemetrySnapshot(
                timestamp_utc=now_utc,
                measurement_age_seconds=age_seconds,
                data_quality=DataQuality.COMM_LOST,
                comm_healthy=False,
                source_adapter=self._adapter_name,
                local_load_kw=0.0,
                pv_available_kw=0.0,
                bess_soc_fraction=0.0,
                bess_max_charge_kw=0.0,
                bess_max_discharge_kw=0.0,
                biomass_status=BiomassStatus.OUTAGE,
                biomass_available_kw=0.0,
                biomass_current_power_kw=0.0,
                biomass_min_load_kw=0.0,
                grid_connected=False,
            )

        def val(name: str) -> float:
            spec = self._device.register_map[name]
            return raw_map[spec.address] * spec.scale

        def flag(name: str) -> bool:
            spec = self._device.register_map[name]
            return raw_map[spec.address] != 0

        status_code = int(val("biomass_status_code"))
        bio_status = _CODE_TO_STATUS.get(status_code, BiomassStatus.OUTAGE)
        quality = (
            DataQuality.STALE
            if age_seconds > self._max_telemetry_age_seconds
            else DataQuality.GOOD
        )
        hour = max(0, min(23, int(val("hour_of_day"))))
        month = max(1, min(12, int(val("month"))))

        return PlantTelemetrySnapshot(
            timestamp_utc=now_utc,
            measurement_age_seconds=age_seconds,
            data_quality=quality,
            comm_healthy=(quality == DataQuality.GOOD),
            source_adapter=self._adapter_name,
            hour_of_day=hour,
            month=month,
            local_load_kw=max(0.0, val("local_load_kw")),
            parasitic_load_kw=max(0.0, val("parasitic_load_kw")),
            pv_available_kw=max(0.0, val("pv_available_kw")),
            bess_soc_fraction=min(1.0, max(0.0, val("bess_soc_fraction"))),
            bess_max_charge_kw=max(0.0, val("bess_max_charge_kw")),
            bess_max_discharge_kw=max(0.0, val("bess_max_discharge_kw")),
            biomass_status=bio_status,
            biomass_available_kw=max(0.0, val("biomass_available_kw")),
            biomass_current_power_kw=max(0.0, val("biomass_current_power_kw")),
            biomass_min_load_kw=max(0.0, val("biomass_min_load_kw")),
            biomass_can_start=flag("biomass_can_start"),
            biomass_can_stop=flag("biomass_can_stop"),
            grid_connected=flag("grid_connected"),
            allow_grid_import=flag("allow_grid_import"),
            allow_grid_export=flag("allow_grid_export"),
            grid_export_target_kw=(
                max(0.0, val("grid_export_target_kw")) if flag("has_export_target") else None
            ),
            grid_export_limit_kw=(
                max(0.0, val("grid_export_limit_kw")) if flag("has_export_limit") else None
            ),
            biomass_maintenance_mode=flag("biomass_maintenance_mode"),
            sync_permissive=flag("sync_permissive"),
            emergency_stop=flag("emergency_stop"),
            operator_ack_restoration=flag("operator_ack_restoration"),
            operator_ack_fault_reset=flag("operator_ack_fault_reset"),
        )


class DryRunCommandSink:
    """Audit-only command sink with hardware writes permanently disabled."""

    def __init__(self, sink_name: str = "dry_run_audit_sink") -> None:
        self._sink_name = sink_name

    @property
    def sink_name(self) -> str:
        """Command sink identifier."""
        return self._sink_name

    @property
    def write_enabled(self) -> bool:
        """Always False in simulation/dry-run mode."""
        return False

    def emit(
        self,
        step_idx: int,
        step_out: ControllerStepOutput,
        now_utc: datetime,
    ) -> CommandAuditRecord:
        """Record supervisory setpoints without writing to any hardware register."""
        exec_status: Literal["dry_run_only", "blocked_unsafe", "dispatched_simulated"] = (
            "blocked_unsafe"
            if step_out.constraint_status
            in {ConstraintStatus.REJECTED, ConstraintStatus.INFEASIBLE}
            else "dry_run_only"
        )
        return CommandAuditRecord(
            recorded_at_utc=now_utc,
            step_idx=step_idx,
            operating_mode=step_out.operating_mode,
            constraint_status=step_out.constraint_status,
            execution_status=exec_status,
            write_enabled=self.write_enabled,
            sink_name=self._sink_name,
            pv_command_kw=step_out.pv_command_kw,
            bess_charge_kw=step_out.bess_charge_kw,
            bess_discharge_kw=step_out.bess_discharge_kw,
            biomass_command_kw=step_out.biomass_command_kw,
            grid_import_kw=step_out.grid_import_kw,
            grid_export_kw=step_out.grid_export_kw,
            violations=list(step_out.violations),
            active_rule_ids=list(step_out.active_rule_ids),
            trace_reason=step_out.trace_reason,
        )
