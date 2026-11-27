"""Module 5: Independent Hybrid Constraint & Safety Supervisor."""

from __future__ import annotations

from dataclasses import dataclass

from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
    ProposedDispatch,
)
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.power_balance import check_power_balance
from renewablex_controller.core.units import (
    NUMERIC_ZERO_EPSILON,
    BiomassStatus,
    CommLossAction,
    ConstraintStatus,
    OperatingMode,
)

_OFF_GRID_MODES = frozenset(
    {
        OperatingMode.MOMENTARY_OUTAGE,
        OperatingMode.PROLONGED_OUTAGE,
        OperatingMode.ISLANDED,
        OperatingMode.EMERGENCY_FAULT,
    }
)

_RESERVE_DRAWDOWN_MODES = frozenset(
    {
        OperatingMode.PROLONGED_OUTAGE,
        OperatingMode.ISLANDED,
        OperatingMode.BIOMASS_MAINTENANCE,
    }
)


@dataclass(frozen=True)
class _BiomassEnvelope:
    """Effective biomass command bounds and start/stop flags for one step."""

    command_kw: float
    lower_kw: float
    upper_kw: float
    start_request: bool
    stop_request: bool


class SafetySupervisor:
    """Validates, clamps, or rejects proposed dispatch before finalizing outputs."""

    def __init__(self, config: PlantOperationalConfig) -> None:
        self._config = config

    @property
    def config(self) -> PlantOperationalConfig:
        """Plant operational configuration enforced by this supervisor."""
        return self._config

    def supervise(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        state: ControllerState | None = None,
    ) -> ControllerStepOutput:
        """Enforce physical, mode, telemetry, and closed power-balance constraints."""
        violations: list[str] = []
        policy = self._config.control_policy
        tol = policy.power_balance_tolerance_kw

        # 1. Check emergency stop and telemetry health
        telemetry_stale = step_in.telemetry_age_seconds > policy.max_telemetry_age_seconds
        comm_fault = (not step_in.comm_healthy) or telemetry_stale

        if step_in.emergency_stop:
            violations.append("EMERGENCY_STOP_ACTIVE")
        if not step_in.comm_healthy:
            violations.append("COMM_HEALTH_FAULT")
        if telemetry_stale:
            violations.append("STALE_TELEMETRY")

        if step_in.emergency_stop or (
            comm_fault and policy.comm_loss_action == CommLossAction.EMERGENCY_FAULT
        ):
            return self._build_rejected_output(step_in, proposed, violations, tol)

        clamped = False
        infeasible = False

        # 2. Enforce PV limits
        pv_cmd, pv_clamped = self._clamp_pv(step_in, proposed, violations)
        clamped = clamped or pv_clamped

        # 3. Enforce biomass limits (scaled by dt_seconds)
        bio_env, bio_clamped = self._clamp_biomass(step_in, proposed, state, violations)
        clamped = clamped or bio_clamped
        bio_cmd = bio_env.command_kw

        # 4. Enforce BESS limits and mutual exclusion
        bess_charge, bess_discharge, eff_max_charge, eff_max_discharge, bess_clamped = (
            self._clamp_bess(step_in, proposed, violations)
        )
        clamped = clamped or bess_clamped

        # 5. Enforce grid limits and mutual exclusion
        grid_import, grid_export, grid_max_import, grid_max_export, grid_clamped = (
            self._clamp_grid(step_in, proposed, comm_fault, violations)
        )
        clamped = clamped or grid_clamped

        # 6. Target served load
        served_load, unmet_load, load_clamped = self._resolve_initial_load(
            step_in, proposed, violations
        )
        clamped = clamped or load_clamped

        # 7. Closed active-power balance reconciliation
        imbalance = (pv_cmd + bio_cmd + bess_discharge + grid_import) - (
            served_load + step_in.parasitic_load_kw + bess_charge + grid_export
        )

        reconciled = False
        if imbalance > tol:
            reconciled = True
            # Serve unmet load first
            add_serve = min(imbalance, unmet_load)
            served_load += add_serve
            unmet_load -= add_serve
            imbalance -= add_serve

            # Reduce grid import
            dec_imp = min(imbalance, grid_import)
            grid_import -= dec_imp
            imbalance -= dec_imp

            # Reduce BESS discharge
            dec_dis = min(imbalance, bess_discharge)
            bess_discharge -= dec_dis
            imbalance -= dec_dis

            # Increase grid export if not importing
            if grid_import <= NUMERIC_ZERO_EPSILON:
                exp_headroom = max(0.0, grid_max_export - grid_export)
                inc_exp = min(imbalance, exp_headroom)
                grid_export += inc_exp
                imbalance -= inc_exp

            # Increase BESS charge if not discharging
            if bess_discharge <= NUMERIC_ZERO_EPSILON:
                chg_headroom = max(0.0, eff_max_charge - bess_charge)
                inc_chg = min(imbalance, chg_headroom)
                bess_charge += inc_chg
                imbalance -= inc_chg

            # Curtail PV if allowed
            if self._config.pv.curtailment_enabled and pv_cmd > 0.0:
                dec_pv = min(imbalance, pv_cmd)
                pv_cmd -= dec_pv
                imbalance -= dec_pv

            # Reduce biomass down to its effective lower limit
            if bio_cmd > bio_env.lower_kw:
                dec_bio = min(imbalance, bio_cmd - bio_env.lower_kw)
                bio_cmd -= dec_bio
                imbalance -= dec_bio

            if imbalance > tol:
                infeasible = True
                violations.append("EXCESS_GENERATION_INFEASIBLE")

        elif imbalance < -tol:
            reconciled = True
            deficit = -imbalance

            # Reduce grid export
            dec_exp = min(deficit, grid_export)
            grid_export -= dec_exp
            deficit -= dec_exp

            # Reduce BESS charge
            dec_chg = min(deficit, bess_charge)
            bess_charge -= dec_chg
            deficit -= dec_chg

            # Increase BESS discharge if not charging
            if bess_charge <= NUMERIC_ZERO_EPSILON:
                dis_headroom = max(0.0, eff_max_discharge - bess_discharge)
                inc_dis = min(deficit, dis_headroom)
                bess_discharge += inc_dis
                deficit -= inc_dis

            # Increase grid import if not exporting
            if grid_export <= NUMERIC_ZERO_EPSILON:
                imp_headroom = max(0.0, grid_max_import - grid_import)
                inc_imp = min(deficit, imp_headroom)
                grid_import += inc_imp
                deficit -= inc_imp

            # Shed local served load
            shed = min(deficit, served_load)
            served_load -= shed
            unmet_load += shed
            deficit -= shed

            if deficit > tol:
                infeasible = True
                violations.append("STATION_AUX_DEFICIT")

        if reconciled:
            clamped = True
            if not violations:
                violations.append("POWER_BALANCE_RECONCILED")

        # 8. Clean floating-point noise and verify invariants
        pv_cmd = self._zero_eps(min(pv_cmd, step_in.pv_available_kw))
        pv_curt = self._zero_eps(max(0.0, step_in.pv_available_kw - pv_cmd))
        bio_cmd = self._zero_eps(bio_cmd)
        bess_charge = self._zero_eps(bess_charge)
        bess_discharge = self._zero_eps(bess_discharge)
        grid_import = self._zero_eps(grid_import)
        grid_export = self._zero_eps(grid_export)
        served_load = self._zero_eps(min(served_load, step_in.local_load_kw))
        unmet_load = self._zero_eps(max(0.0, step_in.local_load_kw - served_load))

        report = check_power_balance(
            local_load_kw=step_in.local_load_kw,
            parasitic_load_kw=step_in.parasitic_load_kw,
            pv_available_kw=step_in.pv_available_kw,
            pv_command_kw=pv_cmd,
            pv_curtailment_kw=pv_curt,
            biomass_command_kw=bio_cmd,
            bess_charge_kw=bess_charge,
            bess_discharge_kw=bess_discharge,
            grid_import_kw=grid_import,
            grid_export_kw=grid_export,
            served_load_kw=served_load,
            unmet_load_kw=unmet_load,
            tolerance_kw=tol,
        )

        if not report.is_balanced:
            infeasible = True
            for code in report.violations:
                if code not in violations:
                    violations.append(code)

        if infeasible:
            status = ConstraintStatus.INFEASIBLE
        elif clamped or violations:
            status = ConstraintStatus.CLAMPED
        else:
            status = ConstraintStatus.OK

        trace_reason = self._format_trace_reason(
            mode=proposed.operating_mode,
            rule_ids=proposed.active_rule_ids,
            pv_cmd=pv_cmd,
            pv_curt=pv_curt,
            bio_cmd=bio_cmd,
            bess_charge=bess_charge,
            bess_discharge=bess_discharge,
            grid_export=grid_export,
            grid_import=grid_import,
            unmet_load=unmet_load,
            status=status,
            base_reason=proposed.trace_reason,
        )

        return ControllerStepOutput(
            operating_mode=proposed.operating_mode,
            sequence_state=proposed.sequence_state,
            pv_command_kw=pv_cmd,
            pv_curtailment_kw=pv_curt,
            bess_charge_kw=bess_charge,
            bess_discharge_kw=bess_discharge,
            biomass_command_kw=bio_cmd,
            biomass_start_request=bio_env.start_request,
            biomass_stop_request=bio_env.stop_request,
            grid_import_kw=grid_import,
            grid_export_kw=grid_export,
            served_load_kw=served_load,
            unmet_load_kw=unmet_load,
            constraint_status=status,
            violations=violations,
            active_rule_ids=list(proposed.active_rule_ids),
            trace_reason=trace_reason,
            balance_residual_kw=report.net_residual_kw,
        )

    def _build_rejected_output(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        violations: list[str],
        tol: float,
    ) -> ControllerStepOutput:
        stop_req = step_in.biomass_status == BiomassStatus.RUNNING and step_in.biomass_can_stop
        report = check_power_balance(
            local_load_kw=step_in.local_load_kw,
            parasitic_load_kw=step_in.parasitic_load_kw,
            pv_available_kw=step_in.pv_available_kw,
            pv_command_kw=0.0,
            pv_curtailment_kw=step_in.pv_available_kw,
            biomass_command_kw=0.0,
            bess_charge_kw=0.0,
            bess_discharge_kw=0.0,
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            served_load_kw=0.0,
            unmet_load_kw=step_in.local_load_kw,
            tolerance_kw=tol,
        )
        trace_reason = self._format_trace_reason(
            mode=OperatingMode.EMERGENCY_FAULT,
            rule_ids=proposed.active_rule_ids,
            pv_cmd=0.0,
            pv_curt=step_in.pv_available_kw,
            bio_cmd=0.0,
            bess_charge=0.0,
            bess_discharge=0.0,
            grid_export=0.0,
            grid_import=0.0,
            unmet_load=step_in.local_load_kw,
            status=ConstraintStatus.REJECTED,
            base_reason=proposed.trace_reason,
        )
        return ControllerStepOutput(
            operating_mode=OperatingMode.EMERGENCY_FAULT,
            sequence_state=None,
            pv_command_kw=0.0,
            pv_curtailment_kw=step_in.pv_available_kw,
            bess_charge_kw=0.0,
            bess_discharge_kw=0.0,
            biomass_command_kw=0.0,
            biomass_start_request=False,
            biomass_stop_request=stop_req,
            grid_import_kw=0.0,
            grid_export_kw=0.0,
            served_load_kw=0.0,
            unmet_load_kw=step_in.local_load_kw,
            constraint_status=ConstraintStatus.REJECTED,
            violations=violations,
            active_rule_ids=list(proposed.active_rule_ids),
            trace_reason=trace_reason,
            balance_residual_kw=report.net_residual_kw,
        )

    def _clamp_pv(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        violations: list[str],
    ) -> tuple[float, bool]:
        pv_cfg = self._config.pv
        if not pv_cfg.available:
            if proposed.pv_command_kw > NUMERIC_ZERO_EPSILON:
                violations.append("PV_UNAVAILABLE")
                return 0.0, True
            return 0.0, False

        pv_max = min(step_in.pv_available_kw, pv_cfg.rated_ac_kw)
        pv_cmd = proposed.pv_command_kw
        clamped = False

        if pv_cmd > pv_max + NUMERIC_ZERO_EPSILON:
            pv_cmd = pv_max
            clamped = True
            violations.append("PV_ABOVE_AC_OR_AVAILABILITY_LIMIT")
        elif pv_cmd > pv_max:
            pv_cmd = pv_max

        if not pv_cfg.curtailment_enabled and pv_cmd < pv_max - NUMERIC_ZERO_EPSILON:
            pv_cmd = pv_max
            clamped = True
            violations.append("PV_CURTAILMENT_DISABLED")

        return max(0.0, pv_cmd), clamped

    def _clamp_biomass(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        state: ControllerState | None,
        violations: list[str],
    ) -> tuple[_BiomassEnvelope, bool]:
        bio_cfg = self._config.biomass
        clamped = False

        fuel_depleted = (
            step_in.biomass_fuel_remaining_tons is not None
            and step_in.biomass_fuel_remaining_tons <= 0.0
        )
        maint_locked = (
            step_in.biomass_maintenance_mode
            or proposed.operating_mode == OperatingMode.BIOMASS_MAINTENANCE
        )

        start_req = proposed.biomass_start_request
        stop_req = proposed.biomass_stop_request

        if start_req and (
            maint_locked or not bio_cfg.available or fuel_depleted or not step_in.biomass_can_start
        ):
            start_req = False
            clamped = True
            violations.append("BIOMASS_START_BLOCKED")

        if stop_req and not step_in.biomass_can_stop:
            stop_req = False
            clamped = True
            violations.append("BIOMASS_STOP_BLOCKED")

        if maint_locked and step_in.biomass_status == BiomassStatus.RUNNING and step_in.biomass_can_stop:
            stop_req = True

        cannot_generate = (
            maint_locked
            or not bio_cfg.available
            or fuel_depleted
            or step_in.biomass_status
            in {BiomassStatus.OFF, BiomassStatus.STARTING, BiomassStatus.OUTAGE}
        )
        if cannot_generate:
            if proposed.biomass_command_kw > NUMERIC_ZERO_EPSILON:
                clamped = True
                violations.append("BIOMASS_UNAVAILABLE_OR_MAINTENANCE")
            return (
                _BiomassEnvelope(
                    command_kw=0.0,
                    lower_kw=0.0,
                    upper_kw=0.0,
                    start_request=start_req,
                    stop_request=stop_req,
                ),
                clamped,
            )

        ref_power = step_in.biomass_current_power_kw

        bio_upper = min(bio_cfg.operating_max_kw, step_in.biomass_available_kw)
        bio_lower = min(bio_upper, max(bio_cfg.min_load_kw, step_in.biomass_min_load_kw))
        ramp_up_ceil = ref_power + bio_cfg.max_ramp_up_kw(step_in.dt_seconds)
        ramp_down_floor = max(0.0, ref_power - bio_cfg.max_ramp_down_kw(step_in.dt_seconds))

        if step_in.biomass_status == BiomassStatus.STOPPING:
            eff_upper = min(bio_upper, ref_power)
            eff_lower = min(eff_upper, ramp_down_floor)
        else:
            eff_upper = min(bio_upper, ramp_up_ceil)
            raw_lower = max(bio_lower, ramp_down_floor)
            if raw_lower > eff_upper + NUMERIC_ZERO_EPSILON:
                violations.append("BIOMASS_LIMIT_CONFLICT")
                eff_lower = eff_upper
                clamped = True
            else:
                eff_lower = min(eff_upper, raw_lower)

        bio_cmd = proposed.biomass_command_kw
        if bio_cmd > eff_upper + NUMERIC_ZERO_EPSILON:
            clamped = True
            if bio_upper < ramp_up_ceil and bio_cmd > bio_upper + NUMERIC_ZERO_EPSILON:
                violations.append("BIOMASS_ABOVE_MAX")
            else:
                violations.append("BIOMASS_RAMP_UP_CLAMPED")
            bio_cmd = eff_upper
        elif bio_cmd < eff_lower - NUMERIC_ZERO_EPSILON:
            clamped = True
            if ramp_down_floor > bio_lower and bio_cmd < ramp_down_floor - NUMERIC_ZERO_EPSILON:
                violations.append("BIOMASS_RAMP_DOWN_CLAMPED")
            else:
                violations.append("BIOMASS_BELOW_MIN")
            bio_cmd = eff_lower

        return (
            _BiomassEnvelope(
                command_kw=max(0.0, bio_cmd),
                lower_kw=max(0.0, eff_lower),
                upper_kw=max(0.0, eff_upper),
                start_request=start_req,
                stop_request=stop_req,
            ),
            clamped,
        )

    def _clamp_bess(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        violations: list[str],
    ) -> tuple[float, float, float, float, bool]:
        bess_cfg = self._config.bess
        clamped = False

        charge = proposed.bess_charge_kw
        discharge = proposed.bess_discharge_kw
        if charge > NUMERIC_ZERO_EPSILON and discharge > NUMERIC_ZERO_EPSILON:
            clamped = True
            violations.append("BESS_SIMULTANEOUS_CHARGE_DISCHARGE")
            if charge >= discharge:
                charge -= discharge
                discharge = 0.0
            else:
                discharge -= charge
                charge = 0.0

        use_min_floor = (
            proposed.operating_mode in _RESERVE_DRAWDOWN_MODES
            and bess_cfg.allow_reserve_drawdown_in_islanded
        )
        soc_floor = bess_cfg.soc_min_fraction if use_min_floor else bess_cfg.reserve_soc_fraction

        eff_max_charge = min(
            step_in.bess_max_charge_kw,
            bess_cfg.max_charge_kw_for_step(step_in.bess_soc_fraction, step_in.dt_seconds),
        )
        eff_max_discharge = min(
            step_in.bess_max_discharge_kw,
            bess_cfg.max_discharge_kw_for_step(
                step_in.bess_soc_fraction,
                step_in.dt_seconds,
                soc_floor_fraction=soc_floor,
            ),
        )

        if charge > eff_max_charge + NUMERIC_ZERO_EPSILON:
            charge = eff_max_charge
            clamped = True
            violations.append("BESS_CHARGE_LIMIT_CLAMPED")
        elif charge > eff_max_charge:
            charge = eff_max_charge

        if discharge > eff_max_discharge + NUMERIC_ZERO_EPSILON:
            discharge = eff_max_discharge
            clamped = True
            violations.append("BESS_DISCHARGE_LIMIT_CLAMPED")
        elif discharge > eff_max_discharge:
            discharge = eff_max_discharge

        return (
            max(0.0, charge),
            max(0.0, discharge),
            max(0.0, eff_max_charge),
            max(0.0, eff_max_discharge),
            clamped,
        )

    def _clamp_grid(
        self,
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        comm_fault: bool,
        violations: list[str],
    ) -> tuple[float, float, float, float, bool]:
        grid_cfg = self._config.grid
        clamped = False

        grid_import = proposed.grid_import_kw
        grid_export = proposed.grid_export_kw
        if grid_import > NUMERIC_ZERO_EPSILON and grid_export > NUMERIC_ZERO_EPSILON:
            clamped = True
            violations.append("GRID_SIMULTANEOUS_IMPORT_EXPORT")
            if grid_export >= grid_import:
                grid_export -= grid_import
                grid_import = 0.0
            else:
                grid_import -= grid_export
                grid_export = 0.0

        grid_active = step_in.grid_connected and proposed.operating_mode not in _OFF_GRID_MODES
        if not grid_active:
            max_import = 0.0
            max_export = 0.0
        else:
            max_import = (
                grid_cfg.interconnection_limit_kw
                if (step_in.allow_grid_import and grid_cfg.default_allow_import)
                else 0.0
            )
            if not step_in.allow_grid_export or not grid_cfg.default_allow_export or comm_fault:
                max_export = 0.0
            else:
                max_export = grid_cfg.interconnection_limit_kw
                if grid_cfg.default_export_limit_kw is not None:
                    max_export = min(max_export, grid_cfg.default_export_limit_kw)
                if step_in.grid_export_limit_kw is not None:
                    max_export = min(max_export, step_in.grid_export_limit_kw)

        if grid_import > max_import + NUMERIC_ZERO_EPSILON:
            grid_import = max_import
            clamped = True
            violations.append("GRID_IMPORT_LIMIT_CLAMPED")
        elif grid_import > max_import:
            grid_import = max_import

        if grid_export > max_export + NUMERIC_ZERO_EPSILON:
            grid_export = max_export
            clamped = True
            violations.append("GRID_EXPORT_LIMIT_CLAMPED")
        elif grid_export > max_export:
            grid_export = max_export

        return (
            max(0.0, grid_import),
            max(0.0, grid_export),
            max(0.0, max_import),
            max(0.0, max_export),
            clamped,
        )

    @staticmethod
    def _resolve_initial_load(
        step_in: ControllerStepInput,
        proposed: ProposedDispatch,
        violations: list[str],
    ) -> tuple[float, float, bool]:
        clamped = False
        if (
            proposed.served_load_kw == 0.0
            and proposed.unmet_load_kw == 0.0
            and step_in.local_load_kw > 0.0
        ):
            served = step_in.local_load_kw
        else:
            served = proposed.served_load_kw
            if served > step_in.local_load_kw + NUMERIC_ZERO_EPSILON:
                served = step_in.local_load_kw
                clamped = True
                violations.append("SERVED_LOAD_ABOVE_LOCAL_LOAD")
            else:
                served = min(served, step_in.local_load_kw)
        unmet = max(0.0, step_in.local_load_kw - served)
        return served, unmet, clamped

    @staticmethod
    def _zero_eps(value: float) -> float:
        return 0.0 if abs(value) < NUMERIC_ZERO_EPSILON else value

    @staticmethod
    def _format_trace_reason(
        *,
        mode: OperatingMode,
        rule_ids: list[str],
        pv_cmd: float,
        pv_curt: float,
        bio_cmd: float,
        bess_charge: float,
        bess_discharge: float,
        grid_export: float,
        grid_import: float,
        unmet_load: float,
        status: ConstraintStatus,
        base_reason: str,
    ) -> str:
        bess_str = (
            f"bess_chg={bess_charge:.1f}kW"
            if bess_charge >= bess_discharge
            else f"bess_dis={bess_discharge:.1f}kW"
        )
        grid_str = (
            f"exp={grid_export:.1f}kW"
            if grid_export >= grid_import
            else f"imp={grid_import:.1f}kW"
        )
        summary = (
            f"mode={mode.value} | rules={rule_ids} | "
            f"pv={pv_cmd:.1f}kW(curt={pv_curt:.1f}) bio={bio_cmd:.1f}kW "
            f"{bess_str} {grid_str} unmet={unmet_load:.1f}kW | safety={status.value}"
        )
        if base_reason:
            return f"{base_reason} || {summary}"
        return summary
