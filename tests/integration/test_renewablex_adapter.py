"""Integration tests for RenewableX adapter compatibility and sign translation."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from renewablex_controller.core import OperatingMode, PlantOperationalConfig
from renewablex_controller.integration import (
    RenewableXEMSAdapter,
    from_renewablex_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _StubDispatchContext:
    step_idx: int
    hour_of_day: int
    month: int
    load_kw: float
    parasitic_load_kw: float
    pv_available_kw: float
    bess_soc: float
    bess_max_charge_kw: float
    bess_max_discharge_kw: float
    biomass_status: str
    biomass_available_kw: float
    biomass_current_power_kw: float
    biomass_min_load_kw: float
    biomass_can_start: bool
    biomass_can_stop: bool
    biomass_fuel_remaining_tons: float | None
    allow_grid_import: bool
    allow_grid_export: bool
    tariff_per_kwh: float


def test_adapter_context_mapping_and_bess_sign_translation() -> None:
    cfg = PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )
    adapter = RenewableXEMSAdapter(cfg)

    # Case 1: PV surplus charges BESS -> RenewableX bess_command_kw must be POSITIVE
    ctx_charge = _StubDispatchContext(
        step_idx=10,
        hour_of_day=12,
        month=7,
        load_kw=200.0,
        parasitic_load_kw=20.0,
        pv_available_kw=500.0,
        bess_soc=0.50,
        bess_max_charge_kw=1000.0,
        bess_max_discharge_kw=1000.0,
        biomass_status="off",
        biomass_available_kw=0.0,
        biomass_current_power_kw=0.0,
        biomass_min_load_kw=0.0,
        biomass_can_start=False,
        biomass_can_stop=False,
        biomass_fuel_remaining_tons=None,
        allow_grid_import=False,
        allow_grid_export=False,
        tariff_per_kwh=0.15,
    )
    step_in = from_renewablex_context(ctx_charge, dt_seconds=900.0, grid_connected=False)
    assert step_in.local_load_kw == 200.0
    assert step_in.dt_seconds == 900.0
    assert step_in.grid_connected is False

    decision_chg, out_chg = adapter.step_from_context(
        ctx_charge,
        dt_seconds=900.0,
        operating_mode=OperatingMode.ISLANDED,
        grid_connected=False,
    )
    assert out_chg.bess_charge_kw == pytest.approx(280.0)
    assert out_chg.bess_discharge_kw == pytest.approx(0.0)
    assert decision_chg.bess_command_kw == pytest.approx(280.0)

    # Case 2: Islanded deficit discharges BESS -> RenewableX bess_command_kw must be NEGATIVE
    ctx_discharge = _StubDispatchContext(
        step_idx=11,
        hour_of_day=20,
        month=7,
        load_kw=600.0,
        parasitic_load_kw=20.0,
        pv_available_kw=0.0,
        bess_soc=0.60,
        bess_max_charge_kw=1000.0,
        bess_max_discharge_kw=1000.0,
        biomass_status="off",
        biomass_available_kw=0.0,
        biomass_current_power_kw=0.0,
        biomass_min_load_kw=0.0,
        biomass_can_start=False,
        biomass_can_stop=False,
        biomass_fuel_remaining_tons=None,
        allow_grid_import=False,
        allow_grid_export=False,
        tariff_per_kwh=0.15,
    )
    decision_dis, out_dis = adapter.step_from_context(
        ctx_discharge,
        dt_seconds=900.0,
        operating_mode=OperatingMode.ISLANDED,
        grid_connected=False,
    )
    assert out_dis.bess_discharge_kw == pytest.approx(620.0)
    assert decision_dis.bess_command_kw == pytest.approx(-620.0)
    assert decision_dis.curtailment_kw == out_dis.pv_curtailment_kw
