"""Unit tests for Module 5 baseline SafetySupervisor."""

from pathlib import Path

import pytest

from renewablex_controller.core import (
    BiomassStatus,
    CommLossAction,
    ConstraintStatus,
    ControllerStepInput,
    OperatingMode,
    PlantOperationalConfig,
    ProposedDispatch,
    SafetySupervisor,
    verify_step_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_ref_config() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(**overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": 0,
        "dt_seconds": 900.0,
        "hour_of_day": 12,
        "month": 5,
        "local_load_kw": 800.0,
        "parasitic_load_kw": 50.0,
        "pv_available_kw": 400.0,
        "bess_soc_fraction": 0.50,
        "bess_max_charge_kw": 1000.0,
        "bess_max_discharge_kw": 1000.0,
        "biomass_status": BiomassStatus.RUNNING,
        "biomass_available_kw": 2000.0,
        "biomass_current_power_kw": 1500.0,
        "biomass_min_load_kw": 600.0,
        "biomass_can_start": False,
        "biomass_can_stop": True,
        "biomass_fuel_remaining_tons": 50.0,
        "allow_grid_import": True,
        "allow_grid_export": True,
        "tariff_per_kwh": 0.1,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_feasible_proposal_passes_unchanged() -> None:
    cfg = _load_ref_config()
    sup = SafetySupervisor(cfg)
    step_in = _step_input()
    proposed = ProposedDispatch(
        operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
        pv_command_kw=400.0,
        biomass_command_kw=1500.0,
        bess_charge_kw=150.0,
        grid_export_kw=900.0,
        served_load_kw=800.0,
    )
    out = sup.supervise(step_in, proposed)
    assert out.constraint_status == ConstraintStatus.OK
    assert out.violations == []
    assert abs(out.balance_residual_kw) < 1e-6
    assert verify_step_output(step_in, out).is_balanced


def test_biomass_ramp_and_maintenance_clamping_with_reconciliation() -> None:
    cfg = _load_ref_config()
    sup = SafetySupervisor(cfg)
    # 900s = 0.25h -> max ramp up = 600 * 0.25 = 150 kW -> max allowed = 1650 kW
    step_in = _step_input(biomass_current_power_kw=1500.0)
    proposed = ProposedDispatch(
        operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
        pv_command_kw=400.0,
        biomass_command_kw=2000.0,
        grid_export_kw=1550.0,
        served_load_kw=800.0,
    )
    out = sup.supervise(step_in, proposed)
    assert out.constraint_status == ConstraintStatus.CLAMPED
    assert "BIOMASS_RAMP_UP_CLAMPED" in out.violations
    assert out.biomass_command_kw == pytest.approx(1650.0)
    assert abs(out.balance_residual_kw) < 1e-6
    assert verify_step_output(step_in, out).is_balanced

    # Maintenance forces biomass to 0 and reconciles via grid import / BESS
    maint_in = _step_input(biomass_maintenance_mode=True)
    maint_out = sup.supervise(maint_in, proposed)
    assert maint_out.constraint_status == ConstraintStatus.CLAMPED
    assert maint_out.biomass_command_kw == pytest.approx(0.0)
    assert "BIOMASS_UNAVAILABLE_OR_MAINTENANCE" in maint_out.violations
    assert maint_out.biomass_stop_request is True
    assert abs(maint_out.balance_residual_kw) < 1e-6


def test_bess_reserve_enforced_in_normal_mode_and_relaxed_in_islanded() -> None:
    cfg = _load_ref_config()
    sup = SafetySupervisor(cfg)

    # At SoC = 0.30 (equal to reserve_soc_fraction), discharge in normal mode must be 0 kW
    normal_in = _step_input(
        bess_soc_fraction=0.30,
        pv_available_kw=0.0,
        biomass_status=BiomassStatus.OFF,
    )
    proposed_normal = ProposedDispatch(
        operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
        pv_command_kw=0.0,
        bess_discharge_kw=500.0,
        served_load_kw=800.0,
    )
    normal_out = sup.supervise(normal_in, proposed_normal)
    assert normal_out.constraint_status == ConstraintStatus.CLAMPED
    assert normal_out.bess_discharge_kw == pytest.approx(0.0)
    assert normal_out.grid_import_kw == pytest.approx(850.0)
    assert "BESS_DISCHARGE_LIMIT_CLAMPED" in normal_out.violations

    # In islanded mode, drawdown down to soc_min (0.15) is permitted
    island_in = _step_input(
        grid_connected=False,
        bess_soc_fraction=0.30,
        pv_available_kw=0.0,
        biomass_status=BiomassStatus.OFF,
    )
    proposed_island = ProposedDispatch(
        operating_mode=OperatingMode.ISLANDED,
        pv_command_kw=0.0,
        bess_discharge_kw=850.0,
        served_load_kw=800.0,
    )
    island_out = sup.supervise(island_in, proposed_island)
    assert island_out.constraint_status == ConstraintStatus.OK
    assert island_out.bess_discharge_kw == pytest.approx(850.0)
    assert island_out.unmet_load_kw == pytest.approx(0.0)


def test_emergency_stop_and_stale_telemetry_reject_and_balance() -> None:
    cfg = _load_ref_config()
    sup = SafetySupervisor(cfg)

    estop_in = _step_input(emergency_stop=True)
    out = sup.supervise(
        estop_in,
        ProposedDispatch(
            operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
            pv_command_kw=400.0,
            biomass_command_kw=1500.0,
            served_load_kw=800.0,
        ),
    )
    assert out.constraint_status == ConstraintStatus.REJECTED
    assert out.operating_mode == OperatingMode.EMERGENCY_FAULT
    assert "EMERGENCY_STOP_ACTIVE" in out.violations
    assert out.pv_command_kw == 0.0
    assert out.biomass_command_kw == 0.0
    assert out.served_load_kw == 0.0
    assert out.unmet_load_kw == 800.0
    assert out.grid_import_kw == 0.0
    assert out.balance_residual_kw == pytest.approx(-estop_in.parasitic_load_kw)
    assert out.bess_charge_kw * out.bess_discharge_kw == 0.0
    assert out.grid_import_kw * out.grid_export_kw == 0.0

    # Stale telemetry with HOLD_SAFE policy keeps mode but disables export
    hold_cfg = cfg.model_copy(
        update={
            "control_policy": cfg.control_policy.model_copy(
                update={"comm_loss_action": CommLossAction.HOLD_SAFE}
            )
        }
    )
    hold_sup = SafetySupervisor(hold_cfg)
    stale_in = _step_input(telemetry_age_seconds=120.0)
    hold_out = hold_sup.supervise(
        stale_in,
        ProposedDispatch(
            operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
            pv_command_kw=400.0,
            biomass_command_kw=1500.0,
            grid_export_kw=1050.0,
            served_load_kw=800.0,
        ),
    )
    assert hold_out.constraint_status == ConstraintStatus.CLAMPED
    assert "STALE_TELEMETRY" in hold_out.violations
    assert hold_out.grid_export_kw == 0.0
    assert verify_step_output(stale_in, hold_out).is_balanced


def test_station_aux_deficit_flags_infeasible() -> None:
    cfg = _load_ref_config()
    sup = SafetySupervisor(cfg)
    blackout_in = _step_input(
        grid_connected=False,
        local_load_kw=200.0,
        parasitic_load_kw=50.0,
        pv_available_kw=10.0,
        bess_soc_fraction=0.15,
        biomass_status=BiomassStatus.OFF,
        biomass_available_kw=0.0,
        biomass_current_power_kw=0.0,
    )
    out = sup.supervise(
        blackout_in,
        ProposedDispatch(
            operating_mode=OperatingMode.ISLANDED,
            pv_command_kw=10.0,
            served_load_kw=200.0,
        ),
    )
    assert out.constraint_status == ConstraintStatus.INFEASIBLE
    assert "STATION_AUX_DEFICIT" in out.violations
    assert out.served_load_kw == 0.0
    assert out.unmet_load_kw == 200.0
