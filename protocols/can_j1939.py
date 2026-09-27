"""CAN Bus (SAE J1939 / ISO 11898) Frame Codec for EV Charging Controllers.

Implements CAN telemetry frames exchanged between the Battery Management System (BMS),
the Vehicle Communication Controller (EVCC), and the Supply Equipment Controller (SECC).
Compatible with SocketCAN and IXXAT USB-to-CAN hardware.
"""
from __future__ import annotations

import struct
from typing import Dict, Optional, Tuple


class CanFrame:
    """Represents a standard or extended CAN frame."""

    def __init__(
        self,
        arbitration_id: int,
        data: bytes,
        is_extended_id: bool = True,
        is_error_frame: bool = False,
        timestamp: float = 0.0,
    ):
        self.arbitration_id = arbitration_id
        self.data = data[:8]  # CAN 2.0B max 8 bytes
        self.dlc = len(self.data)
        self.is_extended_id = is_extended_id
        self.is_error_frame = is_error_frame
        self.timestamp = timestamp

    def __repr__(self) -> str:
        data_hex = " ".join(f"{b:02X}" for b in self.data)
        return f"<CanFrame ID=0x{self.arbitration_id:08X} DLC={self.dlc} DATA=[{data_hex}]>"


# ==========================================
# J1939 / EV Charging Parameter Group Numbers (PGNs)
# ==========================================

PGN_BMS_CHARGING_LIMITS = 0x1806E5F4
PGN_BMS_STATUS = 0x1807E5F4
PGN_EVSE_STATUS = 0x1808F4E5
PGN_EVSE_EMERGENCY_STOP = 0x1809F4E5


def encode_bms_charging_limits(max_voltage_v: float, max_current_a: float, temp_c: float) -> CanFrame:
    """Encodes BMS Maximum Charging Limits: Voltage (0.1V/bit), Current (0.1A/bit), Temp (-40C offset)."""
    v_raw = min(65535, max(0, int(max_voltage_v * 10)))
    i_raw = min(65535, max(0, int(max_current_a * 10)))
    t_raw = min(255, max(0, int(temp_c + 40)))
    payload = struct.pack("<HHBxxx", v_raw, i_raw, t_raw)
    return CanFrame(arbitration_id=PGN_BMS_CHARGING_LIMITS, data=payload)


def decode_bms_charging_limits(frame: CanFrame) -> Dict[str, float]:
    """Decodes BMS Maximum Charging Limits."""
    v_raw, i_raw, t_raw = struct.unpack("<HHBxxx", frame.data[:8])
    return {
        "max_voltage_v": v_raw * 0.1,
        "max_current_a": i_raw * 0.1,
        "temp_c": t_raw - 40,
    }


def encode_bms_status(voltage_v: float, current_a: float, soc_percent: float) -> CanFrame:
    """Encodes Real-time BMS Status: Voltage (0.1V/bit), Current (0.1A/bit signed), SoC (1%/bit)."""
    v_raw = min(65535, max(0, int(voltage_v * 10)))
    i_raw = min(32767, max(-32768, int(current_a * 10)))
    soc_raw = min(100, max(0, int(soc_percent)))
    payload = struct.pack("<HhBxxx", v_raw, i_raw, soc_raw)
    return CanFrame(arbitration_id=PGN_BMS_STATUS, data=payload)


def decode_bms_status(frame: CanFrame) -> Dict[str, float]:
    """Decodes Real-time BMS Status."""
    v_raw, i_raw, soc_raw = struct.unpack("<HhBxxx", frame.data[:8])
    return {
        "voltage_v": v_raw * 0.1,
        "current_a": i_raw * 0.1,
        "soc_percent": float(soc_raw),
    }


def encode_emergency_stop(reason_code: int = 0x01) -> CanFrame:
    """Encodes Emergency Hardware Shutdown CAN signal."""
    payload = struct.pack("!BBxxxxxx", 0xFF, reason_code)
    return CanFrame(arbitration_id=PGN_EVSE_EMERGENCY_STOP, data=payload)
