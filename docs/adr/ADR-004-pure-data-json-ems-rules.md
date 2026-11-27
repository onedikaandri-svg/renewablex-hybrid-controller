# ADR-004: Pure-Data Versioned JSON EMS Rules Without Code Execution

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

RenewableX Stage 3B requires user-configurable EMS dispatch strategies stored as JSON configurations and executed inside both simulation sweeps and local edge controllers.

## Decision

Implement EMS rules as a strictly **pure-data JSON DSL** (`ems_schema_version: "0.1"`):
- Forbid `eval`, `exec`, `compile`, `getattr` lookups, and arbitrary string expression parsing.
- Restrict conditions and actions to fixed whitelists (18 variables, 8 comparison operators + `all`/`any`/`not` up to depth 8, 9 actions).
- Sort rules deterministically by `(priority, declaration_index)` and resolve conflicts via first-writer-wins per intent group.
- Route all rule outputs through Module 5 (`safety_supervisor.py`).

## Consequences

- **Positive:** Safe to accept user-authored JSON configs, fully auditable via `active_rule_ids` and `trace_reason`, and guaranteed not to bypass safety limits.
- **Trade-off:** Less expressive than embedded Python scripts; complex mathematical optimization (MPC/MILP) will live in dedicated Phase 6 dispatchers rather than inside the rule DSL.
