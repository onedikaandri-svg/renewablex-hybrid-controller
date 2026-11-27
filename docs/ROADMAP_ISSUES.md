# Phase 0 and Phase 1 Engineering Backlog

## Phase 0: Project Foundation

### Issue P0-01: Initialize Packaging, Repository Layout, and CI
- **Labels:** `phase-0`, `infrastructure`
- **Scope:** Create `pyproject.toml` (`renewablex_controller`), `src/renewablex_controller/` package layout, `pytest`/`ruff`/`mypy` configuration, and `.github/workflows/ci.yml`.
- **Acceptance Criteria:** Editable install (`pip install -e ".[dev]"`) works on Python 3.11+, and `pytest`, `ruff check`, and `mypy` execute cleanly.

### Issue P0-02: Author Architecture, Integration Contract, and EMS Rule Schema Docs
- **Labels:** `phase-0`, `documentation`
- **Scope:** Author `README.md`, `docs/PROJECT_BRIEF.md`, `docs/ARCHITECTURE.md`, `docs/INTEGRATION_CONTRACT.md`, and `docs/EMS_RULE_SCHEMA.md`.
- **Acceptance Criteria:** Dual deployment modes, 6-module pipeline, 8-state FSM, Section 5.2 fields, and Section 5.3 whitelists are fully documented and sanitized.

### Issue P0-03: Establish ADRs (001 to 006) and Engineering Open-Issues Register
- **Labels:** `phase-0`, `documentation`, `engineering`
- **Scope:** Create `docs/OPEN_ISSUES.md` (`OI-001` through `OI-014`) and `docs/adr/ADR-001` through `ADR-006`.
- **Acceptance Criteria:** Every open engineering item is mapped to a configurable parameter or interface boundary without silent assumptions.

### Issue P0-04: Add Reference Plant Config and Example JSON EMS Rules
- **Labels:** `phase-0`, `examples`
- **Scope:** Create `examples/plants/reference_hybrid_plant.json`, `examples/ems_rules/peak_cut_example.json`, and `examples/ems_rules/islanded_reserve_example.json`.
- **Acceptance Criteria:** Example JSON files parse cleanly, keep `hardware_commands_enabled = false`, and use neutral synthetic ratings.

---

## Phase 1: Operational Plant Model, Contracts & Closed Power Balance

### Issue P1-01: Implement Unit Constants, Enums, and `PlantOperationalConfig` (Module 1)
- **Labels:** `phase-1`, `core`
- **Scope:** Implement `core/units.py` and `core/plant_model.py` enforcing `kW`, `kWh`, `dt_seconds`, `0.0..1.0` fractions, `soc_min <= reserve_soc <= soc_max`, `operating_max_kw <= nameplate_kw`, and `hardware_commands_enabled == False`.
- **Acceptance Criteria:** Valid configs load cleanly; out-of-range or inconsistent bounds raise explicit validation errors.

### Issue P1-02: Implement `ControllerStepInput`, `ControllerStepOutput`, and `ControllerState`
- **Labels:** `phase-1`, `core`, `integration`
- **Scope:** Implement `core/contracts.py` with all RenewableX contract fields, 5 standard biomass statuses, non-negative flow validations, `p_pcc_export_kw` derived property, and JSON serialization.
- **Acceptance Criteria:** Negative flow values or invalid statuses are rejected; JSON round-trip succeeds deterministically.

### Issue P1-03: Implement Closed Active-Power Balance & Mutual-Exclusion Verifier
- **Labels:** `phase-1`, `core`, `safety`
- **Scope:** Implement `core/power_balance.py` verifying gross/net PV identity, closed AC-bus balance (`< 1e-6 kW`), demand accounting, and mutual-exclusion products (`== 0.0`).
- **Acceptance Criteria:** Unit and Hypothesis property tests confirm accurate residual and invariant checking across arbitrary positive flows and `dt_seconds` values.

### Issue P1-04: Implement Baseline Safety Supervisor (Module 5)
- **Labels:** `phase-1`, `core`, `safety`
- **Scope:** Implement `core/safety_supervisor.py` enforcing PV AC limit, biomass min/max/ramp limits scaled by `dt_seconds`, BESS power and step-energy SoC bounds, grid import/export ceilings, mutual exclusion, safe closed-balance reconciliation, and `constraint_status`.
- **Acceptance Criteria:** Unsafe or infeasible allocations are clamped or rejected with explicit violation codes while preserving `< 1e-6 kW` balance residual when feasible.

### Issue P1-05: Build Phase 1 Unit & Property-Based Test Suite
- **Labels:** `phase-1`, `testing`
- **Scope:** Add `tests/unit/` and `tests/property/` covering Module 1, contracts, power balance, and baseline Module 5.
- **Acceptance Criteria:** 100% test pass rate and high branch coverage across `core/`.
