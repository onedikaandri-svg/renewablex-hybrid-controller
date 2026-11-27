"""Closed active-power balance and mutual-exclusion verification."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput, ControllerStepOutput
from renewablex_controller.core.units import (
    DEFAULT_POWER_BALANCE_TOLERANCE_KW,
    NUMERIC_ZERO_EPSILON,
)


class PowerBalanceReport(BaseModel):
    """Detailed verification report for closed active-power balance invariants."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    gross_supply_kw: float
    gross_sink_kw: float
    net_supply_kw: float
    net_sink_kw: float
    gross_residual_kw: float
    net_residual_kw: float
    pv_accounting_residual_kw: float
    demand_accounting_residual_kw: float
    bess_mutual_exclusion_ok: bool
    grid_mutual_exclusion_ok: bool
    is_balanced: bool
    violations: list[str] = Field(default_factory=list)


def check_power_balance(
    *,
    local_load_kw: float,
    parasitic_load_kw: float,
    pv_available_kw: float,
    pv_command_kw: float,
    pv_curtailment_kw: float,
    biomass_command_kw: float,
    bess_charge_kw: float,
    bess_discharge_kw: float,
    grid_import_kw: float,
    grid_export_kw: float,
    served_load_kw: float,
    unmet_load_kw: float,
    tolerance_kw: float = DEFAULT_POWER_BALANCE_TOLERANCE_KW,
) -> PowerBalanceReport:
    """Verify gross/net closed active-power balance, accounting, and mutual exclusions."""
    if tolerance_kw <= 0.0:
        raise ValueError(f"tolerance_kw must be > 0, got {tolerance_kw!r}")

    gross_supply_kw = pv_available_kw + biomass_command_kw + bess_discharge_kw + grid_import_kw
    gross_sink_kw = (
        served_load_kw
        + parasitic_load_kw
        + bess_charge_kw
        + grid_export_kw
        + pv_curtailment_kw
    )
    net_supply_kw = pv_command_kw + biomass_command_kw + bess_discharge_kw + grid_import_kw
    net_sink_kw = served_load_kw + parasitic_load_kw + bess_charge_kw + grid_export_kw

    gross_residual_kw = gross_supply_kw - gross_sink_kw
    net_residual_kw = net_supply_kw - net_sink_kw
    pv_accounting_residual_kw = (pv_command_kw + pv_curtailment_kw) - pv_available_kw
    demand_accounting_residual_kw = (served_load_kw + unmet_load_kw) - local_load_kw

    bess_mutual_exclusion_ok = not (
        bess_charge_kw > NUMERIC_ZERO_EPSILON and bess_discharge_kw > NUMERIC_ZERO_EPSILON
    )
    grid_mutual_exclusion_ok = not (
        grid_import_kw > NUMERIC_ZERO_EPSILON and grid_export_kw > NUMERIC_ZERO_EPSILON
    )

    violations: list[str] = []
    if abs(gross_residual_kw) > tolerance_kw or abs(net_residual_kw) > tolerance_kw:
        violations.append("POWER_BALANCE_RESIDUAL_EXCEEDED")
    if abs(pv_accounting_residual_kw) > tolerance_kw:
        violations.append("PV_ACCOUNTING_MISMATCH")
    if abs(demand_accounting_residual_kw) > tolerance_kw:
        violations.append("DEMAND_ACCOUNTING_MISMATCH")
    if not bess_mutual_exclusion_ok:
        violations.append("BESS_SIMULTANEOUS_CHARGE_DISCHARGE")
    if not grid_mutual_exclusion_ok:
        violations.append("GRID_SIMULTANEOUS_IMPORT_EXPORT")

    return PowerBalanceReport(
        gross_supply_kw=gross_supply_kw,
        gross_sink_kw=gross_sink_kw,
        net_supply_kw=net_supply_kw,
        net_sink_kw=net_sink_kw,
        gross_residual_kw=gross_residual_kw,
        net_residual_kw=net_residual_kw,
        pv_accounting_residual_kw=pv_accounting_residual_kw,
        demand_accounting_residual_kw=demand_accounting_residual_kw,
        bess_mutual_exclusion_ok=bess_mutual_exclusion_ok,
        grid_mutual_exclusion_ok=grid_mutual_exclusion_ok,
        is_balanced=len(violations) == 0,
        violations=violations,
    )


def verify_step_output(
    step_in: ControllerStepInput,
    step_out: ControllerStepOutput,
    tolerance_kw: float = DEFAULT_POWER_BALANCE_TOLERANCE_KW,
) -> PowerBalanceReport:
    """Verify closed active-power balance invariants for a ControllerStepOutput."""
    return check_power_balance(
        local_load_kw=step_in.local_load_kw,
        parasitic_load_kw=step_in.parasitic_load_kw,
        pv_available_kw=step_in.pv_available_kw,
        pv_command_kw=step_out.pv_command_kw,
        pv_curtailment_kw=step_out.pv_curtailment_kw,
        biomass_command_kw=step_out.biomass_command_kw,
        bess_charge_kw=step_out.bess_charge_kw,
        bess_discharge_kw=step_out.bess_discharge_kw,
        grid_import_kw=step_out.grid_import_kw,
        grid_export_kw=step_out.grid_export_kw,
        served_load_kw=step_out.served_load_kw,
        unmet_load_kw=step_out.unmet_load_kw,
        tolerance_kw=tolerance_kw,
    )
