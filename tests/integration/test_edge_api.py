"""Integration tests for Phase 7 FastAPI Edge API and SQLite persistence."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from renewablex_controller.core import OperatingMode, PlantOperationalConfig
from renewablex_controller.edge_api import create_edge_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cfg() -> PlantOperationalConfig:
    return PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )


def test_edge_api_lifecycle_validation_audit_and_sqlite_reload(tmp_path: Path) -> None:
    cfg = _load_cfg()
    db_path = tmp_path / "edge_test.sqlite3"
    app = create_edge_app(cfg, db_path=db_path)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["hardware_commands_enabled"] is False
        assert health.json()["plant_id"] == "ref_plant_01"

        plant_resp = client.get("/config/plant")
        assert plant_resp.status_code == 200
        assert plant_resp.json()["plant_metadata"]["plant_id"] == "ref_plant_01"

        # Invalid EMS rule set returns HTTP 422 with structured EMS_ERR_* code
        bad_rules = client.put(
            "/config/ems-rules",
            json={"ems_schema_version": "9.9", "name": "bad", "rules": []},
        )
        assert bad_rules.status_code == 422
        assert bad_rules.json()["detail"]["code"] == "EMS_ERR_UNSUPPORTED_VERSION"

        # Valid EMS rule set updates controller and persists in SQLite
        peak_rules = json.loads(
            (REPO_ROOT / "examples" / "ems_rules" / "peak_cut_example.json").read_text(
                encoding="utf-8"
            )
        )
        put_ok = client.put("/config/ems-rules", json=peak_rules)
        assert put_ok.status_code == 200
        assert put_ok.json()["status"] == "updated"

        # Execute one control step
        step_resp = client.post(
            "/control/step",
            json={
                "step_idx": 0,
                "dt_seconds": 900.0,
                "hour_of_day": 12,
                "month": 6,
                "local_load_kw": 300.0,
                "parasitic_load_kw": 40.0,
                "pv_available_kw": 550.0,
                "bess_soc_fraction": 0.50,
                "bess_max_charge_kw": 1000.0,
                "bess_max_discharge_kw": 1000.0,
                "biomass_status": "running",
                "biomass_available_kw": 2000.0,
                "biomass_current_power_kw": 1500.0,
                "biomass_min_load_kw": 600.0,
                "biomass_can_start": False,
                "biomass_can_stop": True,
                "allow_grid_import": True,
                "allow_grid_export": True,
                "grid_export_limit_kw": 2000.0,
            },
        )
        assert step_resp.status_code == 200
        body = step_resp.json()
        assert body["operating_mode"] == OperatingMode.PEAK_CUT.value
        assert body["grid_export_kw"] <= 2000.0 + 1e-6

        state_resp = client.get("/state")
        assert state_resp.status_code == 200
        assert state_resp.json()["operating_mode"] == OperatingMode.PEAK_CUT.value

        audit_resp = client.get("/audit/commands?limit=10")
        assert audit_resp.status_code == 200
        audits = audit_resp.json()
        assert len(audits) == 1
        assert audits[0]["write_enabled"] is False
        assert audits[0]["execution_status"] == "dry_run_only"

        # Scenarios endpoint
        sc_list = client.get("/scenarios")
        assert sc_list.status_code == 200
        assert "peak_cut_export_limit" in sc_list.json()["scenarios"]

        sc_run = client.post("/scenarios/peak_cut_export_limit/run")
        assert sc_run.status_code == 200
        assert sc_run.json()["all_invariants_passed"] is True

        sc_404 = client.post("/scenarios/unknown_scenario/run")
        assert sc_404.status_code == 404

    # Re-create app against the same SQLite DB -> active rules and audit logs persist
    restarted_app = create_edge_app(cfg, db_path=db_path)
    with TestClient(restarted_app) as restarted_client:
        rules_after = restarted_client.get("/config/ems-rules")
        assert rules_after.status_code == 200
        assert rules_after.json()["active_rule_set"]["name"] == "peak_cut_example"

        audits_after = restarted_client.get("/audit/commands")
        assert audits_after.status_code == 200
        assert len(audits_after.json()) == 1
