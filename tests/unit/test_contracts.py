"""Unit tests for core.contracts."""

import pytest
from pydantic import ValidationError

from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
    OperatingMode,
)


def _sample_step_input(**overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": 0,
        "dt_seconds": 900.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 800.0,
        "parasitic_load_kw": 50.0,
        "pv_available_kw": 500.0,
        "bess_soc_fraction": 0.55,
        "bess_max_charge_kw": 1000.0,
        "bess_max_discharge_kw": 1000.0,
        "biomass_status": BiomassStatus.RUNNING,
        "biomass_available_kw": 2000.0,
        "biomass_current_power_kw": 1500.0,
        "biomass_min_load_kw": 600.0,
        "biomass_can_start": False,
        "biomass_can_stop": True,
        "biomass_fuel_remaining_tons": 120.0,
        "allow_grid_import": True,
        "allow_grid_export": True,
        "tariff_per_kwh": 0.12,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_step_input_derived_properties_and_json_roundtrip() -> None:
    step_in = _sample_step_input(local_load_kw=400.0, parasitic_load_kw=50.0, pv_available_kw=600.0)
    assert step_in.step_hours == pytest.approx(0.25)
    assert step_in.total_demand_kw == pytest.approx(450.0)
    assert step_in.net_load_kw == pytest.approx(0.0)
    assert step_in.pv_surplus_kw == pytest.approx(150.0)

    roundtrip = ControllerStepInput.model_validate_json(step_in.model_dump_json())
    assert roundtrip == step_in


def test_step_input_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        _sample_step_input(bess_soc_fraction=1.2)
    with pytest.raises(ValidationError):
        _sample_step_input(local_load_kw=-1.0)
    with pytest.raises(ValidationError):
        _sample_step_input(biomass_status="invalid_status")
    with pytest.raises(ValidationError):
        _sample_step_input(dt_seconds=0.0)


def test_step_output_mutual_exclusion_and_pcc_export() -> None:
    out = ControllerStepOutput(
        operating_mode=OperatingMode.GRID_CONNECTED_NORMAL,
        pv_command_kw=500.0,
        pv_curtailment_kw=0.0,
        bess_charge_kw=100.0,
        bess_discharge_kw=0.0,
        biomass_command_kw=1500.0,
        biomass_start_request=False,
        biomass_stop_request=False,
        grid_import_kw=0.0,
        grid_export_kw=1050.0,
        served_load_kw=800.0,
        unmet_load_kw=0.0,
        constraint_status=ConstraintStatus.OK,
        violations=[],
        active_rule_ids=[],
        trace_reason="ok",
        balance_residual_kw=0.0,
    )
    assert out.p_pcc_export_kw == pytest.approx(1050.0)

    with pytest.raises(ValidationError):
        out.model_copy(update={"bess_discharge_kw": 50.0})
        ControllerStepOutput(**{**out.model_dump(), "bess_discharge_kw": 50.0})

    with pytest.raises(ValidationError):
        ControllerStepOutput(**{**out.model_dump(), "grid_import_kw": 20.0})


def test_controller_state_roundtrip() -> None:
    state = ControllerState()
    assert state.operating_mode == OperatingMode.GRID_CONNECTED_NORMAL
    restored = ControllerState.model_validate_json(state.model_dump_json())
    assert restored == state
