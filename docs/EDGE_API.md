# Optional Edge API Specification (Phase 7)

The `edge_api` package (`pip install "renewablex-hybrid-controller[edge]"`) exposes a local FastAPI supervisory service backed by SQLite (`EdgeSQLiteStore`). It operates 100% locally without requiring cloud connectivity, while letting the RenewableX platform synchronize versioned JSON EMS rules and inspect command audit logs.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service status, `plant_id`, active `operating_mode`, and `hardware_commands_enabled` (`false`) |
| `GET` | `/state` | Full serialized `ControllerState` (mode, sequence step, event log) |
| `GET` | `/config/plant` | Active validated `PlantOperationalConfig` |
| `GET` | `/config/ems-rules` | Currently active `EMSRuleSet` (or `null`) |
| `PUT` | `/config/ems-rules` | Validate and persist a v0.1 JSON EMS rule set (returns HTTP 422 with `EMS_ERR_*` on failure) |
| `POST` | `/control/step` | Execute one `HybridController.step()` and append a `CommandAuditRecord` to SQLite |
| `GET` | `/audit/commands?limit=50` | Retrieve recent `CommandAuditRecord` entries |
| `GET` | `/scenarios` | List standard Phase 4 validation scenarios |
| `POST` | `/scenarios/{name}/run` | Run a standard scenario using active EMS rules and return `SimulationRunSummary` |
