"""Phase 6: Hardware-in-the-Loop (HIL) readiness validation suite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.adapters.simulated_modbus import (
    DryRunCommandSink,
    SimulatedModbusAdapter,
    SimulatedModbusDevice,
)
from renewablex_controller.core.contracts import ControllerStepInput
from renewablex_controller.core.controller import HybridController
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.power_balance import verify_step_output
from renewablex_controller.core.units import BiomassStatus, ConstraintStatus, OperatingMode
from renewablex_controller.devices.interfaces import InMemoryAuditStore
from renewablex_controller.devices.models import DataQuality
from renewablex_controller.devices.runtime import EdgeSupervisoryRuntime


class HILTestResult(BaseModel):
    """Result for one automated HIL verification check."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: str
    description: str
    passed: bool
    details: str


class HILSuiteReport(BaseModel):
    """Aggregated HIL readiness report."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    total_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    all_passed: bool
    results: list[HILTestResult]


class HILValidationSuite:
    """Runs deterministic HIL-readiness checks against simulated adapters and runtime."""

    def __init__(self, config: PlantOperationalConfig) -> None:
        self._config = config

    def run_all(self) -> HILSuiteReport:
        """Execute all HIL safety, timing, and fail-safe validation cases."""
        results = [
            self._check_sub_minute_ramp_compliance(),
            self._check_modbus_comm_loss_failsafe(),
            self._check_stale_telemetry_rejection(),
            self._check_emergency_stop_override(),
            self._check_dry_run_write_lock(),
        ]
        passed_count = sum(1 for item in results if item.passed)
        return HILSuiteReport(
            total_cases=len(results),
            passed_cases=passed_count,
            all_passed=(passed_count == len(results)),
            results=results,
        )

    def _base_step_input(self, *, dt_seconds: float = 10.0) -> ControllerStepInput:
        return ControllerStepInput(
            step_idx=0,
            dt_seconds=dt_seconds,
            hour_of_day=12,
            month=6,
            local_load_kw=750.0,
            parasitic_load_kw=40.0,
            pv_available_kw=400.0,
            bess_soc_fraction=0.55,
            bess_max_charge_kw=self._config.bess.max_charge_kw,
            bess_max_discharge_kw=self._config.bess.max_discharge_kw,
            biomass_status=BiomassStatus.RUNNING,
            biomass_available_kw=self._config.biomass.operating_max_kw,
            biomass_current_power_kw=1200.0,
            biomass_min_load_kw=self._config.biomass.min_load_kw,
            biomass_can_start=False,
            biomass_can_stop=True,
            allow_grid_import=True,
            allow_grid_export=True,
            grid_export_target_kw=1500.0,
        )

    def _check_sub_minute_ramp_compliance(self) -> HILTestResult:
        dt_sec = 10.0
        ctrl = HybridController(self._config)
        max_up = self._config.biomass.max_ramp_up_kw(dt_sec)
        max_down = self._config.biomass.max_ramp_down_kw(dt_sec)

        bio_power = 1200.0
        passed = True
        details = "ok"
        for idx in range(6):
            target = 2000.0 if idx < 3 else 500.0
            step_in = self._base_step_input(dt_seconds=dt_sec).model_copy(
                update={
                    "step_idx": idx,
                    "biomass_current_power_kw": bio_power,
                    "grid_export_target_kw": target,
                }
            )
            out = ctrl.step(step_in)
            delta = out.biomass_command_kw - bio_power
            if delta > max_up + 1e-6 or (-delta) > max_down + 1e-6:
                passed = False
                details = f"step {idx}: ramp delta {delta:.4f} exceeded bounds"
                break
            if not verify_step_output(step_in, out).is_balanced:
                passed = False
                details = f"step {idx}: power balance failed"
                break
            bio_power = out.biomass_command_kw

        return HILTestResult(
            name="sub_minute_ramp_compliance",
            description="Verify 10-second ramp limits and closed balance over rapid target steps.",
            passed=passed,
            details=details,
        )

    def _check_modbus_comm_loss_failsafe(self) -> HILTestResult:
        now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
        dev = SimulatedModbusDevice.from_step_input(self._base_step_input(), now)
        dev.comm_online = False
        adapter = SimulatedModbusAdapter(
            dev,
            max_telemetry_age_seconds=self._config.control_policy.max_telemetry_age_seconds,
        )
        runtime = EdgeSupervisoryRuntime(
            self._config,
            HybridController(self._config),
            adapter,
            DryRunCommandSink(),
            InMemoryAuditStore(),
        )
        snap, out, audit = runtime.run_cycle(now, step_idx=0, dt_seconds=10.0)
        passed = (
            snap.data_quality == DataQuality.COMM_LOST
            and out.operating_mode == OperatingMode.EMERGENCY_FAULT
            and out.constraint_status == ConstraintStatus.REJECTED
            and audit.execution_status == "blocked_unsafe"
            and audit.write_enabled is False
        )
        return HILTestResult(
            name="modbus_comm_loss_failsafe",
            description=(
                "Verify simulated Modbus disconnect triggers EMERGENCY_FAULT and dry-run block."
            ),
            passed=passed,
            details=f"quality={snap.data_quality.value}, status={out.constraint_status.value}",
        )

    def _check_stale_telemetry_rejection(self) -> HILTestResult:
        t0 = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
        dev = SimulatedModbusDevice.from_step_input(self._base_step_input(), t0)
        adapter = SimulatedModbusAdapter(
            dev,
            max_telemetry_age_seconds=self._config.control_policy.max_telemetry_age_seconds,
        )
        runtime = EdgeSupervisoryRuntime(
            self._config,
            HybridController(self._config),
            adapter,
            DryRunCommandSink(),
            InMemoryAuditStore(),
        )
        stale_time = t0 + timedelta(
            seconds=self._config.control_policy.max_telemetry_age_seconds + 15.0
        )
        snap, out, audit = runtime.run_cycle(stale_time, step_idx=0, dt_seconds=10.0)
        passed = (
            snap.data_quality == DataQuality.STALE
            and out.operating_mode == OperatingMode.EMERGENCY_FAULT
            and out.constraint_status == ConstraintStatus.REJECTED
            and audit.execution_status == "blocked_unsafe"
        )
        return HILTestResult(
            name="stale_telemetry_rejection",
            description="Verify stale telemetry past timeout triggers fail-safe rejection.",
            passed=passed,
            details=(
                f"age={snap.measurement_age_seconds:.1f}s, "
                f"status={out.constraint_status.value}"
            ),
        )

    def _check_emergency_stop_override(self) -> HILTestResult:
        now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
        dev = SimulatedModbusDevice.from_step_input(self._base_step_input(), now)
        dev.set_register("emergency_stop", 1, now)
        adapter = SimulatedModbusAdapter(dev)
        runtime = EdgeSupervisoryRuntime(
            self._config,
            HybridController(self._config),
            adapter,
            DryRunCommandSink(),
            InMemoryAuditStore(),
        )
        _, out, _ = runtime.run_cycle(now, step_idx=0, dt_seconds=10.0)
        passed = (
            out.operating_mode == OperatingMode.EMERGENCY_FAULT
            and out.constraint_status == ConstraintStatus.REJECTED
            and out.pv_command_kw == 0.0
            and out.biomass_command_kw == 0.0
            and out.grid_export_kw == 0.0
        )
        return HILTestResult(
            name="emergency_stop_override",
            description="Verify emergency stop register zeroes all active generation and export.",
            passed=passed,
            details=f"mode={out.operating_mode.value}, violations={out.violations}",
        )

    def _check_dry_run_write_lock(self) -> HILTestResult:
        now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
        dev = SimulatedModbusDevice.from_step_input(self._base_step_input(), now)
        adapter = SimulatedModbusAdapter(dev)
        sink = DryRunCommandSink()
        runtime = EdgeSupervisoryRuntime(
            self._config,
            HybridController(self._config),
            adapter,
            sink,
            InMemoryAuditStore(),
        )
        _, out, audit = runtime.run_cycle(now, step_idx=0, dt_seconds=10.0)
        passed = (
            adapter.read_only is True
            and sink.write_enabled is False
            and self._config.control_policy.hardware_commands_enabled is False
            and audit.write_enabled is False
            and audit.execution_status == "dry_run_only"
            and out.constraint_status in {ConstraintStatus.OK, ConstraintStatus.CLAMPED}
        )
        return HILTestResult(
            name="dry_run_write_lock",
            description="Verify read-only telemetry and disabled hardware writes on normal cycles.",
            passed=passed,
            details=f"write_enabled={audit.write_enabled}, exec={audit.execution_status}",
        )
