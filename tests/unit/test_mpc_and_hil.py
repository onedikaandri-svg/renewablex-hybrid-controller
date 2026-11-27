"""Unit tests for Phase 6 LookaheadMPCDispatcher and HILValidationSuite."""

from pathlib import Path

import pytest

from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    ForecastPoint,
    LookaheadMPCDispatcher,
    OperatingMode,
    PlantOperationalConfig,
    verify_step_output,
)
from renewablex_controller.simulation import HILValidationSuite

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(**overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": 0,
        "dt_seconds": 3600.0,
        "hour_of_day": 2,
        "month": 6,
        "local_load_kw": 800.0,
        "parasitic_load_kw": 40.0,
        "pv_available_kw": 0.0,
        "bess_soc_fraction": 0.45,
        "bess_max_charge_kw": 1000.0,
        "bess_max_discharge_kw": 1000.0,
        "biomass_status": BiomassStatus.OFF,
        "biomass_available_kw": 0.0,
        "biomass_current_power_kw": 0.0,
        "biomass_min_load_kw": 600.0,
        "biomass_can_start": True,
        "biomass_can_stop": False,
        "allow_grid_import": True,
        "allow_grid_export": True,
        "tariff_per_kwh": 0.08,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_mpc_lookahead_tariff_arbitrage_and_reserve_safety() -> None:
    cfg = _load_cfg()
    mpc = LookaheadMPCDispatcher(cfg, horizon_steps=4)

    # Low current tariff (0.08) followed by high peak tariff (0.25) -> charges BESS now
    low_tariff_in = _step_input(tariff_per_kwh=0.08, bess_soc_fraction=0.45)
    high_future = [
        ForecastPoint(
            dt_seconds=3600.0,
            local_load_kw=1200.0,
            pv_available_kw=0.0,
            tariff_per_kwh=0.25,
        )
        for _ in range(4)
    ]
    out_charge = mpc.dispatch_step(low_tariff_in, high_future, OperatingMode.GRID_CONNECTED_NORMAL)
    assert out_charge.constraint_status == ConstraintStatus.OK
    assert out_charge.bess_charge_kw > 0.0
    assert verify_step_output(low_tariff_in, out_charge).is_balanced

    # High current tariff (0.25) followed by low tariff (0.08) -> discharges up to reserve limit
    high_tariff_in = _step_input(tariff_per_kwh=0.25, bess_soc_fraction=0.70)
    low_future = [
        ForecastPoint(
            dt_seconds=3600.0,
            local_load_kw=700.0,
            pv_available_kw=300.0,
            tariff_per_kwh=0.08,
        )
        for _ in range(4)
    ]
    out_dis = mpc.dispatch_step(high_tariff_in, low_future, OperatingMode.GRID_CONNECTED_NORMAL)
    assert out_dis.constraint_status == ConstraintStatus.OK
    assert out_dis.bess_discharge_kw == pytest.approx(840.0)
    assert verify_step_output(high_tariff_in, out_dis).is_balanced

    # At reserve_soc_fraction (0.30), MPC discharge is blocked
    at_reserve = _step_input(tariff_per_kwh=0.25, bess_soc_fraction=0.30)
    out_res = mpc.dispatch_step(at_reserve, low_future, OperatingMode.GRID_CONNECTED_NORMAL)
    assert out_res.bess_discharge_kw == 0.0
    assert verify_step_output(at_reserve, out_res).is_balanced


def test_hil_validation_suite_all_cases_pass() -> None:
    cfg = _load_cfg()
    report = HILValidationSuite(cfg).run_all()
    assert report.total_cases == 5
    assert report.all_passed is True, f"HIL failures: {report}"
