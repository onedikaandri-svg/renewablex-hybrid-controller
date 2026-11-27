# ADR-006: Configurable Communication Loss and Stale Telemetry Fail-Safe Policy

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

In field supervisory deployments, SCADA or equipment telemetry can drop (`comm_healthy == False`) or become stale (`telemetry_age_seconds > max_telemetry_age_seconds`). Different projects and utilities require either immediate transition to `emergency_fault` or holding a bounded safe state for a grace period while local PLCs/relays manage fast protection.

## Decision

Make the fail-safe behavior explicitly configurable in `ControlPolicyConfig` via `max_telemetry_age_seconds` and `comm_loss_action: Literal["hold_safe", "emergency_fault"]` (defaulting to `"emergency_fault"`).

## Consequences

- **Positive:** Avoids hardcoding an unapproved site safety policy while ensuring stale or missing telemetry is never treated as valid live data.
- **Trade-off:** Requires site engineering sign-off during Phase 5/6 commissioning to select the approved `comm_loss_action` and timeout values.
