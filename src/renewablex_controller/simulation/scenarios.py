"""Standardized neutral hybrid-plant validation scenarios (Phase 4)."""

from __future__ import annotations

from renewablex_controller.core.units import BiomassStatus
from renewablex_controller.simulation.harness import ScenarioDefinition, ScenarioStepProfile

_PV_24H_KW = [
    0.0, 0.0, 0.0, 0.0, 0.0, 20.0,
    120.0, 280.0, 420.0, 520.0, 580.0, 600.0,
    590.0, 540.0, 460.0, 350.0, 210.0, 80.0,
    10.0, 0.0, 0.0, 0.0, 0.0, 0.0,
]

_LOAD_24H_KW = [
    650.0, 620.0, 600.0, 600.0, 630.0, 700.0,
    820.0, 950.0, 1050.0, 1100.0, 1120.0, 1150.0,
    1100.0, 1080.0, 1050.0, 1020.0, 1080.0, 1200.0,
    1320.0, 1300.0, 1200.0, 1050.0, 880.0, 740.0,
]


def build_normal_export_tracking_scenario() -> ScenarioDefinition:
    """Scenario A: 24-hour normal grid-connected operation tracking export target."""
    steps = [
        ScenarioStepProfile(
            hour_of_day=hour,
            local_load_kw=_LOAD_24H_KW[hour],
            pv_available_kw=_PV_24H_KW[hour],
            grid_export_target_kw=1200.0,
        )
        for hour in range(24)
    ]
    return ScenarioDefinition(
        name="normal_export_tracking",
        description="24-hour grid-connected operation tracking PCC export target.",
        dt_seconds=3600.0,
        initial_soc_fraction=0.50,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1400.0,
        steps=steps,
    )


def build_peak_cut_export_limit_scenario() -> ScenarioDefinition:
    """Scenario B: 24-hour grid-connected peak-cut operation enforcing export ceiling."""
    steps = [
        ScenarioStepProfile(
            hour_of_day=hour,
            local_load_kw=_LOAD_24H_KW[hour] * 0.55,
            pv_available_kw=_PV_24H_KW[hour],
            grid_export_target_kw=1800.0,
            grid_export_limit_kw=2000.0,
        )
        for hour in range(24)
    ]
    return ScenarioDefinition(
        name="peak_cut_export_limit",
        description="24-hour peak-cut operation capping PCC export at 2000 kW.",
        dt_seconds=3600.0,
        initial_soc_fraction=0.45,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1750.0,
        steps=steps,
    )


def build_momentary_grid_outage_scenario() -> ScenarioDefinition:
    """Exceptional Event: brief outage below momentary threshold recovering cleanly."""
    steps = [
        ScenarioStepProfile(hour_of_day=10, local_load_kw=800.0, pv_available_kw=400.0),
        ScenarioStepProfile(
            hour_of_day=10,
            local_load_kw=800.0,
            pv_available_kw=400.0,
            grid_connected=False,
            allow_grid_import=False,
            allow_grid_export=False,
            grid_outage_duration_seconds=20.0,
        ),
        ScenarioStepProfile(
            hour_of_day=10,
            local_load_kw=800.0,
            pv_available_kw=400.0,
            grid_connected=False,
            allow_grid_import=False,
            allow_grid_export=False,
            grid_outage_duration_seconds=40.0,
        ),
        ScenarioStepProfile(hour_of_day=10, local_load_kw=800.0, pv_available_kw=400.0),
        ScenarioStepProfile(hour_of_day=10, local_load_kw=800.0, pv_available_kw=400.0),
    ]
    return ScenarioDefinition(
        name="momentary_grid_outage",
        description="20-second step momentary outage recovering below 60-second threshold.",
        dt_seconds=20.0,
        initial_soc_fraction=0.55,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1000.0,
        steps=steps,
    )


def build_prolonged_outage_islanded_scenario() -> ScenarioDefinition:
    """Scenario C: prolonged blackout transitioning to islanded load-following."""
    steps: list[ScenarioStepProfile] = [
        ScenarioStepProfile(
            hour_of_day=12,
            local_load_kw=900.0,
            pv_available_kw=500.0,
            grid_export_target_kw=1000.0,
        )
    ]
    for offset in range(10):
        hour = 13 + (offset // 4)
        load_kw = 950.0 if offset < 6 else 1850.0
        pv_kw = max(0.0, 450.0 - offset * 45.0)
        steps.append(
            ScenarioStepProfile(
                hour_of_day=hour,
                local_load_kw=load_kw,
                pv_available_kw=pv_kw,
                grid_connected=False,
                allow_grid_import=False,
                allow_grid_export=False,
                grid_outage_duration_seconds=float((offset + 1) * 900),
            )
        )
    return ScenarioDefinition(
        name="prolonged_outage_islanded",
        description="Prolonged outage transitioning to islanded feeder load-following.",
        dt_seconds=900.0,
        initial_soc_fraction=0.60,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1200.0,
        steps=steps,
    )


def build_biomass_maintenance_scenario() -> ScenarioDefinition:
    """Scenario D: biomass maintenance mode operating on PV + BESS + grid/islanded."""
    steps = [
        ScenarioStepProfile(hour_of_day=8, local_load_kw=800.0, pv_available_kw=250.0),
        ScenarioStepProfile(
            hour_of_day=9,
            local_load_kw=750.0,
            pv_available_kw=400.0,
            biomass_maintenance_mode=True,
        ),
        ScenarioStepProfile(
            hour_of_day=10,
            local_load_kw=700.0,
            pv_available_kw=520.0,
            biomass_maintenance_mode=True,
        ),
        ScenarioStepProfile(
            hour_of_day=11,
            local_load_kw=700.0,
            pv_available_kw=550.0,
            biomass_maintenance_mode=True,
        ),
        ScenarioStepProfile(
            hour_of_day=12,
            local_load_kw=1200.0,
            pv_available_kw=300.0,
            grid_connected=False,
            allow_grid_import=False,
            allow_grid_export=False,
            biomass_maintenance_mode=True,
        ),
        ScenarioStepProfile(
            hour_of_day=13,
            local_load_kw=700.0,
            pv_available_kw=500.0,
            biomass_maintenance_mode=False,
        ),
    ]
    return ScenarioDefinition(
        name="biomass_maintenance_pv_bess",
        description="Biomass maintenance entry, PV+BESS operation, and exit.",
        dt_seconds=3600.0,
        initial_soc_fraction=0.65,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1000.0,
        steps=steps,
    )


def build_grid_restoration_sync_scenario() -> ScenarioDefinition:
    """Scenario E: islanded operation followed by grid restoration and sync ack."""
    steps = [
        ScenarioStepProfile(hour_of_day=14, local_load_kw=850.0, pv_available_kw=400.0),
        ScenarioStepProfile(
            hour_of_day=15,
            local_load_kw=850.0,
            pv_available_kw=350.0,
            grid_connected=False,
            allow_grid_import=False,
            allow_grid_export=False,
            grid_outage_duration_seconds=900.0,
        ),
        ScenarioStepProfile(
            hour_of_day=16,
            local_load_kw=850.0,
            pv_available_kw=250.0,
            grid_connected=False,
            allow_grid_import=False,
            allow_grid_export=False,
            grid_outage_duration_seconds=1800.0,
        ),
        ScenarioStepProfile(
            hour_of_day=17,
            local_load_kw=850.0,
            pv_available_kw=100.0,
            grid_connected=True,
            sync_permissive=False,
            operator_ack_restoration=False,
        ),
        ScenarioStepProfile(
            hour_of_day=18,
            local_load_kw=900.0,
            pv_available_kw=0.0,
            grid_connected=True,
            sync_permissive=True,
            operator_ack_restoration=True,
        ),
        ScenarioStepProfile(
            hour_of_day=19,
            local_load_kw=950.0,
            pv_available_kw=0.0,
            grid_connected=True,
            grid_export_target_kw=1000.0,
        ),
    ]
    return ScenarioDefinition(
        name="grid_restoration_sync",
        description="Outage to islanded followed by sync permissive restoration.",
        dt_seconds=900.0,
        initial_soc_fraction=0.60,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1000.0,
        steps=steps,
    )


def build_telemetry_comm_failure_scenario() -> ScenarioDefinition:
    """Exceptional Event: telemetry loss -> emergency_fault -> operator reset recovery."""
    steps = [
        ScenarioStepProfile(hour_of_day=9, local_load_kw=700.0, pv_available_kw=350.0),
        ScenarioStepProfile(
            hour_of_day=10,
            local_load_kw=700.0,
            pv_available_kw=400.0,
            comm_healthy=False,
        ),
        ScenarioStepProfile(
            hour_of_day=11,
            local_load_kw=700.0,
            pv_available_kw=420.0,
            comm_healthy=True,
            operator_ack_fault_reset=False,
        ),
        ScenarioStepProfile(
            hour_of_day=12,
            local_load_kw=700.0,
            pv_available_kw=450.0,
            comm_healthy=True,
            operator_ack_fault_reset=True,
        ),
        ScenarioStepProfile(
            hour_of_day=13,
            local_load_kw=700.0,
            pv_available_kw=450.0,
            comm_healthy=True,
            sync_permissive=True,
            operator_ack_restoration=True,
        ),
    ]
    return ScenarioDefinition(
        name="telemetry_comm_failure",
        description="Telemetry comm fault to emergency_fault and controlled recovery.",
        dt_seconds=900.0,
        initial_soc_fraction=0.55,
        initial_biomass_status=BiomassStatus.RUNNING,
        initial_biomass_power_kw=1000.0,
        steps=steps,
    )


def get_standard_scenarios() -> dict[str, ScenarioDefinition]:
    """Return all standard Phase 4 validation scenarios keyed by name."""
    scenarios = [
        build_normal_export_tracking_scenario(),
        build_peak_cut_export_limit_scenario(),
        build_momentary_grid_outage_scenario(),
        build_prolonged_outage_islanded_scenario(),
        build_biomass_maintenance_scenario(),
        build_grid_restoration_sync_scenario(),
        build_telemetry_comm_failure_scenario(),
    ]
    return {sc.name: sc for sc in scenarios}
