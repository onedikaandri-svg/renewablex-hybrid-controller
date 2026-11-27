"""Unit tests for Module 6 SequenceManager."""

from pathlib import Path

from renewablex_controller.core import (
    BiomassStatus,
    ControllerStepInput,
    PlantOperationalConfig,
    SequenceKind,
    SequenceManager,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def _step_input(**overrides: object) -> ControllerStepInput:
    base: dict[str, object] = {
        "step_idx": 0,
        "dt_seconds": 60.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 700.0,
        "parasitic_load_kw": 40.0,
        "pv_available_kw": 300.0,
        "bess_soc_fraction": 0.50,
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
    }
    base.update(overrides)
    return ControllerStepInput.model_validate(base)


def test_islanding_prep_multi_step_and_grid_abort() -> None:
    cfg = _load_cfg()
    seq = SequenceManager(cfg, islanding_prep_steps=2)

    res1 = seq.advance(
        SequenceKind.ISLANDING_PREP,
        _step_input(grid_connected=False),
        current_step=0,
        elapsed_seconds=0.0,
    )
    assert res1.completed is False
    assert res1.sequence_step == 1

    res2 = seq.advance(
        SequenceKind.ISLANDING_PREP,
        _step_input(grid_connected=False),
        current_step=res1.sequence_step,
        elapsed_seconds=res1.sequence_elapsed_seconds,
    )
    assert res2.completed is True
    assert res2.sequence_state == "islanding_prep_complete"

    aborted = seq.advance(
        SequenceKind.ISLANDING_PREP,
        _step_input(grid_connected=True),
        current_step=0,
        elapsed_seconds=0.0,
    )
    assert aborted.completed is False
    assert "GRID_STILL_CONNECTED" in aborted.permissive_blocks


def test_grid_restoration_requires_sync_and_operator_ack() -> None:
    cfg = _load_cfg()
    seq = SequenceManager(cfg)

    blocked = seq.advance(
        SequenceKind.GRID_RESTORATION,
        _step_input(grid_connected=True, sync_permissive=False, operator_ack_restoration=False),
        current_step=0,
        elapsed_seconds=0.0,
    )
    assert blocked.completed is False
    assert "SYNC_PERMISSIVE_MISSING" in blocked.permissive_blocks
    assert "OPERATOR_ACK_RESTORATION_MISSING" in blocked.permissive_blocks

    ready = seq.advance(
        SequenceKind.GRID_RESTORATION,
        _step_input(grid_connected=True, sync_permissive=True, operator_ack_restoration=True),
        current_step=blocked.sequence_step,
        elapsed_seconds=blocked.sequence_elapsed_seconds,
    )
    assert ready.completed is True
    assert ready.sequence_state == "grid_restoration_complete"


def test_biomass_maintenance_entry_and_exit_sequences() -> None:
    cfg = _load_cfg()
    seq = SequenceManager(cfg)

    cooldown = seq.advance(
        SequenceKind.BIOMASS_MAINTENANCE_ENTRY,
        _step_input(biomass_status=BiomassStatus.RUNNING, biomass_current_power_kw=800.0),
        current_step=0,
        elapsed_seconds=0.0,
    )
    assert cooldown.completed is False
    assert "BIOMASS_STILL_RUNNING" in cooldown.permissive_blocks

    locked = seq.advance(
        SequenceKind.BIOMASS_MAINTENANCE_EXIT,
        _step_input(biomass_maintenance_mode=True),
        current_step=0,
        elapsed_seconds=0.0,
    )
    assert locked.completed is False
    assert "MAINTENANCE_FLAG_ACTIVE" in locked.permissive_blocks
