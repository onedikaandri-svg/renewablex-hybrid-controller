"""Phase 6: Rolling-horizon lookahead MPC dispatcher (pure-Python)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.bess_manager import BESSSupervisoryManager
from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
    ProposedDispatch,
)
from renewablex_controller.core.dispatch_engine import PowerDispatchEngine
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.safety_supervisor import SafetySupervisor
from renewablex_controller.core.units import BiomassStatus, OperatingMode


class ForecastPoint(BaseModel):
    """Single future lookahead step forecast for MPC scheduling."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    dt_seconds: float = Field(gt=0.0)
    local_load_kw: float = Field(ge=0.0)
    parasitic_load_kw: float = Field(default=0.0, ge=0.0)
    pv_available_kw: float = Field(ge=0.0)
    tariff_per_kwh: float = 0.0
    grid_export_limit_kw: float | None = Field(default=None, ge=0.0)


class LookaheadMPCDispatcher:
    """Pure-Python rolling-horizon lookahead dispatcher for grid-connected BESS."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        *,
        horizon_steps: int = 6,
    ) -> None:
        if horizon_steps < 1:
            raise ValueError("horizon_steps must be >= 1")
        self._config = config
        self._horizon_steps = horizon_steps
        self._bess_manager = BESSSupervisoryManager(config.bess)
        self._fallback_engine = PowerDispatchEngine(config)

    @property
    def horizon_steps(self) -> int:
        """Maximum lookahead horizon length in steps."""
        return self._horizon_steps

    def propose(
        self,
        step_in: ControllerStepInput,
        forecast: list[ForecastPoint],
        operating_mode: OperatingMode,
        *,
        sequence_state: str | None = None,
    ) -> ProposedDispatch:
        """Compute a lookahead-aware dispatch proposal for the active step."""
        window = forecast[: self._horizon_steps]
        if (
            operating_mode != OperatingMode.GRID_CONNECTED_NORMAL
            and operating_mode != OperatingMode.PEAK_CUT
        ) or not step_in.grid_connected or step_in.emergency_stop or not window:
            return self._fallback_engine.propose(
                step_in,
                operating_mode,
                sequence_state=sequence_state,
            )

        bess_cap = self._bess_manager.compute_capability(step_in, operating_mode)
        pv_max = (
            0.0
            if not self._config.pv.available
            else min(step_in.pv_available_kw, self._config.pv.rated_ac_kw)
        )
        pv_cmd = pv_max

        # Feasible biomass power
        bio_cmd = 0.0
        if (
            not step_in.biomass_maintenance_mode
            and self._config.biomass.available
            and step_in.biomass_status == BiomassStatus.RUNNING
        ):
            bio_upper = min(
                self._config.biomass.operating_max_kw,
                step_in.biomass_available_kw,
                step_in.biomass_current_power_kw
                + self._config.biomass.max_ramp_up_kw(step_in.dt_seconds),
            )
            bio_lower = min(
                bio_upper,
                max(
                    self._config.biomass.min_load_kw,
                    step_in.biomass_min_load_kw,
                    step_in.biomass_current_power_kw
                    - self._config.biomass.max_ramp_down_kw(step_in.dt_seconds),
                ),
            )
            bio_cmd = min(max(step_in.biomass_current_power_kw, bio_lower), bio_upper)

        avg_future_tariff = sum(p.tariff_per_kwh for p in window) / len(window)
        max_future_tariff = max(p.tariff_per_kwh for p in window)

        bess_charge = 0.0
        bess_discharge = 0.0
        total_demand = step_in.total_demand_kw
        pre_bess_net = pv_cmd + bio_cmd - total_demand

        max_export = self._config.grid.interconnection_limit_kw
        if not (step_in.allow_grid_export and self._config.grid.default_allow_export):
            max_export = 0.0
        else:
            if self._config.grid.default_export_limit_kw is not None:
                max_export = min(max_export, self._config.grid.default_export_limit_kw)
            if step_in.grid_export_limit_kw is not None:
                max_export = min(max_export, step_in.grid_export_limit_kw)

        # Lookahead arbitrage and peak-cut readiness
        if pre_bess_net > max_export:
            bess_charge, _ = self._bess_manager.allocate_power(
                bess_cap,
                requested_charge_kw=(pre_bess_net - max_export),
            )
        elif step_in.pv_surplus_kw > 0.0 and bess_cap.max_charge_kw > 0.0:
            bess_charge, _ = self._bess_manager.allocate_power(
                bess_cap,
                requested_charge_kw=step_in.pv_surplus_kw,
            )
        elif (
            max_future_tariff > step_in.tariff_per_kwh * 1.15
            and step_in.allow_grid_import
            and self._config.grid.default_allow_import
            and bess_cap.max_charge_kw > 0.0
        ):
            bess_charge, _ = self._bess_manager.allocate_power(
                bess_cap,
                requested_charge_kw=bess_cap.max_charge_kw,
            )
        elif (
            step_in.tariff_per_kwh >= avg_future_tariff * 1.10
            and pre_bess_net < 0.0
            and bess_cap.max_discharge_kw > 0.0
        ):
            _, bess_discharge = self._bess_manager.allocate_power(
                bess_cap,
                requested_discharge_kw=-pre_bess_net,
            )

        net_pcc = pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand
        grid_export = max(0.0, net_pcc)
        grid_import = max(0.0, -net_pcc)

        return ProposedDispatch(
            operating_mode=operating_mode,
            sequence_state=sequence_state,
            pv_command_kw=pv_cmd,
            pv_curtailment_kw=max(0.0, step_in.pv_available_kw - pv_cmd),
            bess_charge_kw=bess_charge,
            bess_discharge_kw=bess_discharge,
            biomass_command_kw=bio_cmd,
            grid_import_kw=grid_import,
            grid_export_kw=grid_export,
            served_load_kw=step_in.local_load_kw,
            unmet_load_kw=0.0,
            active_rule_ids=[],
            trace_reason=(
                f"mpc_horizon={len(window)} avg_tariff={avg_future_tariff:.3f} "
                f"max_tariff={max_future_tariff:.3f}"
            ),
        )

    def dispatch_step(
        self,
        step_in: ControllerStepInput,
        forecast: list[ForecastPoint],
        operating_mode: OperatingMode,
        *,
        supervisor: SafetySupervisor | None = None,
        state: ControllerState | None = None,
        sequence_state: str | None = None,
    ) -> ControllerStepOutput:
        """Compute MPC proposal and enforce Module 5 safety supervision."""
        proposed = self.propose(
            step_in,
            forecast,
            operating_mode,
            sequence_state=sequence_state,
        )
        sup = supervisor if supervisor is not None else SafetySupervisor(self._config)
        return sup.supervise(step_in, proposed, state=state)
