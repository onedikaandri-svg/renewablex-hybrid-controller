# RenewableX Integration Contract (Draft v0.1)

This contract defines the data schemas, units, status values, sign conventions, and active-power balance invariants shared between `renewablex_controller` and the RenewableX calculation engine.

## 1. Shared Unit, Status, and Sign Standards

1. **Internal Units:**
   - Active power: `kW`
   - Energy: `kWh`
   - Step duration: `dt_seconds` (`> 0.0`, derived `step_hours = dt_seconds / 3600.0`, zero hardcoded 8,760 or 1-hour assumptions)
   - Ratios (SoC, efficiency, load fraction): `0.0` to `1.0`
   - Fuel mass: `tons`
2. **Standard Biomass Statuses (`BiomassStatus`):**
   - `"off"`, `"starting"`, `"running"`, `"stopping"`, `"outage"`
3. **Non-Negative Split Power Flows:**
   - `bess_charge_kw >= 0.0` and `bess_discharge_kw >= 0.0` (mutually exclusive: `bess_charge_kw * bess_discharge_kw == 0.0`)
   - `grid_import_kw >= 0.0` and `grid_export_kw >= 0.0` (mutually exclusive: `grid_import_kw * grid_export_kw == 0.0`)
4. **Exact Adapter Translations (`integration/renewablex_adapter.py`):**
   - Derived SCADA PCC net export: `p_pcc_export_kw = out.grid_export_kw - out.grid_import_kw`
   - RenewableX `DispatchDecision.bess_command_kw` (positive = charge, negative = discharge): `bess_command_kw = out.bess_charge_kw - out.bess_discharge_kw`

## 2. Closed Active-Power Balance Invariants

Every valid `ControllerStepOutput` must satisfy all of the following equations simultaneously (`|residual_kw| < 1e-6`):

```text
pv_command_kw + pv_curtailment_kw = pv_available_kw

pv_available_kw + biomass_command_kw + bess_discharge_kw + grid_import_kw
  = served_load_kw + parasitic_load_kw + bess_charge_kw + grid_export_kw + pv_curtailment_kw

pv_command_kw + biomass_command_kw + bess_discharge_kw + grid_import_kw
  = served_load_kw + parasitic_load_kw + bess_charge_kw + grid_export_kw

served_load_kw + unmet_load_kw = local_load_kw
bess_charge_kw * bess_discharge_kw == 0.0
grid_import_kw * grid_export_kw == 0.0
```

## 3. `PlantOperationalConfig` (Module 1)

| Sub-model | Field | Type | Unit / Constraint | Notes |
|---|---|---|---|---|
| `PlantMetadataConfig` | `plant_id` | `str` | non-empty | Unique config ID |
| | `plant_name` | `str` | non-empty | Human-readable label |
| | `feeder_id` | `str \| None` | optional | Configurable distribution feeder ID |
| | `nominal_voltage_kv` | `float \| None` | `> 0` kV | Metadata only |
| `PVOperationalConfig` | `nameplate_kwp` | `float` | `>= 0` kWp | DC array rating |
| | `rated_ac_kw` | `float` | `>= 0` kW | AC inverter export ceiling |
| | `curtailment_enabled` | `bool` | default `True` | Supervisory curtailment allowed |
| | `available` | `bool` | default `True` | PV plant availability |
| `BiomassOperationalConfig` | `nameplate_kw` | `float` | `>= 0` kW | Equipment nameplate rating |
| | `operating_max_kw` | `float` | `>= 0`, `<= nameplate_kw` | Approved operating ceiling |
| | `min_load_kw` | `float` | `>= 0`, `<= operating_max_kw` | Minimum stable generation |
| | `ramp_up_kw_per_hour` | `float` | `>= 0` kW/h | Scaled by `dt_seconds / 3600` |
| | `ramp_down_kw_per_hour` | `float` | `>= 0` kW/h | Scaled by `dt_seconds / 3600` |
| | `startup_time_seconds` | `float` | `>= 0` s | Startup duration |
| | `shutdown_time_seconds` | `float` | `>= 0` s | Shutdown duration |
| | `min_up_time_seconds` | `float` | `>= 0` s | Minimum run duration |
| | `min_down_time_seconds` | `float` | `>= 0` s | Minimum off duration |
| | `available` | `bool` | default `True` | Biomass plant availability |
| `BESSOperationalConfig` | `nominal_capacity_kwh` | `float` | `>= 0` kWh | Nameplate energy capacity |
| | `effective_capacity_kwh` | `float` | `>= 0`, `<= nominal_capacity_kwh` | Usable energy capacity |
| | `max_charge_kw` | `float` | `>= 0` kW | PCS charge power ceiling |
| | `max_discharge_kw` | `float` | `>= 0` kW | PCS discharge power ceiling |
| | `soc_min_fraction` | `float` | `0.0..1.0` | Hard lower physical SoC bound |
| | `reserve_soc_fraction` | `float` | `soc_min..soc_max` | Islanding operational reserve |
| | `soc_max_fraction` | `float` | `0.0..1.0` | Hard upper physical SoC bound |
| | `charge_efficiency` | `float` | `(0.0, 1.0]` | Lookahead step energy bound |
| | `discharge_efficiency` | `float` | `(0.0, 1.0]` | Lookahead step energy bound |
| | `allow_reserve_drawdown_in_islanded` | `bool` | default `True` | Reserve policy toggle |
| | `available` | `bool` | default `True` | BESS/PCS availability |
| `GridOperationalConfig` | `interconnection_limit_kw` | `float` | `>= 0` kW | PCC thermal/contract ceiling |
| | `default_allow_import` | `bool` | default `True` | Default grid import permission |
| | `default_allow_export` | `bool` | default `True` | Default grid export permission |
| | `default_export_limit_kw` | `float \| None` | `>= 0` kW or `None` | Optional static export cap |
| `ControlPolicyConfig` | `default_dt_seconds` | `float` | `> 0`, default `3600.0` | Default step size |
| | `momentary_outage_threshold_seconds` | `float` | `>= 0`, default `60.0` | Outage classification threshold |
| | `max_telemetry_age_seconds` | `float` | `> 0`, default `30.0` | Stale telemetry threshold |
| | `power_balance_tolerance_kw` | `float` | `> 0`, default `1e-6` | Balance residual threshold |
| | `require_operator_ack_for_restoration` | `bool` | default `True` | Restoration permissive gate |
| | `require_operator_ack_for_fault_reset` | `bool` | default `True` | Fault reset gate |
| | `comm_loss_action` | `Literal["hold_safe", "emergency_fault"]` | default `"emergency_fault"` | Telemetry loss behavior |
| | `hardware_commands_enabled` | `bool` | default `False` | Must be `False` in Phases 0 to 4 |

## 4. `ControllerStepInput`

| Field | Type | Unit / Values | Mapping from RenewableX `DispatchContext` |
|---|---|---|---|
| `step_idx` | `int` | `>= 0` | `ctx.step_idx` |
| `dt_seconds` | `float` | `> 0` s | `time_config.step_minutes * 60.0` |
| `hour_of_day` | `int` | `0..23` | `ctx.hour_of_day` |
| `month` | `int` | `1..12` | `ctx.month` |
| `local_load_kw` | `float` | `>= 0` kW | `ctx.load_kw` |
| `parasitic_load_kw` | `float` | `>= 0` kW | `ctx.parasitic_load_kw` |
| `pv_available_kw` | `float` | `>= 0` kW | `ctx.pv_available_kw` |
| `bess_soc_fraction` | `float` | `0.0..1.0` | `ctx.bess_soc` |
| `bess_max_charge_kw` | `float` | `>= 0` kW | `ctx.bess_max_charge_kw` |
| `bess_max_discharge_kw` | `float` | `>= 0` kW | `ctx.bess_max_discharge_kw` |
| `biomass_status` | `BiomassStatus` | 5 standard statuses | `ctx.biomass_status` |
| `biomass_available_kw` | `float` | `>= 0` kW | `ctx.biomass_available_kw` |
| `biomass_current_power_kw` | `float` | `>= 0` kW | `ctx.biomass_current_power_kw` |
| `biomass_min_load_kw` | `float` | `>= 0` kW | `ctx.biomass_min_load_kw` |
| `biomass_can_start` | `bool` | boolean | `ctx.biomass_can_start` |
| `biomass_can_stop` | `bool` | boolean | `ctx.biomass_can_stop` |
| `biomass_fuel_remaining_tons` | `float \| None` | tons or `None` | `ctx.biomass_fuel_remaining_tons` |
| `allow_grid_import` | `bool` | boolean | `ctx.allow_grid_import` |
| `allow_grid_export` | `bool` | boolean | `ctx.allow_grid_export` |
| `tariff_per_kwh` | `float` | currency/kWh | `ctx.tariff_per_kwh` |
| `grid_connected` | `bool` | default `True` | Optional extension |
| `grid_outage_duration_seconds` | `float` | `>= 0`, default `0.0` | Optional outage scenario input |
| `grid_export_target_kw` | `float \| None` | `>= 0` kW or `None` | Optional External SCADA target |
| `grid_export_limit_kw` | `float \| None` | `>= 0` kW or `None` | Optional peak-cut export ceiling |
| `biomass_maintenance_mode` | `bool` | default `False` | Optional maintenance flag |
| `sync_permissive` | `bool` | default `False` | Optional restoration permissive |
| `comm_healthy` | `bool` | default `True` | Optional telemetry health flag |
| `emergency_stop` | `bool` | default `False` | Optional emergency stop flag |
| `telemetry_age_seconds` | `float` | `>= 0`, default `0.0` | Checked vs `max_telemetry_age_seconds` |
| `operator_ack_restoration` | `bool` | default `False` | Optional operator ack |
| `operator_ack_fault_reset` | `bool` | default `False` | Optional operator ack |

## 5. `ControllerStepOutput`

| Field | Type | Mapping to RenewableX `DispatchDecision` |
|---|---|---|
| `operating_mode` | `OperatingMode` | Stored in extended mode/trace log |
| `sequence_state` | `str \| None` | Stored in extended sequence log |
| `pv_command_kw` | `float` (`>= 0`) | `pv_available_kw - pv_curtailment_kw` |
| `pv_curtailment_kw` | `float` (`>= 0`) | `curtailment_kw` |
| `bess_charge_kw` | `float` (`>= 0`) | `bess_command_kw = bess_charge_kw - bess_discharge_kw` |
| `bess_discharge_kw` | `float` (`>= 0`) | `bess_command_kw = bess_charge_kw - bess_discharge_kw` |
| `biomass_command_kw` | `float` (`>= 0`) | `biomass_command_kw` |
| `biomass_start_request` | `bool` | `biomass_start_request` |
| `biomass_stop_request` | `bool` | `biomass_stop_request` |
| `grid_import_kw` | `float` (`>= 0`) | `grid_import_kw` |
| `grid_export_kw` | `float` (`>= 0`) | `grid_export_kw` |
| `served_load_kw` | `float` (`>= 0`) | Validated against simulator power balance |
| `unmet_load_kw` | `float` (`>= 0`) | Validated against simulator power balance |
| `constraint_status` | `"ok" \| "clamped" \| "infeasible" \| "rejected"` | Logged / checked in validation tests |
| `violations` | `list[str]` | Logged / surfaced in diagnostics |
| `active_rule_ids` | `list[str]` | Included in trace inspection |
| `trace_reason` | `str` | `trace_reason` |
| `balance_residual_kw` | `float` | Must satisfy `abs(residual) < 1e-6` |

Derived property: `p_pcc_export_kw = grid_export_kw - grid_import_kw`.

## 6. `ControllerState` (Inter-Step Memory)

- `operating_mode: OperatingMode` (default `"grid_connected_normal"`)
- `previous_grid_mode: OperatingMode` (default `"grid_connected_normal"`)
- `outage_elapsed_seconds: float = 0.0`
- `active_sequence: str | None = None`
- `sequence_step: int = 0`
- `sequence_elapsed_seconds: float = 0.0`
- `last_biomass_command_kw: float | None = None`
- `biomass_state_elapsed_seconds: float = 0.0`
- `event_log: list[ModeTransitionEvent]`
