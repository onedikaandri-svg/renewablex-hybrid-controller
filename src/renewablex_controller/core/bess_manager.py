"""Module 4: BESS Supervisory Manager for power, energy, and reserve bounds."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from renewablex_controller.core.contracts import ControllerStepInput
from renewablex_controller.core.plant_model import BESSOperationalConfig
from renewablex_controller.core.units import (
    NUMERIC_ZERO_EPSILON,
    OperatingMode,
    dt_seconds_to_hours,
)

_RESERVE_DRAWDOWN_MODES = frozenset(
    {
        OperatingMode.PROLONGED_OUTAGE,
        OperatingMode.ISLANDED,
        OperatingMode.BIOMASS_MAINTENANCE,
    }
)


class BESSCapability(BaseModel):
    """Effective BESS charge/discharge power limits for a single control step."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    available: bool
    soc_fraction: float = Field(ge=0.0, le=1.0)
    active_soc_floor_fraction: float = Field(ge=0.0, le=1.0)
    soc_max_fraction: float = Field(ge=0.0, le=1.0)
    max_charge_kw: float = Field(ge=0.0)
    max_discharge_kw: float = Field(ge=0.0)
    reserve_protected: bool


class BESSSupervisoryManager:
    """Computes mode-aware BESS power envelopes and projects step SoC transitions."""

    def __init__(self, config: BESSOperationalConfig) -> None:
        self._config = config

    @property
    def config(self) -> BESSOperationalConfig:
        """Validated BESS operational configuration."""
        return self._config

    def active_soc_floor(self, operating_mode: OperatingMode) -> tuple[float, bool]:
        """Return (active_soc_floor_fraction, reserve_protected) for the mode."""
        if (
            operating_mode in _RESERVE_DRAWDOWN_MODES
            and self._config.allow_reserve_drawdown_in_islanded
        ):
            return self._config.soc_min_fraction, False
        return self._config.reserve_soc_fraction, True

    def compute_capability(
        self,
        step_in: ControllerStepInput,
        operating_mode: OperatingMode,
    ) -> BESSCapability:
        """Compute step charge/discharge capability respecting PCS and SoC bounds."""
        soc_floor, reserve_protected = self.active_soc_floor(operating_mode)
        available = self._config.available and self._config.effective_capacity_kwh > 0.0

        if not available or operating_mode == OperatingMode.EMERGENCY_FAULT:
            return BESSCapability(
                available=False,
                soc_fraction=step_in.bess_soc_fraction,
                active_soc_floor_fraction=soc_floor,
                soc_max_fraction=self._config.soc_max_fraction,
                max_charge_kw=0.0,
                max_discharge_kw=0.0,
                reserve_protected=reserve_protected,
            )

        max_charge = min(
            step_in.bess_max_charge_kw,
            self._config.max_charge_kw_for_step(step_in.bess_soc_fraction, step_in.dt_seconds),
        )
        max_discharge = min(
            step_in.bess_max_discharge_kw,
            self._config.max_discharge_kw_for_step(
                step_in.bess_soc_fraction,
                step_in.dt_seconds,
                soc_floor_fraction=soc_floor,
            ),
        )

        return BESSCapability(
            available=True,
            soc_fraction=step_in.bess_soc_fraction,
            active_soc_floor_fraction=soc_floor,
            soc_max_fraction=self._config.soc_max_fraction,
            max_charge_kw=max(0.0, max_charge),
            max_discharge_kw=max(0.0, max_discharge),
            reserve_protected=reserve_protected,
        )

    def allocate_power(
        self,
        capability: BESSCapability,
        *,
        requested_charge_kw: float = 0.0,
        requested_discharge_kw: float = 0.0,
    ) -> tuple[float, float]:
        """Clamp non-negative charge/discharge requests within step capability."""
        if requested_charge_kw < 0.0 or requested_discharge_kw < 0.0:
            raise ValueError("BESS power requests must be non-negative")
        if not capability.available:
            return 0.0, 0.0

        net_charge = requested_charge_kw - requested_discharge_kw
        if net_charge > NUMERIC_ZERO_EPSILON:
            return min(net_charge, capability.max_charge_kw), 0.0
        if net_charge < -NUMERIC_ZERO_EPSILON:
            return 0.0, min(-net_charge, capability.max_discharge_kw)
        return 0.0, 0.0

    def project_next_soc(
        self,
        soc_fraction: float,
        *,
        charge_kw: float,
        discharge_kw: float,
        dt_seconds: float,
    ) -> float:
        """Project next SoC fraction after applying non-negative charge/discharge."""
        if not (0.0 <= soc_fraction <= 1.0):
            raise ValueError(f"soc_fraction must be in [0, 1], got {soc_fraction!r}")
        if charge_kw < 0.0 or discharge_kw < 0.0:
            raise ValueError("charge_kw and discharge_kw must be non-negative")
        if charge_kw > NUMERIC_ZERO_EPSILON and discharge_kw > NUMERIC_ZERO_EPSILON:
            raise ValueError("charge_kw and discharge_kw cannot both be > 0")

        dt_hours = dt_seconds_to_hours(dt_seconds)
        if self._config.effective_capacity_kwh <= 0.0:
            return soc_fraction

        energy_in_kwh = charge_kw * dt_hours * self._config.charge_efficiency
        energy_out_kwh = (discharge_kw * dt_hours) / self._config.discharge_efficiency
        next_soc = soc_fraction + (
            (energy_in_kwh - energy_out_kwh) / self._config.effective_capacity_kwh
        )
        return min(1.0, max(0.0, next_soc))
