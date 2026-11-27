"""Phase 7: Optional FastAPI edge service keeping local control cloud-independent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from renewablex_controller.adapters.simulated_modbus import DryRunCommandSink
from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
)
from renewablex_controller.core.controller import HybridController
from renewablex_controller.core.ems_rules import EMSRuleSet, EMSRuleValidationError
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.devices.models import CommandAuditRecord
from renewablex_controller.edge_api.storage import EdgeSQLiteStore
from renewablex_controller.simulation.harness import (
    ControllerSimulationHarness,
    SimulationRunSummary,
)
from renewablex_controller.simulation.scenarios import get_standard_scenarios


def create_edge_app(
    config: PlantOperationalConfig,
    *,
    db_path: str | Path = ":memory:",
    rule_set: EMSRuleSet | None = None,
) -> FastAPI:
    """Create a FastAPI edge supervisory API bound to a local SQLite audit store."""
    store = EdgeSQLiteStore(db_path=db_path)
    initial_rules = rule_set if rule_set is not None else store.get_active_rule_set()
    if rule_set is not None:
        store.save_rule_set(rule_set, datetime.now(UTC).isoformat())

    controller = HybridController(config, rule_set=initial_rules)
    sink = DryRunCommandSink(sink_name="edge_api_dry_run_sink")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            store.close()

    app = FastAPI(
        title="RenewableX Hybrid Controller Edge API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.controller = controller

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "plant_id": config.plant_metadata.plant_id,
            "operating_mode": app.state.controller.state.operating_mode.value,
            "hardware_commands_enabled": config.control_policy.hardware_commands_enabled,
        }

    @app.get("/state", response_model=ControllerState)
    def get_state() -> ControllerState:
        return app.state.controller.state  # type: ignore[no-any-return]

    @app.get("/config/plant", response_model=PlantOperationalConfig)
    def get_plant_config() -> PlantOperationalConfig:
        return config

    @app.get("/config/ems-rules")
    def get_ems_rules() -> dict[str, Any]:
        active: EMSRuleSet | None = app.state.controller.rule_set
        return {"active_rule_set": active.model_dump() if active is not None else None}

    @app.put("/config/ems-rules")
    def put_ems_rules(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = EMSRuleSet.from_dict(payload)
        except EMSRuleValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.message, "path": exc.path},
            ) from exc

        now_iso = datetime.now(UTC).isoformat()
        store.save_rule_set(validated, now_iso)
        current_state: ControllerState = app.state.controller.state
        app.state.controller = HybridController(
            config,
            rule_set=validated,
            initial_state=current_state,
        )
        return {"status": "updated", "active_rule_set": validated.model_dump()}

    @app.post("/control/step", response_model=ControllerStepOutput)
    def control_step(step_in: ControllerStepInput) -> ControllerStepOutput:
        ctrl: HybridController = app.state.controller
        step_out = ctrl.step(step_in)
        audit = sink.emit(step_in.step_idx, step_out, datetime.now(UTC))
        store.append(audit)
        return step_out

    @app.get("/audit/commands", response_model=list[CommandAuditRecord])
    def list_audit_commands(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[CommandAuditRecord]:
        return store.list_recent(limit=limit)

    @app.get("/scenarios")
    def list_scenarios() -> dict[str, list[str]]:
        return {"scenarios": sorted(get_standard_scenarios().keys())}

    @app.post("/scenarios/{name}/run", response_model=SimulationRunSummary)
    def run_scenario(name: str) -> SimulationRunSummary:
        scenarios = get_standard_scenarios()
        if name not in scenarios:
            raise HTTPException(status_code=404, detail=f"Unknown scenario '{name}'")
        ctrl: HybridController = app.state.controller
        harness = ControllerSimulationHarness(config, rule_set=ctrl.rule_set)
        return harness.run_scenario(scenarios[name]).summary

    return app
