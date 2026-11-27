"""Shared unit constants, standard status enums, and time-step conversions."""

from __future__ import annotations

import math
from enum import StrEnum

SECONDS_PER_HOUR: float = 3600.0
DEFAULT_POWER_BALANCE_TOLERANCE_KW: float = 1e-6
NUMERIC_ZERO_EPSILON: float = 1e-12


class BiomassStatus(StrEnum):
    """Standard biomass operational statuses aligned with RenewableX."""

    OFF = "off"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    OUTAGE = "outage"


class OperatingMode(StrEnum):
    """Deterministic hybrid plant supervisory operating modes."""

    GRID_CONNECTED_NORMAL = "grid_connected_normal"
    PEAK_CUT = "peak_cut"
    MOMENTARY_OUTAGE = "momentary_outage"
    PROLONGED_OUTAGE = "prolonged_outage"
    ISLANDED = "islanded"
    BIOMASS_MAINTENANCE = "biomass_maintenance"
    GRID_RESTORATION = "grid_restoration"
    EMERGENCY_FAULT = "emergency_fault"


class ConstraintStatus(StrEnum):
    """Module 5 supervision verdict for a control step."""

    OK = "ok"
    CLAMPED = "clamped"
    INFEASIBLE = "infeasible"
    REJECTED = "rejected"


class CommLossAction(StrEnum):
    """Configured controller behavior when telemetry is unhealthy or stale."""

    HOLD_SAFE = "hold_safe"
    EMERGENCY_FAULT = "emergency_fault"


def dt_seconds_to_hours(dt_seconds: float) -> float:
    """Convert a strictly positive finite step duration in seconds to hours."""
    if not math.isfinite(dt_seconds) or dt_seconds <= 0.0:
        raise ValueError(f"dt_seconds must be finite and > 0, got {dt_seconds!r}")
    return dt_seconds / SECONDS_PER_HOUR
