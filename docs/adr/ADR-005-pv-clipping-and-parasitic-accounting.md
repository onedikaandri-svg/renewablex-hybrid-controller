# ADR-005: Unified PV Curtailment Accounting and Parasitic Load Handling

- **Status:** Accepted
- **Date:** 2026-11-27

## Context

In hybrid plants, actual PV output (`pv_command_kw`) can fall below theoretical available PV (`pv_available_kw`) due to either inverter AC clipping (`pv_available_kw > rated_ac_kw`) or intentional supervisory curtailment (such as peak-cut or islanded excess). Additionally, off-grid periods may occur where total available generation and BESS discharge cannot cover `parasitic_load_kw`.

## Decision

1. Define `pv_curtailment_kw = pv_available_kw - pv_command_kw` (covering both AC ceiling clipping and supervisory curtailment) so the gross and net closed active-power balance equations remain exact without double counting.
2. Treat `parasitic_load_kw` as mandatory plant auxiliary demand. In off-grid scenarios where total supply cannot even cover `parasitic_load_kw`, Module 5 sets `served_load_kw = 0.0`, `unmet_load_kw = local_load_kw`, flags `constraint_status = "infeasible"`, and records `STATION_AUX_DEFICIT` rather than silently dropping auxiliary load.

## Consequences

- **Positive:** Guarantees `< 1e-6 kW` active-power balance across both gross and net PV equations and surfaces station auxiliary deficits explicitly.
- **Trade-off:** Separating AC clipping vs supervisory curtailment in reporting later requires comparing `pv_available_kw` against `rated_ac_kw`, which can be added as a diagnostic field without changing the balance equation.
