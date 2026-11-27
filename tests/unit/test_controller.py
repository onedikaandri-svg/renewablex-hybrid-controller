"""Unit tests for unified HybridController facade and FSM adapter mode."""

from dataclasses import dataclass
from pathlib import Path

from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    EMSRuleSet,
    HybridController,
    OperatingMode,
    PlantOperationalConfig,
    verify_step_output,
)
from renewablex_controller.integration import RenewableXEMSAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(step_idx: int, **overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": step_idx,
        "dt_seconds": 900.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 750.0,
        "parasitic_load_kw": 40.0,
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
        "allow_grid_import": True,
        "allow_grid_export": True,
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_hybrid_controller_deterministic_replay_and_invalid_request() -> None:
    cfg = _load_cfg()
    rules = EMSRuleSet.from_json_file(
        REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json"
    )
    ctrl_a = HybridController(cfg, rule_set=rules)
    ctrl_b = HybridController(cfg, rule_set=rules)

    steps = [
        _step_input(0, grid_export_limit_kw=2000.0),
        _step_input(1, grid_connected=False, allow_grid_import=False, allow_grid_export=False),
        _step_input(2, grid_connected=False, allow_grid_import=False, allow_grid_export=False),
        _step_input(3, grid_connected=True),
        _step_input(4, grid_connected=True, sync_permissive=True, operator_ack_restoration=True),
    ]

    for step in steps:
        out_a = ctrl_a.step(step)
        out_b = ctrl_b.step(step)
        assert out_a == out_b
        assert out_a.constraint_status in {ConstraintStatus.OK, ConstraintStatus.CLAMPED}
        assert verify_step_output(step, out_a).is_balanced

    assert ctrl_a.state == ctrl_b.state
    assert ctrl_a.state.operating_mode == OperatingMode.GRID_CONNECTED_NORMAL
    assert len(ctrl_a.state.event_log) >= 4

    # Requesting an invalid transition keeps current mode and surfaces violation
    bad_out = ctrl_a.step(_step_input(5), requested_mode=OperatingMode.ISLANDED)
    assert bad_out.operating_mode == OperatingMode.GRID_CONNECTED_NORMAL
    assert "INVALID_MODE_TRANSITION:grid_connected_normal->islanded" in bad_out.violations


@dataclass
class _StubCtx:
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


def test_adapter_with_fsm_enabled() -> None:
    cfg = _load_cfg()
    adapter = RenewableXEMSAdapter(cfg, use_fsm=True)
    ctx = _StubCtx(
        step_idx=0,
        hour_of_day=10,
        month=5,
        load_kw=500.0,
        parasitic_load_kw=30.0,
        pv_available_kw=400.0,
        bess_soc=0.50,
        bess_max_charge_kw=1000.0,
        bess_max_discharge_kw=1000.0,
        biomass_status="running",
        biomass_available_kw=2000.0,
        biomass_current_power_kw=1200.0,
        biomass_min_load_kw=600.0,
        biomass_can_start=False,
        biomass_can_stop=True,
        biomass_fuel_remaining_tons=50.0,
        allow_grid_import=True,
        allow_grid_export=True,
        tariff_per_kwh=0.12,
    )
    decision, out = adapter.step_from_context(
        ctx,
        dt_seconds=900.0,
        grid_export_limit_kw=1800.0,
    )
    assert decision.operating_mode == OperatingMode.PEAK_CUT
    assert out.grid_export_kw <= 1800.0 + 1e-6
    assert adapter.controller_state.operating_mode == OperatingMode.PEAK_CUT
