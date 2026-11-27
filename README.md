# RenewableX Hybrid Controller

Configurable pure-Python supervisory Energy Management System (EMS) and power dispatch controller for hybrid renewable energy plants (`renewablex_controller`).

> **Safety Notice:** Phases 0 to 4 are simulation-only. Hardware command execution is disabled by default (`hardware_commands_enabled = false`). This software does not replace certified industrial protection relays, switchgear interlocks, BMS trips, or local equipment governors.

## Overview

`renewablex_controller` calculates and coordinates active-power dispatch across:

- Solar Photovoltaic (PV)
- Battery Energy Storage Systems (BESS)
- Biomass Power Plants
- Utility Grid / Distribution Networks

The core engine (`renewablex_controller.core`) is designed to run identically in two deployment modes:

1. **In-process EMS library** imported into the RenewableX calculation engine for multi-year planning studies via `integration/renewablex_adapter.py`.
2. **Standalone edge supervisory controller** deployed locally at a hybrid plant without cloud runtime dependencies (device adapters and optional edge API added in Phases 5 to 7).

## Design Principles

- **Pure-Python core (Phases 0 to 4):** Depends only on Python 3.11+ and Pydantic v2. Zero web server, UI, database, or protocol library dependencies.
- **Data-driven JSON EMS rules:** Versioned JSON dispatch rule sets with strict schema validation and whitelisted variables, operators, and actions (no `eval` or `exec`).
- **Closed active-power balance:** Every control step enforces `< 1e-6 kW` active-power residual and strict mutual exclusion on BESS charge/discharge and grid import/export.
- **Independent safety supervision:** Module 5 validates and clamps or rejects proposed dispatch commands regardless of whether they came from built-in mode dispatchers or custom JSON rules.

## Repository Structure

```text
renewablex-hybrid-controller/
├── .github/workflows/ci.yml
├── pyproject.toml
├── README.md
├── docs/
│   ├── PROJECT_BRIEF.md
│   ├── ARCHITECTURE.md
│   ├── INTEGRATION_CONTRACT.md
│   ├── EMS_RULE_SCHEMA.md
│   ├── OPEN_ISSUES.md
│   ├── ROADMAP_ISSUES.md
│   └── adr/
├── examples/
│   ├── plants/reference_hybrid_plant.json
│   └── ems_rules/
├── src/renewablex_controller/
│   ├── core/
│   ├── integration/
│   └── simulation/
└── tests/
```

## Development Roadmap

- **Phase 0:** Project foundation, architecture, integration contract, JSON EMS schema, ADRs, and open-issues register.
- **Phase 1:** Operational plant model (`PlantOperationalConfig`), step contracts, closed power balance verifier, and baseline safety supervisor.
- **Phase 2:** BESS supervisory manager, mode dispatch engine, JSON EMS rule interpreter, and RenewableX adapter.
- **Phase 3:** Deterministic 8-mode Operating Mode FSM, sequence manager, and full safety supervisor.
- **Phase 4:** Lightweight scenario validation harness and JSON/CSV trace reporters.
- **Phase 5 to 7:** Read-only protocol adapters (`devices/`, `adapters/`), HIL/MPC evaluation, and optional edge sync API (`edge_api/`).

## Local Development

Install editable package and run tests:

```bash
pip install -e ".[dev]"
pytest -v
```
