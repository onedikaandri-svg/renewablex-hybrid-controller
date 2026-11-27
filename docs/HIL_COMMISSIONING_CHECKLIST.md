# HIL Validation and Pre-Commissioning Checklist (Phase 6)

Physical equipment command activation must not proceed until every item below is verified and signed off by the responsible engineering parties.

## 1. Software Boundary & Safety Lockouts

- [ ] `core/` passes architectural boundary checks (zero protocol, web, or database imports).
- [ ] `control_policy.hardware_commands_enabled` stays `false` until formal commissioning authorization.
- [ ] All `HILValidationSuite` checks (`sub_minute_ramp_compliance`, `modbus_comm_loss_failsafe`, `stale_telemetry_rejection`, `emergency_stop_override`, `dry_run_write_lock`) pass against site parameters.
- [ ] Closed active-power balance residual remains `< 1e-6 kW` across all nominal and clamped steps.

## 2. Equipment & Protection Independence

- [ ] Protection relays (50/51, 27/59, 81O/U, 25 synchrocheck) operate independently of supervisory software.
- [ ] PCC Breaker open/close authority and hardware interlocks are verified with utility and EPC teams.
- [ ] Biomass governor/PLC enforces minimum stable load, ramp limits, and cooldown timers locally.
- [ ] BESS BMS hardware safety trips and PCS current/voltage limits override supervisory setpoints.
- [ ] PV inverter anti-islanding and AC ceiling curtailment interfaces are bench-tested.

## 3. Communication & Fail-Safe Verification

- [ ] Approved register maps, scaling factors, and byte orders are validated against vendor manuals.
- [ ] Scan rate, timestamp synchronization, and `max_telemetry_age_seconds` are calibrated on site.
- [ ] `comm_loss_action` (`emergency_fault` or `hold_safe`) is formally signed off by safety lead.
- [ ] Dry-run shadow operation (`write_enabled = false`) completes without unexpected rejections or faults.
