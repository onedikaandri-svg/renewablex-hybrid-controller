"""Phase 4 end-to-end validation scenario tests and JSON/CSV report checks."""

import csv
import json
from pathlib import Path

from renewablex_controller.core import EMSRuleSet, OperatingMode, PlantOperationalConfig
from renewablex_controller.simulation import (
    ControllerSimulationHarness,
    export_result_json,
    export_schedule_csv,
    format_summary_table,
    get_standard_scenarios,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def test_all_standard_scenarios_pass_invariants() -> None:
    cfg = _load_cfg()
    harness = ControllerSimulationHarness(cfg)
    scenarios = get_standard_scenarios()
    assert len(scenarios) == 7

    results = [harness.run_scenario(sc) for sc in scenarios.values()]
    for res in results:
        assert res.summary.all_invariants_passed is True, (
            f"Scenario {res.scenario_name} failed invariants: {res.summary}"
        )
        assert res.summary.max_abs_balance_residual_kw < 1e-6

    table = format_summary_table(results)
    assert "normal_export_tracking" in table
    assert "FAIL" not in table


def test_scenario_trajectories_and_json_rules() -> None:
    cfg = _load_cfg()
    peak_rules = EMSRuleSet.from_json_file(
        REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json"
    )
    island_rules = EMSRuleSet.from_json_file(
        REPO_ROOT / "examples" / "ems_rules" / "islanded_reserve_example.json"
    )
    scenarios = get_standard_scenarios()

    # Peak cut with JSON rules caps export at 2000 kW
    peak_res = ControllerSimulationHarness(cfg, rule_set=peak_rules).run_scenario(
        scenarios["peak_cut_export_limit"]
    )
    assert peak_res.summary.all_invariants_passed is True
    assert OperatingMode.PEAK_CUT.value in peak_res.summary.modes_visited
    assert all(rec.grid_export_kw <= 2000.0 + 1e-6 for rec in peak_res.records)

    # Momentary outage stays out of prolonged_outage and islanded
    mom_res = ControllerSimulationHarness(cfg).run_scenario(
        scenarios["momentary_grid_outage"]
    )
    assert OperatingMode.MOMENTARY_OUTAGE.value in mom_res.summary.modes_visited
    assert OperatingMode.ISLANDED.value not in mom_res.summary.modes_visited

    # Prolonged outage enters islanded and draws BESS below reserve_soc_fraction (0.30)
    isl_res = ControllerSimulationHarness(cfg, rule_set=island_rules).run_scenario(
        scenarios["prolonged_outage_islanded"]
    )
    assert isl_res.summary.all_invariants_passed is True
    assert OperatingMode.PROLONGED_OUTAGE.value in isl_res.summary.modes_visited
    assert OperatingMode.ISLANDED.value in isl_res.summary.modes_visited

    # Biomass maintenance locks biomass to 0 kW during maintenance steps
    maint_res = ControllerSimulationHarness(cfg).run_scenario(
        scenarios["biomass_maintenance_pv_bess"]
    )
    assert OperatingMode.BIOMASS_MAINTENANCE.value in maint_res.summary.modes_visited
    for rec in maint_res.records:
        if rec.operating_mode == OperatingMode.BIOMASS_MAINTENANCE:
            assert rec.biomass_command_kw == 0.0

    # Grid restoration transitions islanded -> grid_restoration -> grid_connected_normal
    rest_res = ControllerSimulationHarness(cfg).run_scenario(
        scenarios["grid_restoration_sync"]
    )
    assert OperatingMode.GRID_RESTORATION.value in rest_res.summary.modes_visited
    assert rest_res.records[-1].operating_mode == OperatingMode.GRID_CONNECTED_NORMAL

    # Telemetry comm failure triggers emergency_fault then recovers with operator reset
    comm_res = ControllerSimulationHarness(cfg).run_scenario(
        scenarios["telemetry_comm_failure"]
    )
    assert OperatingMode.EMERGENCY_FAULT.value in comm_res.summary.modes_visited
    assert comm_res.summary.rejected_steps == 1
    assert comm_res.records[-1].operating_mode == OperatingMode.GRID_CONNECTED_NORMAL


def test_json_and_csv_report_exports(tmp_path: Path) -> None:
    cfg = _load_cfg()
    scenarios = get_standard_scenarios()
    res = ControllerSimulationHarness(cfg).run_scenario(scenarios["grid_restoration_sync"])

    json_path = export_result_json(res, tmp_path / "reports" / "restoration.json")
    csv_path = export_schedule_csv(res, tmp_path / "reports" / "restoration.csv")

    assert json_path.is_file()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["scenario_name"] == "grid_restoration_sync"
    assert len(parsed["records"]) == len(res.records)

    assert csv_path.is_file()
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(res.records)
    assert rows[0]["operating_mode"] == OperatingMode.GRID_CONNECTED_NORMAL.value
