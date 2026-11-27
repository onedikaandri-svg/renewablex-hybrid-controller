"""Protocol-specific device adapters (kept isolated from pure-Python core)."""

from renewablex_controller.adapters.simulated_modbus import (
    DryRunCommandSink,
    ModbusRegisterSpec,
    SimulatedModbusAdapter,
    SimulatedModbusDevice,
    default_register_map,
)

__all__ = [
    "DryRunCommandSink",
    "ModbusRegisterSpec",
    "SimulatedModbusAdapter",
    "SimulatedModbusDevice",
    "default_register_map",
]
