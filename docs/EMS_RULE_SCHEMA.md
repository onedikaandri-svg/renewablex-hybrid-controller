# Versioned JSON EMS Rule Schema Specification (Draft v0.1)

## 1. Security & Data-Only Guarantee

Custom EMS configurations are strictly **pure data**. Python `eval`, `exec`, `compile`, `getattr` chains, and arbitrary string expression parsing are strictly forbidden. Every rule set is validated once at load time against a strict schema and fixed whitelists before any step runs, and every rule decision is checked downstream by Module 5 (`safety_supervisor.py`).

## 2. Top-Level JSON Structure

```json
{
  "ems_schema_version": "0.1",
  "name": "peak_cut_example",
  "description": "Optional human-readable summary",
  "params": {
    "soc_min_fraction": 0.20,
    "reserve_soc_fraction": 0.30,
    "soc_max_fraction": 0.95,
    "grid_export_limit_kw": 2000.0
  },
  "priority": ["pv", "bess", "biomass"],
  "fallback_mode_dispatcher": true,
  "rules": [
    {
      "id": "r_peak_cut_charge",
      "enabled": true,
      "priority": 10,
      "mode_scope": ["peak_cut", "grid_connected_normal"],
      "when": {
        "all": [
          { "var": "pv_surplus_kw", "op": ">", "value": 0.0 },
          { "var": "bess_soc_fraction", "op": "<", "value": "$params.soc_max_fraction" }
        ]
      },
      "then": [
        { "action": "charge_bess", "limit": "pv_surplus_kw" }
      ]
    }
  ]
}
```

## 3. Whitelisted Variables (18 Signals)

| Variable | Type | Definition / Source |
|---|---|---|
| `pv_surplus_kw` | `float` | `max(0.0, pv_available_kw - (local_load_kw + parasitic_load_kw))` |
| `net_load_kw` | `float` | `max(0.0, (local_load_kw + parasitic_load_kw) - pv_available_kw)` |
| `local_load_kw` | `float` | `ControllerStepInput.local_load_kw` |
| `pv_available_kw` | `float` | `ControllerStepInput.pv_available_kw` |
| `bess_soc_fraction` | `float` | `ControllerStepInput.bess_soc_fraction` |
| `bess_max_charge_kw` | `float` | `ControllerStepInput.bess_max_charge_kw` |
| `bess_max_discharge_kw` | `float` | `ControllerStepInput.bess_max_discharge_kw` |
| `biomass_status` | `str` | `"off" \| "starting" \| "running" \| "stopping" \| "outage"` |
| `biomass_available_kw` | `float` | `ControllerStepInput.biomass_available_kw` |
| `biomass_can_start` | `bool` | `ControllerStepInput.biomass_can_start` |
| `biomass_can_stop` | `bool` | `ControllerStepInput.biomass_can_stop` |
| `hour_of_day` | `int` | `0..23` |
| `month` | `int` | `1..12` |
| `tariff_per_kwh` | `float` | `ControllerStepInput.tariff_per_kwh` |
| `operating_mode` | `str` | Current FSM mode from Module 2 |
| `grid_connected` | `bool` | `ControllerStepInput.grid_connected` |
| `grid_export_target_kw` | `float \| None` | `ControllerStepInput.grid_export_target_kw` |
| `grid_export_limit_kw` | `float \| None` | `ControllerStepInput.grid_export_limit_kw` |

## 4. Operators, Combinators & Null Safety

- **Leaf comparison operators:** `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`
- **Logical combinators:** `all` (list of nodes), `any` (list of nodes), `not` (single node)
- **Max condition tree depth:** `8`
- **Parameter references:** `"$params.<key>"` validated at load time against `params`
- **Null-safe comparisons:** If an optional variable (`grid_export_target_kw` or `grid_export_limit_kw`) evaluates to `None`, ordering operators (`<`, `<=`, `>`, `>=`) return `False` without raising an exception, while `==` and `!=` compare against `None` explicitly.

## 5. Whitelisted Actions & Conflict Resolution

Value arguments (`limit`, `target`) accept a non-negative float literal, `"$params.<key>"`, or any whitelisted numeric variable name.

| Action | Required Argument | Intent Group |
|---|---|---|
| `charge_bess` | `limit` | `bess` |
| `discharge_bess` | `limit` | `bess` |
| `hold_bess` | none | `bess` |
| `set_biomass_target` | `target` | `biomass_power` |
| `start_biomass` | none | `biomass_lifecycle` |
| `stop_biomass` | none | `biomass_lifecycle` |
| `curtail_pv` | `limit` | `pv` |
| `enforce_export_limit` | `limit` | `grid_export_limit` |
| `follow_export_target` | `target` | `grid_export_target` |

**Deterministic Ordering & First-Writer-Wins Policy:**

1. Filter rules where `enabled == true` and (`operating_mode in mode_scope` or `"*" in mode_scope`).
2. Sort matching rules by `(rule.priority, declaration_index)`.
3. Evaluate `when`; if `True`, apply each action whose intent group has not yet been claimed in the current step. Conflicting lower-priority actions in an already-claimed intent group are skipped and logged in the step trace.
4. If `fallback_mode_dispatcher` is `true`, Module 3's built-in mode dispatcher closes any remaining active-power balance before passing proposed setpoints to Module 5.

## 6. Validator Error Codes

| Error Code | Condition |
|---|---|
| `EMS_ERR_UNSUPPORTED_VERSION` | `ems_schema_version` is missing or not `"0.1"` |
| `EMS_ERR_DUPLICATE_RULE_ID` | Two or more rules share the same `id` |
| `EMS_ERR_INVALID_MODE_SCOPE` | `mode_scope` is empty or contains an unknown operating mode |
| `EMS_ERR_UNKNOWN_VARIABLE` | `var` or action value source is not in the 18-variable whitelist |
| `EMS_ERR_UNKNOWN_OPERATOR` | `op` is outside the 8 whitelisted comparison operators |
| `EMS_ERR_UNKNOWN_ACTION` | `action` is outside the 9 whitelisted actions |
| `EMS_ERR_UNRESOLVED_PARAM` | `"$params.<key>"` does not exist in `params` |
| `EMS_ERR_TYPE_MISMATCH` | Operator or comparison value is incompatible with the variable type |
| `EMS_ERR_MAX_DEPTH_EXCEEDED` | Condition nesting exceeds max depth `8` |
| `EMS_ERR_INVALID_ACTION_ARG` | Missing required argument or negative numeric literal |

## 7. Step Trace Output Format

- `active_rule_ids`: ordered `list[str]` of rules that fired and applied at least one action.
- `trace_reason`: single deterministic summary string compatible with RenewableX `DispatchDecision.trace_reason`:

```text
mode=peak_cut | rules=[r_peak_cut_charge] | skipped=[] | pv=520.0kW(curt=0.0) bio=1500.0kW bess_chg=120.0kW exp=1900.0kW unmet=0.0kW | safety=ok
```
