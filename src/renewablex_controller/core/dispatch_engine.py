"""Module 3: Hybrid active-power dispatch engine combining JSON EMS rules and mode dispatchers."""

from __future__ import annotations

from renewablex_controller.core.bess_manager import BESSSupervisoryManager
from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
    ProposedDispatch,
)
from renewablex_controller.core.ems_rules import EMSRuleInterpreter, EMSRuleSet
from renewablex_controller.core.plant_model import PlantOperationalConfig
from renewablex_controller.core.safety_supervisor import SafetySupervisor
from renewablex_controller.core.units import (
    NUMERIC_ZERO_EPSILON,
    BiomassStatus,
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


class PowerDispatchEngine:
    """Calculates per-step active power allocations across PV, BESS, Biomass, and Grid."""

    def __init__(
        self,
        config: PlantOperationalConfig,
        rule_set: EMSRuleSet | None = None,
    ) -> None:
        self._config = config
        self._rule_set = rule_set
        self._bess_manager = BESSSupervisoryManager(config.bess)
        self._rule_interpreter = EMSRuleInterpreter()

    @property
    def config(self) -> PlantOperationalConfig:
        """Plant operational configuration."""
        return self._config

    @property
    def rule_set(self) -> EMSRuleSet | None:
        """Optional loaded JSON EMS rule set."""
        return self._rule_set

    def propose(
        self,
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
        sequence_state: str | None = None,
    ) -> ProposedDispatch:
        """Compute an active-power dispatch proposal prior to Module 5 supervision."""
        if operating_mode == OperatingMode.EMERGENCY_FAULT or step_in.emergency_stop:
            return ProposedDispatch(
                operating_mode=OperatingMode.EMERGENCY_FAULT,
                sequence_state=None,
                pv_command_kw=0.0,
                pv_curtailment_kw=step_in.pv_available_kw,
                bess_charge_kw=0.0,
                bess_discharge_kw=0.0,
                biomass_command_kw=0.0,
                biomass_start_request=False,
                biomass_stop_request=(
                    step_in.biomass_status == BiomassStatus.RUNNING and step_in.biomass_can_stop
                ),
                grid_import_kw=0.0,
                grid_export_kw=0.0,
                served_load_kw=0.0,
                unmet_load_kw=step_in.local_load_kw,
                active_rule_ids=[],
                trace_reason="emergency_fault_shutdown",
            )

        active_rule_ids: list[str] = []
        skipped_rule_ids: list[str] = []
        intents: dict[str, tuple[str, float | None]] = {}
        fallback_enabled = True

        if self._rule_set is not None:
            eval_res = self._rule_interpreter.evaluate(self._rule_set, step_in, operating_mode)
            active_rule_ids = eval_res.active_rule_ids
            skipped_rule_ids = eval_res.skipped_rule_ids
            intents = eval_res.intents
            fallback_enabled = self._rule_set.fallback_mode_dispatcher

        bess_cap = self._bess_manager.compute_capability(step_in, operating_mode)

        # PV ceiling
        pv_max = (
            0.0
            if not self._config.pv.available
            else min(step_in.pv_available_kw, self._config.pv.rated_ac_kw)
        )
        pv_cmd = pv_max
        if "pv" in intents:
            _, curtail_limit = intents["pv"]
            if curtail_limit is not None and self._config.pv.curtailment_enabled:
                pv_cmd = max(0.0, pv_max - min(pv_max, curtail_limit))

        # Biomass feasible envelope
        bio_lower, bio_upper, bio_can_gen = self._biomass_envelope(step_in, operating_mode)
        bio_start_req = False
        bio_stop_req = (
            (step_in.biomass_maintenance_mode or operating_mode == OperatingMode.BIOMASS_MAINTENANCE)
            and step_in.biomass_status == BiomassStatus.RUNNING
            and step_in.biomass_can_stop
        )
        if "biomass_lifecycle" in intents:
            lc_action, _ = intents["biomass_lifecycle"]
            if lc_action == "start_biomass":
                bio_start_req = True
            elif lc_action == "stop_biomass":
                bio_stop_req = True

        bio_rule_locked = "biomass_power" in intents
        if bio_rule_locked:
            _, target_val = intents["biomass_power"]
            bio_cmd = 0.0 if target_val is None else target_val
        elif not bio_can_gen:
            bio_cmd = 0.0
        elif step_in.biomass_status == BiomassStatus.STOPPING:
            bio_cmd = bio_lower
        else:
            bio_cmd = min(max(step_in.biomass_current_power_kw, bio_lower), bio_upper)

        # Grid limits & export target
        grid_active = step_in.grid_connected and operating_mode not in _OFF_GRID_MODES
        max_import = (
            self._config.grid.interconnection_limit_kw
            if (grid_active and step_in.allow_grid_import and self._config.grid.default_allow_import)
            else 0.0
        )
        if not (grid_active and step_in.allow_grid_export and self._config.grid.default_allow_export):
            max_export = 0.0
        else:
            max_export = self._config.grid.interconnection_limit_kw
            if self._config.grid.default_export_limit_kw is not None:
                max_export = min(max_export, self._config.grid.default_export_limit_kw)
            if step_in.grid_export_limit_kw is not None:
                max_export = min(max_export, step_in.grid_export_limit_kw)
            if "grid_export_limit" in intents:
                _, rule_exp_limit = intents["grid_export_limit"]
                if rule_exp_limit is not None:
                    max_export = min(max_export, rule_exp_limit)

        export_target = step_in.grid_export_target_kw
        if "grid_export_target" in intents:
            _, rule_exp_target = intents["grid_export_target"]
            if rule_exp_target is not None:
                export_target = rule_exp_target

        # Explicit BESS rule intent
        bess_charge = 0.0
        bess_discharge = 0.0
        bess_rule_locked = "bess" in intents
        if bess_rule_locked:
            bess_act, bess_val = intents["bess"]
            req = 0.0 if bess_val is None else bess_val
            if bess_act == "charge_bess":
                bess_charge, bess_discharge = self._bess_manager.allocate_power(
                    bess_cap, requested_charge_kw=req
                )
            elif bess_act == "discharge_bess":
                bess_charge, bess_discharge = self._bess_manager.allocate_power(
                    bess_cap, requested_discharge_kw=req
                )

        total_demand = step_in.total_demand_kw
        served_load = step_in.local_load_kw
        unmet_load = 0.0
        grid_import = 0.0
        grid_export = 0.0

        if not fallback_enabled:
            net = (
                pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand
            )
            if net >= 0.0:
                grid_export = min(net, max_export) if grid_active else 0.0
            else:
                grid_import = min(-net, max_import) if grid_active else 0.0
            trace = f"rules_only | skipped={skipped_rule_ids}"
            return ProposedDispatch(
                operating_mode=operating_mode,
                sequence_state=sequence_state,
                pv_command_kw=pv_cmd,
                pv_curtailment_kw=max(0.0, step_in.pv_available_kw - pv_cmd),
                bess_charge_kw=bess_charge,
                bess_discharge_kw=bess_discharge,
                biomass_command_kw=bio_cmd,
                biomass_start_request=bio_start_req,
                biomass_stop_request=bio_stop_req,
                grid_import_kw=grid_import,
                grid_export_kw=grid_export,
                served_load_kw=served_load,
                unmet_load_kw=unmet_load,
                active_rule_ids=active_rule_ids,
                trace_reason=trace,
            )

        if grid_active:
            if export_target is not None:
                desired_export = min(export_target, max_export)
                if not bio_rule_locked and bio_can_gen and step_in.biomass_status == BiomassStatus.RUNNING:
                    needed_bio = (total_demand + desired_export) - pv_cmd
                    bio_cmd = min(max(needed_bio, bio_lower), bio_upper)

                pre_bess_net = pv_cmd + bio_cmd - total_demand
                export_delta = pre_bess_net - desired_export

                if not bess_rule_locked:
                    if export_delta > NUMERIC_ZERO_EPSILON:
                        bess_charge, _ = self._bess_manager.allocate_power(
                            bess_cap, requested_charge_kw=export_delta
                        )
                    elif export_delta < -NUMERIC_ZERO_EPSILON:
                        _, bess_discharge = self._bess_manager.allocate_power(
                            bess_cap, requested_discharge_kw=-export_delta
                        )

                net_pcc = pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand
                if net_pcc > max_export:
                    excess = net_pcc - max_export
                    if not bess_rule_locked and bess_discharge == 0.0:
                        add_chg = min(excess, max(0.0, bess_cap.max_charge_kw - bess_charge))
                        bess_charge += add_chg
                        excess -= add_chg
                    if self._config.pv.curtailment_enabled and excess > 0.0:
                        cut_pv = min(excess, pv_cmd)
                        pv_cmd -= cut_pv
                        excess -= cut_pv
                    if not bio_rule_locked and bio_can_gen and excess > 0.0 and bio_cmd > bio_lower:
                        cut_bio = min(excess, bio_cmd - bio_lower)
                        bio_cmd -= cut_bio
                        excess -= cut_bio
                    grid_export = max(0.0, pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand)
                elif net_pcc >= 0.0:
                    grid_export = net_pcc
                else:
                    grid_import = -net_pcc
            else:
                if not bio_rule_locked and bio_can_gen and step_in.biomass_status == BiomassStatus.RUNNING:
                    desired_bio = max(total_demand - pv_cmd, bio_lower)
                    bio_cmd = min(max(desired_bio, bio_lower), bio_upper)

                net_pcc = pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand
                if net_pcc > max_export:
                    excess = net_pcc - max_export
                    if not bess_rule_locked and bess_discharge == 0.0:
                        add_chg = min(excess, max(0.0, bess_cap.max_charge_kw - bess_charge))
                        bess_charge += add_chg
                        excess -= add_chg
                    if self._config.pv.curtailment_enabled and excess > 0.0:
                        cut_pv = min(excess, pv_cmd)
                        pv_cmd -= cut_pv
                        excess -= cut_pv
                    if not bio_rule_locked and bio_can_gen and excess > 0.0 and bio_cmd > bio_lower:
                        cut_bio = min(excess, bio_cmd - bio_lower)
                        bio_cmd -= cut_bio
                        excess -= cut_bio
                    grid_export = max(0.0, pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand)
                elif net_pcc >= 0.0:
                    grid_export = net_pcc
                else:
                    deficit = -net_pcc
                    if not bess_rule_locked and bess_charge == 0.0:
                        add_dis = min(deficit, max(0.0, bess_cap.max_discharge_kw - bess_discharge))
                        bess_discharge += add_dis
                        deficit -= add_dis
                    grid_import = max(0.0, deficit)
        else:
            # Off-grid / islanded load-following
            if not bio_rule_locked and bio_can_gen and step_in.biomass_status == BiomassStatus.RUNNING:
                needed_bio = total_demand - pv_cmd
                bio_cmd = min(max(needed_bio, bio_lower), bio_upper)

            net = pv_cmd + bio_cmd + bess_discharge - bess_charge - total_demand
            if net > NUMERIC_ZERO_EPSILON:
                excess = net
                if not bess_rule_locked and bess_discharge == 0.0:
                    add_chg = min(excess, max(0.0, bess_cap.max_charge_kw - bess_charge))
                    bess_charge += add_chg
                    excess -= add_chg
                if self._config.pv.curtailment_enabled and excess > 0.0:
                    cut_pv = min(excess, pv_cmd)
                    pv_cmd -= cut_pv
                    excess -= cut_pv
                if not bio_rule_locked and bio_can_gen and excess > 0.0 and bio_cmd > bio_lower:
                    cut_bio = min(excess, bio_cmd - bio_lower)
                    bio_cmd -= cut_bio
                    excess -= cut_bio
            elif net < -NUMERIC_ZERO_EPSILON:
                deficit = -net
                if not bess_rule_locked and bess_charge == 0.0:
                    add_dis = min(deficit, max(0.0, bess_cap.max_discharge_kw - bess_discharge))
                    bess_discharge += add_dis
                    deficit -= add_dis
                shed = min(deficit, served_load)
                served_load -= shed
                unmet_load += shed

        pv_curt = max(0.0, step_in.pv_available_kw - pv_cmd)
        trace = f"skipped={skipped_rule_ids}"
        return ProposedDispatch(
            operating_mode=operating_mode,
            sequence_state=sequence_state,
            pv_command_kw=pv_cmd,
            pv_curtailment_kw=pv_curt,
            bess_charge_kw=bess_charge,
            bess_discharge_kw=bess_discharge,
            biomass_command_kw=bio_cmd,
            biomass_start_request=bio_start_req,
            biomass_stop_request=bio_stop_req,
            grid_import_kw=grid_import,
            grid_export_kw=grid_export,
            served_load_kw=served_load,
            unmet_load_kw=unmet_load,
            active_rule_ids=active_rule_ids,
            trace_reason=trace,
        )

    def dispatch_step(
        self,
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
        *,
        supervisor: SafetySupervisor | None = None,
        state: ControllerState | None = None,
        sequence_state: str | None = None,
    ) -> ControllerStepOutput:
        """Propose active-power dispatch and verify it through Module 5 SafetySupervisor."""
        proposed = self.propose(step_in, operating_mode, sequence_state=sequence_state)
        sup = supervisor if supervisor is not None else SafetySupervisor(self._config)
        return sup.supervise(step_in, proposed, state=state)

    def _biomass_envelope(
        self,
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
    ) -> tuple[float, float, bool]:
        bio_cfg = self._config.biomass
        maint_locked = (
            step_in.biomass_maintenance_mode
            or operating_mode == OperatingMode.BIOMASS_MAINTENANCE
        )
        fuel_depleted = (
            step_in.biomass_fuel_remaining_tons is not None
            and step_in.biomass_fuel_remaining_tons <= 0.0
        )
        can_gen = (
            not maint_locked
            and bio_cfg.available
            and not fuel_depleted
            and step_in.biomass_status in {BiomassStatus.RUNNING, BiomassStatus.STOPPING}
        )
        if not can_gen:
            return 0.0, 0.0, False

        bio_upper = min(bio_cfg.operating_max_kw, step_in.biomass_available_kw)
        bio_lower = min(bio_upper, max(bio_cfg.min_load_kw, step_in.biomass_min_load_kw))
        ramp_up_ceil = step_in.biomass_current_power_kw + bio_cfg.max_ramp_up_kw(step_in.dt_seconds)
        ramp_down_floor = max(
            0.0,
            step_in.biomass_current_power_kw - bio_cfg.max_ramp_down_kw(step_in.dt_seconds),
        )

        if step_in.biomass_status == BiomassStatus.STOPPING:
            eff_upper = min(bio_upper, step_in.biomass_current_power_kw)
            eff_lower = min(eff_upper, ramp_down_floor)
            return max(0.0, eff_lower), max(0.0, eff_upper), True

        eff_upper = min(bio_upper, ramp_up_ceil)
        eff_lower = min(eff_upper, max(bio_lower, ramp_down_floor))
        return max(0.0, eff_lower), max(0.0, eff_upper), True
