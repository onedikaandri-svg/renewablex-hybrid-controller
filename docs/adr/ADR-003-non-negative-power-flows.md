# ADR-003: Non-Negative Split Power Flows Inside Core

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

Different energy software systems use opposite sign conventions for battery power (some treat charging as positive, others treat discharging as positive) and grid power (export positive vs import positive). Sign mismatches are a frequent source of silent energy-balance bugs.

## Decision

Represent BESS and grid power inside `ControllerStepOutput` strictly as **non-negative split pairs**:
- `bess_charge_kw >= 0.0` and `bess_discharge_kw >= 0.0` with `bess_charge_kw * bess_discharge_kw == 0.0`
- `grid_import_kw >= 0.0` and `grid_export_kw >= 0.0` with `grid_import_kw * grid_export_kw == 0.0`

Isolate signed conversions strictly to derived properties (`p_pcc_export_kw = grid_export_kw - grid_import_kw`) and `integration/renewablex_adapter.py` (`bess_command_kw = bess_charge_kw - bess_discharge_kw`).

## Consequences

- **Positive:** Eliminates sign-inversion bugs inside dispatch, safety supervision, and closed power-balance equations.
- **Trade-off:** Requires explicit mutual-exclusion checks and two fields per bidirectional asset.
