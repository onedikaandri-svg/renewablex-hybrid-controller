"""Unit tests for Module 4 BESSSupervisoryManager."""

import pytest

from renewablex_controller.core import (
    BESSOperationalConfig,
    BESSSupervisoryManager,
    BiomassStatus,
    ControllerStepInput,
    OperatingMode,
)


def _bess_config(allow_drawdown: bool = True) -> BESSOperationalConfig:
    return BESSOperationalConfig(
        nominal_capacity_kwh=2500.0,
        effective_capacity_kwh=2000.0,
        max_charge_kw=1000.0,
        max_discharge_kw=1000.0,
        soc_min_fraction=0.15,
        reserve_soc_fraction=0.30,
        soc_max_fraction=0.95,
        charge_efficiency=0.90,
        discharge_efficiency=0.95,
        allow_reserve_drawdown_in_islanded=allow_drawdown,
    )


def _step_input(soc: float, dt_seconds: float = 3600.0) -> ControllerStepInput:
    return ControllerStepInput(
        step_idx=0,
        dt_seconds=dt_seconds,
        hour_of_day=12,
        month=6,
        local_load_kw=500.0,
        parasitic_load_kw=20.0,
        pv_available_kw=300.0,
        bess_soc_fraction=soc,
        bess_max_charge_kw=1000.0,
        bess_max_discharge_kw=1000.0,
        biomass_status=BiomassStatus.OFF,
        biomass_available_kw=0.0,
        biomass_current_power_kw=0.0,
        biomass_min_load_kw=0.0,
        biomass_can_start=True,
        biomass_can_stop=False,
        allow_grid_import=True,
        allow_grid_export=True,
    )


def test_reserve_protection_vs_islanded_drawdown() -> None:
    mgr = BESSSupervisoryManager(_bess_config(allow_drawdown=True))
    step_in = _step_input(soc=0.30)

    cap_normal = mgr.compute_capability(step_in, OperatingMode.GRID_CONNECTED_NORMAL)
    assert cap_normal.reserve_protected is True
    assert cap_normal.active_soc_floor_fraction == pytest.approx(0.30)
    assert cap_normal.max_discharge_kw == pytest.approx(0.0)
    assert cap_normal.max_charge_kw == pytest.approx(1000.0)

    cap_island = mgr.compute_capability(step_in, OperatingMode.ISLANDED)
    assert cap_island.reserve_protected is False
    assert cap_island.active_soc_floor_fraction == pytest.approx(0.15)
    # (0.30 - 0.15) * 2000 * 0.95 / 1h = 285.0 kW
    assert cap_island.max_discharge_kw == pytest.approx(285.0)

    mgr_locked = BESSSupervisoryManager(_bess_config(allow_drawdown=False))
    cap_locked = mgr_locked.compute_capability(step_in, OperatingMode.ISLANDED)
    assert cap_locked.reserve_protected is True
    assert cap_locked.max_discharge_kw == pytest.approx(0.0)


def test_allocate_power_and_project_next_soc() -> None:
    mgr = BESSSupervisoryManager(_bess_config())
    cap = mgr.compute_capability(_step_input(soc=0.50, dt_seconds=900.0), OperatingMode.PEAK_CUT)

    chg, dis = mgr.allocate_power(cap, requested_charge_kw=600.0, requested_discharge_kw=200.0)
    assert chg == pytest.approx(400.0)
    assert dis == 0.0

    # Charge 400 kW for 0.25h at 0.90 eff = +90 kWh / 2000 kWh = +0.045 SoC
    next_soc = mgr.project_next_soc(0.50, charge_kw=400.0, discharge_kw=0.0, dt_seconds=900.0)
    assert next_soc == pytest.approx(0.545)

    # Discharge 475 kW for 1.0h at 0.95 eff = -500 kWh / 2000 kWh = -0.25 SoC
    after_dis = mgr.project_next_soc(0.50, charge_kw=0.0, discharge_kw=475.0, dt_seconds=3600.0)
    assert after_dis == pytest.approx(0.25)

    with pytest.raises(ValueError):
        mgr.project_next_soc(0.50, charge_kw=10.0, discharge_kw=10.0, dt_seconds=900.0)
    with pytest.raises(ValueError):
        mgr.allocate_power(cap, requested_charge_kw=-1.0)
