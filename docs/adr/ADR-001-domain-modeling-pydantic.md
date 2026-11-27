# ADR-001: Domain Modeling and Validation with Pydantic v2

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

`renewablex_controller.core` must validate complex operational boundaries (`PlantOperationalConfig`), step inputs (`ControllerStepInput`), step outputs (`ControllerStepOutput`), and versioned JSON EMS rule files with strict type safety, range checks, cross-field invariants, and JSON round-trip serialization, while keeping Phases 0 to 4 free of heavy frameworks.

## Decision

Use **Pydantic v2** (`pydantic>=2.6,<3.0`) as the sole third-party runtime dependency of `renewablex_controller.core`. Models at configuration and step boundaries use strict validation (`extra="forbid"` where appropriate), while inner dispatch arithmetic operates on plain Python floats.

## Consequences

- **Positive:** Immediate schema validation, clear field-level error reporting, automatic JSON serialization, and direct alignment with the RenewableX Python stack.
- **Trade-off:** Adds one external dependency and minor object construction overhead per step compared to stdlib `dataclasses`, mitigated by validating plant configs and EMS rules once at load time.
