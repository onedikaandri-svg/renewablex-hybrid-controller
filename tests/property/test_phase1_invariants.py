"""Deterministic seeded property tests for Phase 1 invariants."""

import random
from pathlib import Path

from renewablex_controller.core import (
    BiomassStatus,
    ConstraintStatus,
    ControllerStepInput,
    OperatingMode,
    PlantOperationalConfig,
    ProposedDispatch,
    SafetySupervisor,
    verify_step_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_500_randomized_steps_preserve_invariants() -> None:
    cfg = PlantOperationalConfig.from_json_file(
        REPO_ROOT / "examples" / "plants" / "reference_hybrid_plant.json"
    )
    sup = SafetySupervisor(cfg)
    rng = random.Random(20261127)

    dt_choices = [1.0, 60.0, 300.0, 900.0, 3600.0]
    mode_choices = list(OperatingMode)
    bio_status_choices = list(BiomassStatus)

    for idx in range(500):
        dt_seconds = rng.choice(dt_choices)
        mode = rng.choice(mode_choices)
        bio_status = rng.choice(bio_status_choices)
        grid_connected = mode not in {
            OperatingMode.MOMENTARY_OUTAGE,
            OperatingMode.PROLONGED_OUTAGE,
            OperatingMode.ISLANDED,
        } and rng.choice([True, True, False])

        bio_cur = (
            rng.uniform(0.0, 2000.0)
            if bio_status in {BiomassStatus.RUNNING, BiomassStatus.STOPPING}
            else 0.0
        )
        local_load = rng.uniform(0.0, 2500.0)
        parasitic = rng.uniform(0.0, 80.0)
        pv_avail = rng.uniform(0.0, 700.0)
        soc = rng.uniform(0.0, 1.0)

        step_in = ControllerStepInput(
            step_idx=idx,
            dt_seconds=dt_seconds,
            hour_of_day=idx % 24,
            month=(idx % 12) + 1,
            local_load_kw=local_load,
            parasitic_load_kw=parasitic,
            pv_available_kw=pv_avail,
            bess_soc_fraction=soc,
            bess_max_charge_kw=rng.uniform(0.0, 1200.0),
            bess_max_discharge_kw=rng.uniform(0.0, 1200.0),
            biomass_status=bio_status,
            biomass_available_kw=rng.uniform(0.0, 2500.0),
            biomass_current_power_kw=bio_cur,
            biomass_min_load_kw=rng.uniform(0.0, 800.0),
            biomass_can_start=rng.choice([True, False]),
            biomass_can_stop=rng.choice([True, False]),
            biomass_fuel_remaining_tons=rng.choice([None, 0.0, 50.0]),
            allow_grid_import=rng.choice([True, False]),
            allow_grid_export=rng.choice([True, False]),
            grid_connected=grid_connected,
            grid_export_limit_kw=rng.choice([None, 500.0, 2000.0]),
            biomass_maintenance_mode=rng.choice([False, False, True]),
            comm_healthy=rng.choice([True, True, True, False]),
            emergency_stop=rng.random() < 0.05,
        )

        # Intentionally adversarial proposal with potential simultaneous flows and out-of-range values
        proposed = ProposedDispatch(
            operating_mode=mode,
            pv_command_kw=rng.uniform(0.0, 900.0),
            bess_charge_kw=rng.uniform(0.0, 1500.0),
            bess_discharge_kw=rng.uniform(0.0, 1500.0),
            biomass_command_kw=rng.uniform(0.0, 2600.0),
            biomass_start_request=rng.choice([True, False]),
            biomass_stop_request=rng.choice([True, False]),
            grid_import_kw=rng.uniform(0.0, 3000.0),
            grid_export_kw=rng.uniform(0.0, 3000.0),
            served_load_kw=rng.uniform(0.0, 3000.0),
        )

        out = sup.supervise(step_in, proposed)

        # Mutual exclusion must always hold
        assert out.bess_charge_kw * out.bess_discharge_kw == 0.0
        assert out.grid_import_kw * out.grid_export_kw == 0.0
        # Accounting identities must always hold
        assert abs((out.pv_command_kw + out.pv_curtailment_kw) - step_in.pv_available_kw) < 1e-6
        assert abs((out.served_load_kw + out.unmet_load_kw) - step_in.local_load_kw) < 1e-6
        # PV AC limit must always hold
        assert out.pv_command_kw <= cfg.pv.rated_ac_kw + 1e-9

        # When feasible (OK or CLAMPED), closed active-power balance must be < 1e-6 kW
        if out.constraint_status in (ConstraintStatus.OK, ConstraintStatus.CLAMPED):
            report = verify_step_output(step_in, out)
            assert report.is_balanced, f"Step {idx} failed balance: {report}"
        elif out.constraint_status == ConstraintStatus.REJECTED:
            assert out.pv_command_kw == 0.0
            assert out.biomass_command_kw == 0.0
            assert out.bess_charge_kw == 0.0
            assert out.bess_discharge_kw == 0.0
            assert out.grid_import_kw == 0.0
            assert out.grid_export_kw == 0.0
            assert abs(out.balance_residual_kw + step_in.parasitic_load_kw) < 1e-9
