"""Unit tests for core.units and core.plant_model (Module 1)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from renewablex_controller.core import (
    BESSOperationalConfig,
    BiomassOperationalConfig,
    ControlPolicyConfig,
    GridOperationalConfig,
    PlantMetadataConfig,
    PlantOperationalConfig,
    PVOperationalConfig,
    dt_seconds_to_hours,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dt_seconds_to_hours_conversions() -> None:
    assert dt_seconds_to_hours(3600.0) == pytest.approx(1.0)
    assert dt_seconds_to_hours(900.0) == pytest.approx(0.25)
    assert dt_seconds_to_hours(60.0) == pytest.approx(1.0 / 60.0)

    for bad in (0.0, -10.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            dt_seconds_to_hours(bad)


def test_load_reference_hybrid_plant_json() -> None:
    cfg_path = REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    cfg = PlantOperationalConfig.from_json_file(cfg_path)

    assert cfg.plant_metadata.plant_id == "ref_plant_01"
    assert cfg.pv.rated_ac_kw == 550.0
    assert cfg.biomass.nameplate_kw == 2500.0
    assert cfg.biomass.operating_max_kw == 2000.0
    assert cfg.bess.soc_min_fraction < cfg.bess.reserve_soc_fraction < cfg.bess.soc_max_fraction
    assert cfg.control_policy.hardware_commands_enabled is False


def test_biomass_hierarchy_and_ramp_scaling() -> None:
    bio = BiomassOperationalConfig(
        nameplate_kw=2500.0,
        operating_max_kw=2000.0,
        min_load_kw=600.0,
        ramp_up_kw_per_hour=600.0,
        ramp_down_kw_per_hour=300.0,
        startup_time_seconds=1800.0,
        shutdown_time_seconds=1800.0,
        min_up_time_seconds=3600.0,
        min_down_time_seconds=3600.0,
    )
    assert bio.max_ramp_up_kw(900.0) == pytest.approx(150.0)
    assert bio.max_ramp_down_kw(60.0) == pytest.approx(5.0)

    with pytest.raises(ValidationError):
        BiomassOperationalConfig(
            nameplate_kw=2000.0,
            operating_max_kw=2500.0,
            min_load_kw=500.0,
            ramp_up_kw_per_hour=600.0,
            ramp_down_kw_per_hour=600.0,
            startup_time_seconds=0.0,
            shutdown_time_seconds=0.0,
            min_up_time_seconds=0.0,
            min_down_time_seconds=0.0,
        )

    with pytest.raises(ValidationError):
        BiomassOperationalConfig(
            nameplate_kw=2500.0,
            operating_max_kw=2000.0,
            min_load_kw=2100.0,
            ramp_up_kw_per_hour=600.0,
            ramp_down_kw_per_hour=600.0,
            startup_time_seconds=0.0,
            shutdown_time_seconds=0.0,
            min_up_time_seconds=0.0,
            min_down_time_seconds=0.0,
        )


def test_bess_bounds_and_step_energy_limits() -> None:
    bess = BESSOperationalConfig(
        nominal_capacity_kwh=2500.0,
        effective_capacity_kwh=2000.0,
        max_charge_kw=1000.0,
        max_discharge_kw=1000.0,
        soc_min_fraction=0.15,
        reserve_soc_fraction=0.30,
        soc_max_fraction=0.95,
        charge_efficiency=0.90,
        discharge_efficiency=0.95,
    )
    # 0.05 headroom * 2000 kWh = 100 kWh stored -> AC limit over 0.25h at 0.90 eff = 444.44 kW
    assert bess.max_charge_kw_for_step(0.90, 900.0) == pytest.approx(100.0 / (0.25 * 0.90))
    # Above soc_max gives 0.0
    assert bess.max_charge_kw_for_step(0.96, 900.0) == 0.0
    # Discharge against 0.30 floor from 0.35 SoC over 1h at 0.95 eff = 95 kW
    dis_kw = bess.max_discharge_kw_for_step(0.35, 3600.0, soc_floor_fraction=0.30)
    assert dis_kw == pytest.approx(95.0)
    # Discharge floor clamps to at least soc_min_fraction
    assert bess.max_discharge_kw_for_step(0.35, 3600.0, soc_floor_fraction=0.05) == pytest.approx(
        0.20 * 2000.0 * 0.95
    )

    with pytest.raises(ValidationError):
        BESSOperationalConfig(
            nominal_capacity_kwh=1000.0,
            effective_capacity_kwh=1200.0,
            max_charge_kw=500.0,
            max_discharge_kw=500.0,
            soc_min_fraction=0.1,
            reserve_soc_fraction=0.2,
            soc_max_fraction=0.9,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        )

    with pytest.raises(ValidationError):
        BESSOperationalConfig(
            nominal_capacity_kwh=1000.0,
            effective_capacity_kwh=1000.0,
            max_charge_kw=500.0,
            max_discharge_kw=500.0,
            soc_min_fraction=0.4,
            reserve_soc_fraction=0.2,
            soc_max_fraction=0.9,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        )


def test_grid_and_control_policy_validations() -> None:
    with pytest.raises(ValidationError):
        GridOperationalConfig(interconnection_limit_kw=2000.0, default_export_limit_kw=2500.0)

    with pytest.raises(ValidationError):
        ControlPolicyConfig(hardware_commands_enabled=True)

    with pytest.raises(ValidationError):
        PlantMetadataConfig(plant_id="", plant_name="Test")

    pv = PVOperationalConfig(nameplate_kwp=600.0, rated_ac_kw=550.0)
    assert pv.available is True
