"""Unit tests for core.power_balance."""

import pytest

from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    ControllerStepOutput,
    OperatingMode,
    check_power_balance,
    verify_step_output,
)


def test_check_power_balance_valid_step() -> None:
    report = check_power_balance(
        local_load_kw=800.0,
        parasitic_load_kw=50.0,
        pv_available_kw=600.0,
        pv_command_kw=550.0,
        pv_curtailment_kw=50.0,
        biomass_command_kw=1500.0,
        bess_charge_kw=200.0,
        bess_discharge_kw=0.0,
        grid_import_kw=0.0,
        grid_export_kw=1000.0,
        served_load_kw=800.0,
        unmet_load_kw=0.0,
    )
    assert report.is_balanced is True
    assert report.gross_residual_kw == pytest.approx(0.0)
    assert report.net_residual_kw == pytest.approx(0.0)
    assert report.violations == []


def test_check_power_balance_detects_all_violation_types() -> None:
    report = check_power_balance(
        local_load_kw=800.0,
        parasitic_load_kw=50.0,
        pv_available_kw=600.0,
        pv_command_kw=500.0,
        pv_curtailment_kw=50.0,  # PV mismatch
        biomass_command_kw=1000.0,
        bess_charge_kw=100.0,
        bess_discharge_kw=20.0,  # BESS mutual exclusion
        grid_import_kw=30.0,
        grid_export_kw=50.0,  # Grid mutual exclusion
        served_load_kw=700.0,
        unmet_load_kw=50.0,  # Demand mismatch
    )
    assert report.is_balanced is False
    assert set(report.violations) == {
        "POWER_BALANCE_RESIDUAL_EXCEEDED",
        "PV_ACCOUNTING_MISMATCH",
        "DEMAND_ACCOUNTING_MISMATCH",
        "BESS_SIMULTANEOUS_CHARGE_DISCHARGE",
        "GRID_SIMULTANEOUS_IMPORT_EXPORT",
    }


def test_verify_step_output() -> None:
    step_in = ControllerStepInput(
        step_idx=1,
        dt_seconds=3600.0,
        hour_of_day=14,
        month=8,
        local_load_kw=500.0,
        parasitic_load_kw=40.0,
        pv_available_kw=300.0,
        bess_soc_fraction=0.6,
        bess_max_charge_kw=500.0,
        bess_max_discharge_kw=500.0,
        biomass_status=BiomassStatus.OFF,
        biomass_available_kw=0.0,
        biomass_current_power_kw=0.0,
        biomass_min_load_kw=0.0,
        biomass_can_start=True,
        biomass_can_stop=False,
        allow_grid_import=True,
        allow_grid_export=True,
    )
    step_out = ControllerStepOutput(
        operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
        pv_command_kw=300.0,
        pv_curtailment_kw=0.0,
        bess_charge_kw=0.0,
        bess_discharge_kw=0.0,
        biomass_command_kw=0.0,
        biomass_start_request=False,
        biomass_stop_request=False,
        grid_import_kw=240.0,
        grid_export_kw=0.0,
        served_load_kw=500.0,
        unmet_load_kw=0.0,
        constraint_status=ConstraintStatus.OK,
        violations=[],
        active_rule_ids=[],
        trace_reason="ok",
        balance_residual_kw=0.0,
    )
    report = verify_step_output(step_in, step_out)
    assert report.is_balanced is True
