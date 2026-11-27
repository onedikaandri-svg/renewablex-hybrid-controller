"""Unit and 24-hour multi-step tests for Module 3 PowerDispatchEngine."""

from pathlib import Path

import pytest

from renewablex_controller.core import (
    BESSSupervisoryManager,
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    EMSRuleSet,
    OperatingMode,
    PlantOperationalConfig,
    PowerDispatchEngine,
    SafetySupervisor,
    verify_step_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_plant() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(**overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": 0,
        "dt_seconds": 3600.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 700.0,
        "parasitic_load_kw": 50.0,
        "pv_available_kw": 450.0,
        "bess_soc_fraction": 0.55,
        "bess_max_charge_kw": 1000.0,
        "bess_max_discharge_kw": 1000.0,
        "biomass_status": BiomassStatus.RUNNING,
        "biomass_available_kw": 2000.0,
        "biomass_current_power_kw": 1500.0,
        "biomass_min_load_kw": 600.0,
        "biomass_can_start": False,
        "biomass_can_stop": True,
        "biomass_fuel_remaining_tons": 80.0,
        "allow_grid_import": True,
        "allow_grid_export": True,
        "tariff_per_kwh": 0.12,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_normal_export_target_tracking() -> None:
    cfg = _load_plant()
    engine = PowerDispatchEngine(cfg)
    # Demand = 750 kW, export target = 1500 kW -> total needed = 2250 kW
    # PV = 450 kW, biomass can supply 1800 kW (within 1500 +/- 600 ramp)
    step_in = _step_input(grid_export_target_kw=1500.0)
    out = engine.dispatch_step(step_in, OperatingMode.GRID_CONNECTED_NORMAL)

    assert out.constraint_status == ConstraintStatus.OK
    assert out.pv_command_kw == pytest.approx(450.0)
    assert out.biomass_command_kw == pytest.approx(1800.0)
    assert out.p_pcc_export_kw == pytest.approx(1500.0)
    assert verify_step_output(step_in, out).is_balanced


def test_peak_cut_with_json_rules_buffers_excess_and_curtails() -> None:
    cfg = _load_plant()
    rules = EMSRuleSet.from_json_file(REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json")
    engine = PowerDispatchEngine(cfg, rule_set=rules)

    # Low load (200+50 = 250 kW), high PV (550 kW -> pv_surplus = 300 kW), biomass at 1950 kW
    # Min biomass after 600 kW/h ramp down is 1350 kW -> generation exceeds load + 2000 limit
    step_in = _step_input(
        local_load_kw=200.0,
        parasitic_load_kw=50.0,
        pv_available_kw=550.0,
        biomass_current_power_kw=1950.0,
        grid_export_limit_kw=2000.0,
    )
    out = engine.dispatch_step(step_in, OperatingMode.PEAK_CUT)

    assert out.constraint_status in {ConstraintStatus.OK, ConstraintStatus.CLAMPED}
    assert out.grid_export_kw <= 2000.0 + 1e-6
    assert "r_enforce_peak_cut_limit" in out.active_rule_ids
    assert "r_peak_cut_charge_surplus" in out.active_rule_ids
    assert out.bess_charge_kw > 0.0
    assert verify_step_output(step_in, out).is_balanced


def test_islanded_and_biomass_maintenance_with_unmet_load() -> None:
    cfg = _load_plant()
    rules = EMSRuleSet.from_json_file(
        REPO_ROOT / "examples" / "ems_rules" / "islanded_reserve_example.json"
    )
    engine = PowerDispatchEngine(cfg, rule_set=rules)

    # Off-grid maintenance with insufficient PV + BESS
    step_in = _step_input(
        grid_connected=False,
        biomass_maintenance_mode=True,
        local_load_kw=1400.0,
        parasitic_load_kw=50.0,
        pv_available_kw=200.0,
        bess_soc_fraction=0.25,
    )
    out = engine.dispatch_step(step_in, OperatingMode.BIOMASS_MAINTENANCE)

    assert out.biomass_command_kw == 0.0
    assert out.grid_import_kw == 0.0
    assert out.grid_export_kw == 0.0
    assert out.bess_discharge_kw > 0.0
    assert out.unmet_load_kw > 0.0
    assert "r_islanded_discharge_to_min_soc" in out.active_rule_ids
    assert verify_step_output(step_in, out).is_balanced


def test_unsafe_json_rule_clamped_by_safety_supervisor() -> None:
    cfg = _load_plant()
    unsafe_rules = EMSRuleSet.from_dict(
        {
            "ems_schema_version": "0.1",
            "name": "unsafe_test",
            "rules": [
                {
                    "id": "r_overdrive_bio",
                    "priority": 1,
                    "mode_scope": ["*"],
                    "when": {"var": "grid_connected", "op": "==", "value": True},
                    "then": [
                        {"action": "set_biomass_target", "target": 2400.0},
                        {"action": "start_biomass"},
                    ],
                }
            ],
        }
    )
    engine = PowerDispatchEngine(cfg, rule_set=unsafe_rules)
    sup = SafetySupervisor(cfg)

    step_in = _step_input(biomass_maintenance_mode=True, biomass_current_power_kw=0.0)
    out = engine.dispatch_step(step_in, OperatingMode.BIOMASS_MAINTENANCE, supervisor=sup)

    assert out.constraint_status == ConstraintStatus.CLAMPED
    assert out.biomass_command_kw == 0.0
    assert out.biomass_start_request is False
    assert "BIOMASS_UNAVAILABLE_OR_MAINTENANCE" in out.violations
    assert "BIOMASS_START_BLOCKED" in out.violations
    assert verify_step_output(step_in, out).is_balanced


def test_24_hour_deterministic_dispatch_simulation() -> None:
    cfg = _load_plant()
    rules = EMSRuleSet.from_json_file(REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json")
    engine = PowerDispatchEngine(cfg, rule_set=rules)
    bess_mgr = BESSSupervisoryManager(cfg.bess)
    sup = SafetySupervisor(cfg)

    soc = 0.50
    bio_power = 1400.0
    pv_curve = [
        0.0, 0.0, 0.0, 0.0, 0.0, 20.0,
        120.0, 280.0, 420.0, 520.0, 580.0, 600.0,
        590.0, 540.0, 460.0, 350.0, 210.0, 80.0,
        10.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ]
    load_curve = [
        650.0, 620.0, 600.0, 600.0, 630.0, 700.0,
        820.0, 950.0, 1050.0, 1100.0, 1120.0, 1150.0,
        1100.0, 1080.0, 1050.0, 1020.0, 1080.0, 1200.0,
        1320.0, 1300.0, 1200.0, 1050.0, 880.0, 740.0,
    ]

    for hour in range(24):
        step_in = _step_input(
            step_idx=hour,
            dt_seconds=3600.0,
            hour_of_day=hour,
            local_load_kw=load_curve[hour],
            parasitic_load_kw=40.0,
            pv_available_kw=pv_curve[hour],
            bess_soc_fraction=soc,
            biomass_current_power_kw=bio_power,
            grid_export_target_kw=1200.0,
            grid_export_limit_kw=2000.0,
        )
        out = engine.dispatch_step(step_in, OperatingMode.PEAK_CUT, supervisor=sup)
        assert out.constraint_status in {ConstraintStatus.OK, ConstraintStatus.CLAMPED}
        assert out.bess_charge_kw * out.bess_discharge_kw == 0.0
        assert out.grid_import_kw * out.grid_export_kw == 0.0
        assert out.grid_export_kw <= 2000.0 + 1e-6
        assert verify_step_output(step_in, out).is_balanced

        soc = bess_mgr.project_next_soc(
            soc,
            charge_kw=out.bess_charge_kw,
            discharge_kw=out.bess_discharge_kw,
            dt_seconds=3600.0,
        )
        assert cfg.bess.soc_min_fraction - 1e-9 <= soc <= cfg.bess.soc_max_fraction + 1e-9
        bio_power = out.biomass_command_kw
