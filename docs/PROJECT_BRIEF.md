# Project Brief: RenewableX Hybrid Controller

- **Version:** 0.2 (Sanitized Engineering Baseline)
- **Package:** `renewablex_controller`
- **License:** Apache License 2.0
- **Core Stack:** Pure-Python 3.11+ with Pydantic v2 (Phases 0 to 4)

## 1. Vision & Scope

RenewableX Hybrid Controller is an open-source supervisory Energy Management System (EMS) for hybrid renewable power plants combining Solar PV, Battery Energy Storage Systems (BESS), Biomass generation, and Utility Grid interconnection. Its architecture remains extensible for future assets (such as diesel, wind, or hydro) without rewriting the core dispatch engine.

The core logic operates identically in two roles:

1. **In-process EMS library** imported into the RenewableX calculation engine for multi-year planning studies (such as 8,760-hour simulations).
2. **Standalone local supervisory controller** running independently at a plant site with zero cloud runtime dependency.

## 2. Development Philosophy

1. **Controller-first:** Prioritize dispatch algorithms, deterministic state machines, and safety validation over dashboards or web infrastructure.
2. **Pure-Python core (Phases 0 to 4):** Keep `core/`, `integration/`, and `simulation/` free of web frameworks, databases, UI code, and field protocol libraries.
3. **Simulation-first:** Validate all control logic and edge cases in simulation before field equipment integration.
4. **Data-driven JSON EMS rules:** Support versioned, strictly validated JSON rule sets (never `eval`, `exec`, or executable user scripts) with step-by-step decision tracing.
5. **Configurable plant models:** Separate nameplate ratings from approved operating envelopes; avoid hardcoding site capacities, feeder names, or equipment tags.
6. **Hardware-independent core:** Isolate control logic from Modbus, OPC UA, IEC 60870-5-104, and MQTT adapters (Phase 5+).
7. **Safety-oriented design:** Hardware protection relays, switchgear interlocks, BMS trips, and local governors always retain override authority.

## 3. Reference Hybrid Plant Baseline (Illustrative Unapproved Values)

For Phase 0 to 4 development and validation, the repository uses a generic synthetic plant profile (`Reference Hybrid Plant`, `ref_plant_01`):

| Component | Synthetic Reference Rating | Notes |
|---|---:|---|
| Solar PV | 600.0 kWp DC / 550.0 kW AC | Inverter AC ceiling separated from DC kWp |
| BESS Energy Capacity | 2,500.0 kWh nominal / 2,250.0 kWh effective | Configurable usable window and reserve SoC |
| Biomass Generator | 2,500.0 kW nameplate / 2,000.0 kW operating max | Min load 600.0 kW; ramp rates configurable |
| Grid Interconnection | 2,500.0 kW at 20.0 kV | Configurable feeder ID (`FEEDER_01`) |

All numbers above are synthetic engineering baselines for simulation testing, not approved project defaults.

## 4. Reference Operating Scenarios

1. **Scenario A - Normal Grid-Connected Operation:** Coordinate biomass and PV output, manage BESS charge/discharge, track `grid_export_target_kw`, and preserve `reserve_soc_fraction`.
2. **Scenario B - Grid-Connected Peak-Cut Operation:** Enforce `grid_export_limit_kw` using BESS buffering, biomass coordination, and PV curtailment when required.
3. **Scenario C - Prolonged Blackout / Islanded Operation:** Transition to an isolated feeder (`FEEDER_01`), balance generation against `local_load_kw`, manage BESS stability reserve, and explicitly record any `unmet_load_kw`.
4. **Scenario D - Biomass Maintenance Operation:** Operate with `biomass_maintenance_mode = true`, coordinating PV and BESS while reporting supply shortfalls (`unmet_load_kw`).
5. **Scenario E - Grid Restoration & Synchronization:** Monitor grid restoration, check `sync_permissive` and operator acknowledgment, execute simulated reconnection sequences, and return to grid-connected dispatch.
6. **Exceptional Events:** Momentary outages below duration threshold, emergency stops, and telemetry communication failures.

## 5. Explicit Non-Goals (Phases 0 to 4)

- No standalone frontend dashboard or web UI (belongs in the RenewableX Next.js platform).
- No duplication of detailed solar geometry/transposition, cell temperature, battery degradation, biomass LHV thermodynamics, or financial NPV/IRR/LCOE calculations (belongs in RenewableX).
- No diesel dispatch before PV, BESS, Biomass, and Grid are validated.
- No direct physical breaker closure, black-start automation, or replacement of certified industrial protection relays.

## 6. Modules & Phased Roadmap

- **Module 1 (`plant_model.py`):** Validated plant operational limits and policy parameters.
- **Module 2 (`mode_manager.py`):** Deterministic 8-state Operating Mode FSM.
- **Module 3 (`dispatch_engine.py`, `ems_rules.py`):** Residual dispatchers and pure-data JSON EMS interpreter.
- **Module 4 (`bess_manager.py`):** BESS power/energy headroom and mode-aware SoC reserve policy.
- **Module 5 (`safety_supervisor.py`, `power_balance.py`):** Independent constraint clamping/rejection and `< 1e-6 kW` closed power-balance verification.
- **Module 6 (`sequence_manager.py`):** Simulated transition sequences for islanding, maintenance, and restoration.
- **Module 7 (`devices/`, `adapters/` - Phase 5+):** Protocol-independent device layer and read-only adapters first.
- **Module 8 (`simulation/` - Phase 4):** Lightweight scenario runner and JSON/CSV trace exporter.

## 7. Key Performance Indicators (KPIs)

1. Closed active-power balance residual `< 1e-6 kW` on every step.
2. Zero mutual-exclusion violations (`bess_charge_kw * bess_discharge_kw == 0`, `grid_import_kw * grid_export_kw == 0`).
3. Strict physical SoC and mode-dependent operational reserve compliance.
4. 100% deterministic, reproducible dispatch and FSM transitions across arbitrary `dt_seconds`.
5. Explicit validation rejection of malformed JSON EMS rules and unsafe setpoints.
6. Complete step-level decision traceability (`active_rule_ids`, `trace_reason`).
7. Seamless 1-to-1 adapter compatibility with RenewableX `DispatchContext` and `DispatchDecision`.
