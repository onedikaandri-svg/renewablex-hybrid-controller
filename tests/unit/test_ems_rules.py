"""Unit tests for Module 3 JSON EMS rule schema validator and interpreter."""

from pathlib import Path
from typing import Any

import pytest

from renewablex_controller.core import (
    BiomassStatus,
    ControllerStepInput,
    EMSRuleInterpreter,
    EMSRuleSet,
    EMSRuleValidationError,
    OperatingMode,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_rule_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "ems_schema_version": "0.1",
        "name": "test_rules",
        "params": {"soc_min": 0.20, "limit_kw": 500.0},
        "rules": [
            {
                "id": "r1",
                "priority": 10,
                "mode_scope": ["grid_connected_normal"],
                "when": {"var": "pv_surplus_kw", "op": ">", "value": 0.0},
                "then": [{"action": "charge_bess", "limit": "pv_surplus_kw"}],
            }
        ],
    }
    doc.update(overrides)
    return doc


def _step_input(**overrides: Any) -> ControllerStepInput:
    base: dict[str, Any] = {
        "step_idx": 0,
        "dt_seconds": 3600.0,
        "hour_of_day": 12,
        "month": 6,
        "local_load_kw": 400.0,
        "parasitic_load_kw": 20.0,
        "pv_available_kw": 600.0,
        "bess_soc_fraction": 0.50,
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


def test_load_example_ems_rule_files() -> None:
    peak_cut = EMSRuleSet.from_json_file(REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json")
    islanded = EMSRuleSet.from_json_file(
        REPO_ROOT / "examples" / "ems_rules" / "islanded_reserve_example.json"
    )
    assert peak_cut.ems_schema_version == "0.1"
    assert len(peak_cut.rules) == 3
    assert len(islanded.rules) == 2


@pytest.mark.parametrize(
    ("expected_code", "patch"),
    [
        ("EMS_ERR_UNSUPPORTED_VERSION", {"ems_schema_version": "0.2"}),
        (
            "EMS_ERR_DUPLICATE_RULE_ID",
            {
                "rules": [
                    {
                        "id": "dup",
                        "mode_scope": ["*"],
                        "when": {"var": "grid_connected", "op": "==", "value": True},
                        "then": [{"action": "hold_bess"}],
                    },
                    {
                        "id": "dup",
                        "mode_scope": ["*"],
                        "when": {"var": "grid_connected", "op": "==", "value": False},
                        "then": [{"action": "hold_bess"}],
                    },
                ]
            },
        ),
        (
            "EMS_ERR_INVALID_MODE_SCOPE",
            {
                "rules": [
                    {
                        "id": "r_bad_mode",
                        "mode_scope": ["nonexistent_mode"],
                        "when": {"var": "grid_connected", "op": "==", "value": True},
                        "then": [{"action": "hold_bess"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_UNKNOWN_VARIABLE",
            {
                "rules": [
                    {
                        "id": "r_bad_var",
                        "mode_scope": ["*"],
                        "when": {"var": "__import__('os')", "op": "==", "value": 1},
                        "then": [{"action": "hold_bess"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_UNKNOWN_OPERATOR",
            {
                "rules": [
                    {
                        "id": "r_bad_op",
                        "mode_scope": ["*"],
                        "when": {"var": "pv_surplus_kw", "op": "eval", "value": 0.0},
                        "then": [{"action": "hold_bess"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_UNKNOWN_ACTION",
            {
                "rules": [
                    {
                        "id": "r_bad_action",
                        "mode_scope": ["*"],
                        "when": {"var": "grid_connected", "op": "==", "value": True},
                        "then": [{"action": "exec_shell"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_UNRESOLVED_PARAM",
            {
                "rules": [
                    {
                        "id": "r_bad_param",
                        "mode_scope": ["*"],
                        "when": {"var": "bess_soc_fraction", "op": "<", "value": "$params.missing"},
                        "then": [{"action": "hold_bess"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_TYPE_MISMATCH",
            {
                "rules": [
                    {
                        "id": "r_bad_type",
                        "mode_scope": ["*"],
                        "when": {"var": "biomass_status", "op": "<", "value": "running"},
                        "then": [{"action": "hold_bess"}],
                    }
                ]
            },
        ),
        (
            "EMS_ERR_INVALID_ACTION_ARG",
            {
                "rules": [
                    {
                        "id": "r_neg_arg",
                        "mode_scope": ["*"],
                        "when": {"var": "grid_connected", "op": "==", "value": True},
                        "then": [{"action": "charge_bess", "limit": -50.0}],
                    }
                ]
            },
        ),
    ],
)
def test_schema_validator_error_codes(expected_code: str, patch: dict[str, Any]) -> None:
    doc = _base_rule_doc(**patch)
    with pytest.raises(EMSRuleValidationError) as exc_info:
        EMSRuleSet.from_dict(doc)
    assert exc_info.value.code == expected_code


def test_max_condition_depth_exceeded() -> None:
    node: dict[str, Any] = {"var": "grid_connected", "op": "==", "value": True}
    for _ in range(8):
        node = {"not": node}
    doc = _base_rule_doc(
        rules=[
            {
                "id": "r_deep",
                "mode_scope": ["*"],
                "when": node,
                "then": [{"action": "hold_bess"}],
            }
        ]
    )
    with pytest.raises(EMSRuleValidationError) as exc_info:
        EMSRuleSet.from_dict(doc)
    assert exc_info.value.code == "EMS_ERR_MAX_DEPTH_EXCEEDED"


def test_null_safe_comparisons_and_priority_conflict_resolution() -> None:
    doc = _base_rule_doc(
        rules=[
            {
                "id": "r_low_prio",
                "priority": 50,
                "mode_scope": ["grid_connected_normal"],
                "when": {"var": "pv_surplus_kw", "op": ">", "value": 0.0},
                "then": [{"action": "discharge_bess", "limit": 100.0}],
            },
            {
                "id": "r_high_prio",
                "priority": 10,
                "mode_scope": ["grid_connected_normal"],
                "when": {
                    "all": [
                        {"var": "grid_export_target_kw", "op": "==", "value": None},
                        {"not": {"var": "grid_export_target_kw", "op": ">", "value": 100.0}},
                    ]
                },
                "then": [{"action": "charge_bess", "limit": "$params.limit_kw"}],
            },
        ]
    )
    rule_set = EMSRuleSet.from_dict(doc)
    interpreter = EMSRuleInterpreter()
    res = interpreter.evaluate(
        rule_set,
        _step_input(grid_export_target_kw=None),
        OperatingMode.GRID_CONNECTED_NORMAL,
    )
    assert res.active_rule_ids == ["r_high_prio"]
    assert res.skipped_rule_ids == ["r_low_prio"]
    assert res.intents["bess"] == ("charge_bess", 500.0)
