"""Module 3: Versioned pure-data JSON EMS rule validator and whitelist interpreter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput
from renewablex_controller.core.units import OperatingMode

SUPPORTED_EMS_SCHEMA_VERSION: str = "0.1"
MAX_CONDITION_DEPTH: int = 8

_VARIABLE_TYPES: dict[str, str] = {
    "pv_surplus_kw": "float",
    "net_load_kw": "float",
    "local_load_kw": "float",
    "pv_available_kw": "float",
    "bess_soc_fraction": "float",
    "bess_max_charge_kw": "float",
    "bess_max_discharge_kw": "float",
    "biomass_available_kw": "float",
    "tariff_per_kwh": "float",
    "grid_export_target_kw": "optional_float",
    "grid_export_limit_kw": "optional_float",
    "hour_of_day": "int",
    "month": "int",
    "biomass_can_start": "bool",
    "biomass_can_stop": "bool",
    "grid_connected": "bool",
    "biomass_status": "str",
    "operating_mode": "str",
}

_NUMERIC_VAR_TYPES = frozenset({"float", "optional_float", "int"})
_ORDERING_OPERATORS = frozenset({"<", "<=", ">", ">="})
_SET_OPERATORS = frozenset({"in", "not_in"})
_ALLOWED_OPERATORS = frozenset({"==", "!="}) | _ORDERING_OPERATORS | _SET_OPERATORS

_ACTION_SPECS: dict[str, tuple[str | None, str]] = {
    "charge_bess": ("limit", "bess"),
    "discharge_bess": ("limit", "bess"),
    "hold_bess": (None, "bess"),
    "set_biomass_target": ("target", "biomass_power"),
    "start_biomass": (None, "biomass_lifecycle"),
    "stop_biomass": (None, "biomass_lifecycle"),
    "curtail_pv": ("limit", "pv"),
    "enforce_export_limit": ("limit", "grid_export_limit"),
    "follow_export_target": ("target", "grid_export_target"),
}

_VALID_MODES = frozenset(mode.value for mode in OperatingMode) | {"*"}


class EMSRuleValidationError(ValueError):
    """Structured schema or whitelist validation failure for JSON EMS rules."""

    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.message = message
        self.path = path
        full = f"[{code}] {path}: {message}" if path else f"[{code}] {message}"
        super().__init__(full)


class EMSRuleAction(BaseModel):
    """Validated action node inside an EMS rule."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    action: str
    arg_name: str | None = None
    value_source: float | int | str | None = None
    intent_group: str


class EMSRule(BaseModel):
    """Validated single EMS rule with deterministic ordering metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: str
    enabled: bool = True
    priority: int
    declaration_index: int
    mode_scope: list[str]
    when: dict[str, Any]
    then: list[EMSRuleAction]


class EMSRuleSet(BaseModel):
    """Validated versioned JSON EMS rule set (pure data, zero code execution)."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    ems_schema_version: str
    name: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    priority: list[str] = Field(default_factory=lambda: ["pv", "bess", "biomass"])
    fallback_mode_dispatcher: bool = True
    rules: list[EMSRule]

    @classmethod
    def from_json_file(cls, path: str | Path) -> EMSRuleSet:
        """Load and strictly validate a versioned JSON EMS rule file."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EMSRuleSet:
        """Validate a dictionary against EMS Rule Schema v0.1."""
        if not isinstance(raw, dict):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "Root EMS rule document must be a JSON object",
            )

        version = raw.get("ems_schema_version")
        if version != SUPPORTED_EMS_SCHEMA_VERSION:
            raise EMSRuleValidationError(
                "EMS_ERR_UNSUPPORTED_VERSION",
                f"Expected ems_schema_version '{SUPPORTED_EMS_SCHEMA_VERSION}', got {version!r}",
                path="ems_schema_version",
            )

        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'params' must be a JSON object",
                path="params",
            )

        name = raw.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'name' must be a non-empty string",
                path="name",
            )

        description = raw.get("description", "")
        if not isinstance(description, str):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'description' must be a string",
                path="description",
            )

        priority_order = raw.get("priority", ["pv", "bess", "biomass"])
        if not isinstance(priority_order, list) or not all(
            isinstance(item, str) for item in priority_order
        ):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'priority' must be a list of strings",
                path="priority",
            )

        fallback_mode_dispatcher = raw.get("fallback_mode_dispatcher", True)
        if not isinstance(fallback_mode_dispatcher, bool):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'fallback_mode_dispatcher' must be a boolean",
                path="fallback_mode_dispatcher",
            )

        raw_rules = raw.get("rules")
        if not isinstance(raw_rules, list):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'rules' must be a list",
                path="rules",
            )

        seen_ids: set[str] = set()
        validated_rules: list[EMSRule] = []
        for idx, raw_rule in enumerate(raw_rules):
            rule_path = f"rules[{idx}]"
            if not isinstance(raw_rule, dict):
                raise EMSRuleValidationError(
                    "EMS_ERR_TYPE_MISMATCH",
                    "Each rule must be an object",
                    path=rule_path,
                )

            rule_id = raw_rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise EMSRuleValidationError(
                    "EMS_ERR_TYPE_MISMATCH",
                    "Rule 'id' must be a non-empty string",
                    path=f"{rule_path}.id",
                )
            if rule_id in seen_ids:
                raise EMSRuleValidationError(
                    "EMS_ERR_DUPLICATE_RULE_ID",
                    f"Duplicate rule id '{rule_id}'",
                    path=f"{rule_path}.id",
                )
            seen_ids.add(rule_id)

            enabled = raw_rule.get("enabled", True)
            if not isinstance(enabled, bool):
                raise EMSRuleValidationError(
                    "EMS_ERR_TYPE_MISMATCH",
                    "'enabled' must be a boolean",
                    path=f"{rule_path}.enabled",
                )

            rule_priority = raw_rule.get("priority", idx)
            if isinstance(rule_priority, bool) or not isinstance(rule_priority, int):
                raise EMSRuleValidationError(
                    "EMS_ERR_TYPE_MISMATCH",
                    "'priority' must be an integer",
                    path=f"{rule_path}.priority",
                )

            mode_scope = raw_rule.get("mode_scope")
            if (
                not isinstance(mode_scope, list)
                or len(mode_scope) == 0
                or not all(isinstance(m, str) and m in _VALID_MODES for m in mode_scope)
            ):
                raise EMSRuleValidationError(
                    "EMS_ERR_INVALID_MODE_SCOPE",
                    f"Invalid mode_scope: {mode_scope!r}",
                    path=f"{rule_path}.mode_scope",
                )

            when_node = _validate_condition_node(
                raw_rule.get("when"),
                params=params,
                depth=1,
                path=f"{rule_path}.when",
            )

            raw_then = raw_rule.get("then")
            if not isinstance(raw_then, list) or len(raw_then) == 0:
                raise EMSRuleValidationError(
                    "EMS_ERR_INVALID_ACTION_ARG",
                    "'then' must be a non-empty list of actions",
                    path=f"{rule_path}.then",
                )

            validated_actions = [
                _validate_action_node(
                    act,
                    params=params,
                    path=f"{rule_path}.then[{act_idx}]",
                )
                for act_idx, act in enumerate(raw_then)
            ]

            validated_rules.append(
                EMSRule(
                    id=rule_id,
                    enabled=enabled,
                    priority=rule_priority,
                    declaration_index=idx,
                    mode_scope=list(mode_scope),
                    when=when_node,
                    then=validated_actions,
                )
            )

        return cls(
            ems_schema_version=version,
            name=name,
            description=description,
            params=dict(params),
            priority=list(priority_order),
            fallback_mode_dispatcher=fallback_mode_dispatcher,
            rules=validated_rules,
        )


def _resolve_param_ref(ref: str, params: dict[str, Any], path: str) -> Any:
    key = ref[len("$params.") :]
    if not key or key not in params:
        raise EMSRuleValidationError(
            "EMS_ERR_UNRESOLVED_PARAM",
            f"Unresolved parameter reference '{ref}'",
            path=path,
        )
    return params[key]


def _validate_condition_node(
    node: Any,
    *,
    params: dict[str, Any],
    depth: int,
    path: str,
) -> dict[str, Any]:
    if depth > MAX_CONDITION_DEPTH:
        raise EMSRuleValidationError(
            "EMS_ERR_MAX_DEPTH_EXCEEDED",
            f"Condition depth {depth} exceeds max depth {MAX_CONDITION_DEPTH}",
            path=path,
        )
    if not isinstance(node, dict):
        raise EMSRuleValidationError(
            "EMS_ERR_TYPE_MISMATCH",
            "Condition node must be an object",
            path=path,
        )

    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        if len(node) != 1 or not isinstance(node[key], list) or len(node[key]) == 0:
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"'{key}' combinator must contain a non-empty list of condition nodes",
                path=path,
            )
        for idx, child in enumerate(node[key]):
            _validate_condition_node(
                child,
                params=params,
                depth=depth + 1,
                path=f"{path}.{key}[{idx}]",
            )
        return node

    if "not" in node:
        if len(node) != 1:
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                "'not' combinator must have a single child node",
                path=path,
            )
        _validate_condition_node(node["not"], params=params, depth=depth + 1, path=f"{path}.not")
        return node

    var_name = node.get("var")
    op = node.get("op")
    if not isinstance(var_name, str) or var_name not in _VARIABLE_TYPES:
        raise EMSRuleValidationError(
            "EMS_ERR_UNKNOWN_VARIABLE",
            f"Unknown variable {var_name!r}",
            path=f"{path}.var",
        )
    if not isinstance(op, str) or op not in _ALLOWED_OPERATORS:
        raise EMSRuleValidationError(
            "EMS_ERR_UNKNOWN_OPERATOR",
            f"Unknown operator {op!r}",
            path=f"{path}.op",
        )
    if "value" not in node:
        raise EMSRuleValidationError(
            "EMS_ERR_TYPE_MISMATCH",
            "Leaf condition must define 'value'",
            path=f"{path}.value",
        )

    raw_val = node["value"]
    resolved_val = (
        _resolve_param_ref(raw_val, params, f"{path}.value")
        if isinstance(raw_val, str) and raw_val.startswith("$params.")
        else raw_val
    )
    _validate_comparison_types(
        var_name=var_name,
        var_type=_VARIABLE_TYPES[var_name],
        op=op,
        value=resolved_val,
        path=f"{path}.value",
    )
    return node


def _is_number(val: Any) -> bool:
    return not isinstance(val, bool) and isinstance(val, (int, float))


def _validate_comparison_types(
    *,
    var_name: str,
    var_type: str,
    op: str,
    value: Any,
    path: str,
) -> None:
    if op in _SET_OPERATORS:
        if not isinstance(value, list) or len(value) == 0:
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"Operator '{op}' on '{var_name}' requires a non-empty list",
                path=path,
            )
        for item in value:
            _validate_scalar_type(var_name, var_type, "==", item, path)
        return

    if op in _ORDERING_OPERATORS and var_type not in _NUMERIC_VAR_TYPES:
        raise EMSRuleValidationError(
            "EMS_ERR_TYPE_MISMATCH",
            f"Ordering operator '{op}' is not valid for {var_type} variable '{var_name}'",
            path=path,
        )

    _validate_scalar_type(var_name, var_type, op, value, path)


def _validate_scalar_type(
    var_name: str,
    var_type: str,
    op: str,
    value: Any,
    path: str,
) -> None:
    if var_type == "optional_float":
        if value is None and op in {"==", "!="}:
            return
        if not _is_number(value):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"Variable '{var_name}' requires numeric value (or null with ==/!=), got {value!r}",
                path=path,
            )
        return

    if var_type in {"float", "int"}:
        if not _is_number(value):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"Numeric variable '{var_name}' cannot compare against {value!r}",
                path=path,
            )
        return

    if var_type == "bool":
        if not isinstance(value, bool):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"Boolean variable '{var_name}' cannot compare against {value!r}",
                path=path,
            )
        return

    if var_type == "str":
        if not isinstance(value, str):
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"String variable '{var_name}' cannot compare against {value!r}",
                path=path,
            )


def _validate_action_node(
    raw_action: Any,
    *,
    params: dict[str, Any],
    path: str,
) -> EMSRuleAction:
    if not isinstance(raw_action, dict):
        raise EMSRuleValidationError(
            "EMS_ERR_TYPE_MISMATCH",
            "Action must be a JSON object",
            path=path,
        )

    action_name = raw_action.get("action")
    if not isinstance(action_name, str) or action_name not in _ACTION_SPECS:
        raise EMSRuleValidationError(
            "EMS_ERR_UNKNOWN_ACTION",
            f"Unknown action {action_name!r}",
            path=f"{path}.action",
        )

    req_arg, intent_group = _ACTION_SPECS[action_name]
    extra_keys = set(raw_action.keys()) - {"action"} - ({req_arg} if req_arg else set())
    if extra_keys:
        raise EMSRuleValidationError(
            "EMS_ERR_INVALID_ACTION_ARG",
            f"Unexpected argument(s) {sorted(extra_keys)} for action '{action_name}'",
            path=path,
        )

    if req_arg is None:
        return EMSRuleAction(action=action_name, intent_group=intent_group)

    if req_arg not in raw_action:
        raise EMSRuleValidationError(
            "EMS_ERR_INVALID_ACTION_ARG",
            f"Action '{action_name}' requires '{req_arg}'",
            path=path,
        )

    val_src = raw_action[req_arg]
    arg_path = f"{path}.{req_arg}"
    if isinstance(val_src, bool):
        raise EMSRuleValidationError(
            "EMS_ERR_INVALID_ACTION_ARG",
            f"Argument '{req_arg}' cannot be boolean",
            path=arg_path,
        )
    if isinstance(val_src, (int, float)):
        if float(val_src) < 0.0:
            raise EMSRuleValidationError(
                "EMS_ERR_INVALID_ACTION_ARG",
                f"Argument '{req_arg}' must be >= 0, got {val_src!r}",
                path=arg_path,
            )
    elif isinstance(val_src, str):
        if val_src.startswith("$params."):
            resolved = _resolve_param_ref(val_src, params, arg_path)
            if not _is_number(resolved) or float(resolved) < 0.0:
                raise EMSRuleValidationError(
                    "EMS_ERR_INVALID_ACTION_ARG",
                    f"Parameter '{val_src}' for '{req_arg}' must be non-negative numeric",
                    path=arg_path,
                )
        elif val_src not in _VARIABLE_TYPES:
            raise EMSRuleValidationError(
                "EMS_ERR_UNKNOWN_VARIABLE",
                f"Unknown variable '{val_src}' in action argument '{req_arg}'",
                path=arg_path,
            )
        elif _VARIABLE_TYPES[val_src] not in _NUMERIC_VAR_TYPES:
            raise EMSRuleValidationError(
                "EMS_ERR_TYPE_MISMATCH",
                f"Action argument '{req_arg}' requires numeric variable, got '{val_src}'",
                path=arg_path,
            )
    else:
        raise EMSRuleValidationError(
            "EMS_ERR_INVALID_ACTION_ARG",
            f"Invalid argument type for '{req_arg}': {val_src!r}",
            path=arg_path,
        )

    return EMSRuleAction(
        action=action_name,
        arg_name=req_arg,
        value_source=val_src,
        intent_group=intent_group,
    )


class EMSRuleEvaluationResult(BaseModel):
    """Deterministic output from evaluating a rule set on one control step."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    active_rule_ids: list[str] = Field(default_factory=list)
    skipped_rule_ids: list[str] = Field(default_factory=list)
    intents: dict[str, tuple[str, float | None]] = Field(default_factory=dict)
    trace_notes: list[str] = Field(default_factory=list)


class EMSRuleInterpreter:
    """Evaluates validated pure-data EMSRuleSet against step inputs and mode."""

    @staticmethod
    def build_context(
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
    ) -> dict[str, Any]:
        """Build the exact 18-variable whitelisted evaluation context."""
        return {
            "pv_surplus_kw": step_in.pv_surplus_kw,
            "net_load_kw": step_in.net_load_kw,
            "local_load_kw": step_in.local_load_kw,
            "pv_available_kw": step_in.pv_available_kw,
            "bess_soc_fraction": step_in.bess_soc_fraction,
            "bess_max_charge_kw": step_in.bess_max_charge_kw,
            "bess_max_discharge_kw": step_in.bess_max_discharge_kw,
            "biomass_status": step_in.biomass_status.value,
            "biomass_available_kw": step_in.biomass_available_kw,
            "biomass_can_start": step_in.biomass_can_start,
            "biomass_can_stop": step_in.biomass_can_stop,
            "hour_of_day": step_in.hour_of_day,
            "month": step_in.month,
            "tariff_per_kwh": step_in.tariff_per_kwh,
            "operating_mode": operating_mode.value,
            "grid_connected": step_in.grid_connected,
            "grid_export_target_kw": step_in.grid_export_target_kw,
            "grid_export_limit_kw": step_in.grid_export_limit_kw,
        }

    def evaluate(
        self,
        rule_set: EMSRuleSet,
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
    ) -> EMSRuleEvaluationResult:
        """Evaluate matching enabled rules in (priority, declaration_index) order."""
        context = self.build_context(step_in, operating_mode)
        matching = [
            rule
            for rule in rule_set.rules
            if rule.enabled and ("*" in rule.mode_scope or operating_mode.value in rule.mode_scope)
        ]
        matching.sort(key=lambda r: (r.priority, r.declaration_index))

        active_rule_ids: list[str] = []
        skipped_rule_ids: list[str] = []
        intents: dict[str, tuple[str, float | None]] = {}
        trace_notes: list[str] = []

        for rule in matching:
            if not self._eval_condition(rule.when, context=context, params=rule_set.params):
                continue

            applied_any = False
            skipped_due_to_conflict = False
            for action in rule.then:
                if action.intent_group in intents:
                    skipped_due_to_conflict = True
                    trace_notes.append(
                        f"skipped {rule.id}.{action.action} "
                        f"(group '{action.intent_group}' already set)"
                    )
                    continue

                resolved_val: float | None = None
                if action.arg_name is not None:
                    resolved_val = self._resolve_numeric_source(
                        action.value_source,
                        context=context,
                        params=rule_set.params,
                    )
                    if resolved_val is None:
                        skipped_due_to_conflict = True
                        trace_notes.append(
                            f"skipped {rule.id}.{action.action} "
                            f"(source '{action.value_source}' is None)"
                        )
                        continue
                    resolved_val = max(0.0, resolved_val)

                intents[action.intent_group] = (action.action, resolved_val)
                applied_any = True

            if applied_any:
                active_rule_ids.append(rule.id)
            elif skipped_due_to_conflict:
                skipped_rule_ids.append(rule.id)

        return EMSRuleEvaluationResult(
            active_rule_ids=active_rule_ids,
            skipped_rule_ids=skipped_rule_ids,
            intents=intents,
            trace_notes=trace_notes,
        )

    def _eval_condition(
        self,
        node: dict[str, Any],
        *,
        context: dict[str, Any],
        params: dict[str, Any],
    ) -> bool:
        if "all" in node:
            return all(
                self._eval_condition(child, context=context, params=params)
                for child in node["all"]
            )
        if "any" in node:
            return any(
                self._eval_condition(child, context=context, params=params)
                for child in node["any"]
            )
        if "not" in node:
            return not self._eval_condition(node["not"], context=context, params=params)

        lhs = context[node["var"]]
        op = node["op"]
        raw_rhs = node["value"]
        rhs = (
            params[raw_rhs[len("$params.") :]]
            if isinstance(raw_rhs, str) and raw_rhs.startswith("$params.")
            else raw_rhs
        )

        # Null-safe comparisons for optional variables (grid_export_target_kw, grid_export_limit_kw)
        if op == "==":
            return bool(lhs == rhs)
        if op == "!=":
            return bool(lhs != rhs)
        if lhs is None:
            return False
        if op == "<":
            return bool(lhs < rhs)
        if op == "<=":
            return bool(lhs <= rhs)
        if op == ">":
            return bool(lhs > rhs)
        if op == ">=":
            return bool(lhs >= rhs)
        if op == "in":
            return bool(lhs in rhs)
        if op == "not_in":
            return bool(lhs not in rhs)
        return False

    @staticmethod
    def _resolve_numeric_source(
        source: float | int | str | None,
        *,
        context: dict[str, Any],
        params: dict[str, Any],
    ) -> float | None:
        if source is None:
            return None
        if isinstance(source, (int, float)):
            return float(source)
        if source.startswith("$params."):
            return float(params[source[len("$params.") :]])
        val = context[source]
        return None if val is None else float(val)
