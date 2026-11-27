# ADR-002: Explicit Serializable ControllerState

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

Supervisory control requires inter-step memory across time steps (`dt_seconds`): current operating mode, outage elapsed timer, active transition sequence step, last biomass setpoint for ramp-rate checks, minimum up/down timers, and transition event logs.

## Decision

Represent all inter-step memory in an explicit, serializable `ControllerState` model passed into and returned from pure core step functions: `(state_in, controller_state) -> (state_out, next_controller_state)`. Provide a thin stateful `HybridController` facade for convenience.

## Consequences

- **Positive:** 100% deterministic replay, safe parallel execution during RenewableX multi-year simulation sweeps, trivial unit testing of specific FSM states, and crash-safe state persistence in future edge deployments.
- **Trade-off:** Slightly more explicit state plumbing in internal module signatures.
