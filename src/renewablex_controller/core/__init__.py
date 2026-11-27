"""Pure-Python supervisory EMS core logic (Modules 1 to 6).

Zero dependencies on web frameworks, databases, UIs, or field protocol libraries.
"""

from renewablex_controller.core.bess_manager import BESSCapability, BESSSupervisoryManager
from renewablex_controller.core.contracts import (
    ControllerState,
    ControllerStepInput,
    ControllerStepOutput,
    ModeTransitionEvent,
    ProposedDispatch,
)
from renewablex_controller.core.dispatch_engine import PowerDispatchEngine
from renewablex_controller.core.ems_rules import (
    MAX_CONDITION_DEPTH,
    SUPPORTED_EMS_SCHEMA_VERSION,
    EMSRule,
    EMSRuleAction,
    EMSRuleEvaluationResult,
    EMSRuleInterpreter,
    EMSRuleSet,
    EMSRuleValidationError,
)
from renewablex_controller.core.plant_model import (
    BESSOperationalConfig,
    BiomassOperationalConfig,
    ControlPolicyConfig,
    GridOperationalConfig,
    PlantMetadataConfig,
    PlantOperationalConfig,
    PVOperationalConfig,
)
from renewablex_controller.core.power_balance import (
    PowerBalanceReport,
    check_power_balance,
    verify_step_output,
)
from renewablex_controller.core.safety_supervisor import SafetySupervisor
from renewablex_controller.core.units import (
    DEFAULT_POWER_BALANCE_TOLERANCE_KW,
    NUMERIC_ZERO_EPSILON,
    SECONDS_PER_HOUR,
    BiomassStatus,
    CommLossAction,
    ConstraintStatus,
    OperatingMode,
    dt_seconds_to_hours,
)

__all__ = [
    "BESSCapability",
    "BESSOperationalConfig",
    "BESSSupervisoryManager",
    "BiomassOperationalConfig",
    "BiomassStatus",
    "CommLossAction",
    "ConstraintStatus",
    "ControlPolicyConfig",
    "ControllerState",
    "ControllerStepInput",
    "ControllerStepOutput",
    "DEFAULT_POWER_BALANCE_TOLERANCE_KW",
    "EMSRule",
    "EMSRuleAction",
    "EMSRuleEvaluationResult",
    "EMSRuleInterpreter",
    "EMSRuleSet",
    "EMSRuleValidationError",
    "GridOperationalConfig",
    "MAX_CONDITION_DEPTH",
    "ModeTransitionEvent",
    "NUMERIC_ZERO_EPSILON",
    "OperatingMode",
    "PVOperationalConfig",
    "PlantMetadataConfig",
    "PlantOperationalConfig",
    "PowerBalanceReport",
    "PowerDispatchEngine",
    "ProposedDispatch",
    "SECONDS_PER_HOUR",
    "SUPPORTED_EMS_SCHEMA_VERSION",
    "SafetySupervisor",
    "check_power_balance",
    "dt_seconds_to_hours",
    "verify_step_output",
]
