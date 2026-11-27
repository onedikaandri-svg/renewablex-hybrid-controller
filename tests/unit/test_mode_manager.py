"""Unit tests for Module 2 OperatingModeManager FSM."""

from pathlib import Path

from renewablex_controller.core import (
    BiomassStatus,
    ControllerState,
    ControllerStepInput,
    OperatingMode,
    OperatingModeManager,
    PlantOperationalConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(step_idx: int = 0, **overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": step_idx,
        "dt_seconds": 20.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 700.0,
        "parasitic_load_kw": 40.0,
        "pv_available_kw": 300.0,
        "bess_soc_fraction": 0.55,
        "bess_max_charge_kw": 1000.0,
        "bess_max_discharge_kw": 1000.0,
        "biomass_status": BiomassStatus.RUNNING,
        "biomass_available_kw": 2000.0,
        "biomass_current_power_kw": 1000.0,
        "biomass_min_load_kw": 600.0,
        "biomass_can_start": False,
        "biomass_can_stop": True,
        "allow_grid_import": True,
        "allow_grid_export": True,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_momentary_outage_recovers_below_threshold() -> None:
    cfg = _load_cfg()
    fsm = OperatingModeManager(cfg)
    state = ControllerState()

    # Step 1: 20s outage (< 60s threshold) -> MOMENTARY_OUTAGE
    r1 = fsm.evaluate(_step_input(0, grid_connected=False), state)
    assert r1.operating_mode == OperatingMode.MOMENTARY_OUTAGE
    state = ControllerState(
        operating_mode=r1.operating_mode,
        previous_grid_mode=r1.previous_grid_mode,
        outage_elapsed_seconds=r1.outage_elapsed_seconds,
    )

    # Step 2: Grid returns at 20s -> returns to GRID_CONNECTED_NORMAL
    r2 = fsm.evaluate(_step_input(1, grid_connected=True), state)
    assert r2.operating_mode == OperatingMode.GRID_CONNECTED_NORMAL
    assert r2.outage_elapsed_seconds == 0.0


def test_prolonged_outage_to_islanded_and_sync_restoration() -> None:
    cfg = _load_cfg()
    fsm = OperatingModeManager(cfg)
    state = ControllerState()

    # Large step (900s >= 60s) outage -> PROLONGED_OUTAGE
    r1 = fsm.evaluate(_step_input(0, dt_seconds=900.0, grid_connected=False), state)
    assert r1.operating_mode == OperatingMode.PROLONGED_OUTAGE
    state = ControllerState(
        operating_mode=r1.operating_mode,
        previous_grid_mode=r1.previous_grid_mode,
        outage_elapsed_seconds=r1.outage_elapsed_seconds,
        active_sequence=r1.active_sequence,
        sequence_step=r1.sequence_step,
        sequence_elapsed_seconds=r1.sequence_elapsed_seconds,
    )

    # Next step completes islanding prep -> ISLANDED
    r2 = fsm.evaluate(_step_input(1, dt_seconds=900.0, grid_connected=False), state)
    assert r2.operating_mode == OperatingMode.ISLANDED
    state = ControllerState(
        operating_mode=r2.operating_mode,
        previous_grid_mode=r2.previous_grid_mode,
        outage_elapsed_seconds=r2.outage_elapsed_seconds,
    )

    # Grid returns without sync_permissive -> GRID_RESTORATION
    r3 = fsm.evaluate(_step_input(2, dt_seconds=900.0, grid_connected=True), state)
    assert r3.operating_mode == OperatingMode.GRID_RESTORATION
    state = ControllerState(
        operating_mode=r3.operating_mode,
        previous_grid_mode=r3.previous_grid_mode,
        active_sequence=r3.active_sequence,
        sequence_step=r3.sequence_step,
        sequence_elapsed_seconds=r3.sequence_elapsed_seconds,
    )

    # Still blocked without sync_permissive & ack
    r4 = fsm.evaluate(_step_input(3, dt_seconds=900.0, grid_connected=True), state)
    assert r4.operating_mode == OperatingMode.GRID_RESTORATION
    state = ControllerState(
        operating_mode=r4.operating_mode,
        previous_grid_mode=r4.previous_grid_mode,
        active_sequence=r4.active_sequence,
        sequence_step=r4.sequence_step,
        sequence_elapsed_seconds=r4.sequence_elapsed_seconds,
    )

    # Sync permissive + operator ack + export limit -> PEAK_CUT
    r5 = fsm.evaluate(
        _step_input(
            4,
            dt_seconds=900.0,
            grid_connected=True,
            sync_permissive=True,
            operator_ack_restoration=True,
            grid_export_limit_kw=1800.0,
        ),
        state,
    )
    assert r5.operating_mode == OperatingMode.PEAK_CUT


def test_invalid_transition_request_rejected_and_fault_reset() -> None:
    cfg = _load_cfg()
    fsm = OperatingModeManager(cfg)
    islanded_state = ControllerState(operating_mode=OperatingMode.ISLANDED)

    # Bypassing GRID_RESTORATION directly to GRID_CONNECTED_NORMAL must be rejected
    res = fsm.evaluate(
        _step_input(0, grid_connected=False),
        islanded_state,
        requested_mode=OperatingMode.GRID_CONNECTED_NORMAL,
    )
    assert res.operating_mode == OperatingMode.ISLANDED
    assert "INVALID_MODE_TRANSITION:islanded->grid_connected_normal" in res.violations
    assert any(ev.accepted is False for ev in res.events)

    # Emergency fault requires ack before transitioning to GRID_RESTORATION
    fault_res = fsm.evaluate(_step_input(1, emergency_stop=True), islanded_state)
    assert fault_res.operating_mode == OperatingMode.EMERGENCY_FAULT
    fault_state = ControllerState(operating_mode=fault_res.operating_mode)

    blocked_reset = fsm.evaluate(
        _step_input(2, emergency_stop=False, operator_ack_fault_reset=False),
        fault_state,
    )
    assert blocked_reset.operating_mode == OperatingMode.EMERGENCY_FAULT

    ok_reset = fsm.evaluate(
        _step_input(3, emergency_stop=False, operator_ack_fault_reset=True, grid_connected=True),
        fault_state,
    )
    assert ok_reset.operating_mode == OperatingMode.GRID_RESTORATION
