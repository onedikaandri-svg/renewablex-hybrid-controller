"""Unit tests for Phase 5 device interfaces, simulated Modbus adapter, and audit runtime."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from renewablex_controller.adapters import (
    DryRunCommandSink,
    SimulatedModbusAdapter,
    SimulatedModbusDevice,
)
from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    HybridController,
    OperatingMode,
    PlantOperationalConfig,
    verify_step_output,
)
from renewablex_controller.devices import DataQuality, EdgeSupervisoryRuntime, InMemoryAuditStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _sample_step_input() -> ControllerStepInput:
    return ControllerStepInput(
        step_idx=0,
        dt_seconds=60.0,
        hour_of_day=12,
        month=6,
        local_load_kw=800.0,
        parasitic_load_kw=40.0,
        pv_available_kw=450.0,
        bess_soc_fraction=0.55,
        bess_max_charge_kw=1000.0,
        bess_max_discharge_kw=1000.0,
        biomass_status=BiomassStatus.RUNNING,
        biomass_available_kw=2000.0,
        biomass_current_power_kw=1400.0,
        biomass_min_load_kw=600.0,
        biomass_can_start=False,
        biomass_can_stop=True,
        allow_grid_import=True,
        allow_grid_export=True,
        grid_export_target_kw=1200.0,
    )


def test_simulated_modbus_read_only_cycle_and_register_scaling() -> None:
    cfg = _load_cfg()
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    dev = SimulatedModbusDevice.from_step_input(_sample_step_input(), now)
    adapter = SimulatedModbusAdapter(
        dev,
        max_telemetry_age_seconds=cfg.control_policy.max_telemetry_age_seconds,
    )
    sink = DryRunCommandSink()
    audit_store = InMemoryAuditStore()
    runtime = EdgeSupervisoryRuntime(cfg, HybridController(cfg), adapter, sink, audit_store)

    assert adapter.read_only is True
    assert sink.write_enabled is False

    snap, out, audit = runtime.run_cycle(now, step_idx=0, dt_seconds=60.0)
    assert snap.data_quality == DataQuality.GOOD
    assert snap.bess_soc_fraction == pytest.approx(0.55)
    assert out.constraint_status == ConstraintStatus.OK
    assert audit.execution_status == "dry_run_only"
    assert audit.write_enabled is False
    assert len(audit_store.list_recent()) == 1
    assert verify_step_output(snap.to_step_input(step_idx=0, dt_seconds=60.0), out).is_balanced


def test_simulated_modbus_comm_loss_and_stale_telemetry_failsafe() -> None:
    cfg = _load_cfg()
    t0 = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    dev = SimulatedModbusDevice.from_step_input(_sample_step_input(), t0)
    adapter = SimulatedModbusAdapter(
        dev,
        max_telemetry_age_seconds=cfg.control_policy.max_telemetry_age_seconds,
    )
    runtime = EdgeSupervisoryRuntime(
        cfg,
        HybridController(cfg),
        adapter,
        DryRunCommandSink(),
        InMemoryAuditStore(),
    )

    # Stale telemetry after timeout
    stale_t = t0 + timedelta(seconds=60.0)
    snap_stale, out_stale, audit_stale = runtime.run_cycle(stale_t, step_idx=0, dt_seconds=60.0)
    assert snap_stale.data_quality == DataQuality.STALE
    assert out_stale.operating_mode == OperatingMode.EMERGENCY_FAULT
    assert out_stale.constraint_status == ConstraintStatus.REJECTED
    assert audit_stale.execution_status == "blocked_unsafe"

    # Explicit disconnect triggers COMM_LOST
    dev.comm_online = False
    snap_lost, out_lost, _ = runtime.run_cycle(t0, step_idx=1, dt_seconds=60.0)
    assert snap_lost.data_quality == DataQuality.COMM_LOST
    assert out_lost.operating_mode == OperatingMode.EMERGENCY_FAULT
    assert out_lost.constraint_status == ConstraintStatus.REJECTED
