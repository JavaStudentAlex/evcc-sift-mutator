"""Mock SECC (Supply Equipment Communication Controller) Fast-Charger Server.

Provides a 100% offline, local simulation of a commercial CCS2 DC fast-charging station.
Validates protocol compliance, enforces safety invariants, manages virtual hardware
contactors, and emits realistic ResponseCodes and distress signals for fuzzing tests.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from protocols.iso15118_messages import (
    AuthorizationReq,
    AuthorizationRes,
    CableCheckReq,
    CableCheckRes,
    ChargeParameterDiscoveryReq,
    ChargeParameterDiscoveryRes,
    ChargeProgress,
    CurrentDemandReq,
    CurrentDemandRes,
    PaymentServiceSelectionReq,
    PaymentServiceSelectionRes,
    PowerDeliveryReq,
    PowerDeliveryRes,
    PreChargeReq,
    PreChargeRes,
    ResponseCode,
    SessionSetupReq,
    SessionSetupRes,
    SessionStopReq,
    SessionStopRes,
    SupportedAppProtocolReq,
    SupportedAppProtocolRes,
    V2GMessage,
)

logger = logging.getLogger("secc_mock")


class SeccMockServer:
    """Simulated DC Fast Charging Station Controller."""

    MAX_VOLTAGE = 1000.0  # 1000 Volts
    MAX_CURRENT = 400.0   # 400 Amperes
    MAX_POWER = 350000.0  # 350 kW

    def __init__(self, evse_id: str = "DE*EBW*E12345*01"):
        self.evse_id = evse_id
        self.active_session_id: Optional[str] = None
        self.contactor_closed: bool = False
        self.cable_checked: bool = False
        self.authorized: bool = False
        self.precharged: bool = False
        self.emergency_stop: bool = False
        self.isolation_fault: bool = False
        self.anomalies_detected: List[str] = []
        self.message_history: List[V2GMessage] = []

    def reset(self) -> None:
        """Resets the charging station to idle standby."""
        self.active_session_id = None
        self.contactor_closed = False
        self.cable_checked = False
        self.authorized = False
        self.precharged = False
        self.emergency_stop = False
        self.isolation_fault = False
        self.anomalies_detected.clear()
        self.message_history.clear()

    def process_message(self, msg: V2GMessage) -> V2GMessage:
        """Processes an incoming V2G request and returns appropriate V2G response."""
        self.message_history.append(msg)
        sid = self.active_session_id or ""

        if self.emergency_stop:
            return CurrentDemandRes(
                response_code=ResponseCode.FAILED,
                session_id=sid,
                evse_present_voltage=0.0,
                evse_present_current=0.0,
                evse_status={"notification": "EmergencyShutdown", "isolation_status": "Fault"},
            )

        # 1. Supported App Protocol
        if isinstance(msg, SupportedAppProtocolReq):
            return SupportedAppProtocolRes(response_code=ResponseCode.OK, schema_id=1, session_id=sid)

        # 2. Session Setup
        if isinstance(msg, SessionSetupReq):
            new_id = uuid.uuid4().hex[:16].upper()
            self.active_session_id = new_id
            return SessionSetupRes(response_code=ResponseCode.OK_NewSessionEstablished, session_id=new_id, evse_id=self.evse_id)

        # Session ID verification
        if msg.session_id != self.active_session_id:
            self.anomalies_detected.append(f"Invalid Session ID: {msg.session_id} != {self.active_session_id}")
            return SupportedAppProtocolRes(response_code=ResponseCode.FAILED_UnknownSession, session_id=sid)

        # 3. Payment Selection
        if isinstance(msg, PaymentServiceSelectionReq):
            return PaymentServiceSelectionRes(response_code=ResponseCode.OK, session_id=sid)

        # 4. Authorization
        if isinstance(msg, AuthorizationReq):
            self.authorized = True
            return AuthorizationRes(response_code=ResponseCode.OK, session_id=sid)

        # 5. Charge Parameter Discovery
        if isinstance(msg, ChargeParameterDiscoveryReq):
            if not self.authorized:
                self.anomalies_detected.append("ChargeParameterDiscovery before Authorization")
                return ChargeParameterDiscoveryRes(response_code=ResponseCode.FAILED_SequenceError, session_id=sid)

            params = msg.dc_ev_charge_parameter
            if params.dc_max_voltage_limit > 1200.0 or params.dc_max_current_limit > 1000.0:
                self.anomalies_detected.append(f"Extreme charge parameters requested: V={params.dc_max_voltage_limit}, I={params.dc_max_current_limit}")
                return ChargeParameterDiscoveryRes(response_code=ResponseCode.FAILED_WrongChargeParameter, session_id=sid)

            return ChargeParameterDiscoveryRes(
                response_code=ResponseCode.OK,
                session_id=sid,
                evse_max_voltage_limit=self.MAX_VOLTAGE,
                evse_max_current_limit=self.MAX_CURRENT,
                evse_max_power_limit=self.MAX_POWER,
            )

        # 6. Cable Check
        if isinstance(msg, CableCheckReq):
            self.cable_checked = True
            return CableCheckRes(response_code=ResponseCode.OK, session_id=sid, evse_processing="Finished")

        # 7. PreCharge
        if isinstance(msg, PreChargeReq):
            if not self.cable_checked:
                self.anomalies_detected.append("CRITICAL: PreCharge requested without prior Cable Check!")
                self.emergency_stop = True
                return PreChargeRes(response_code=ResponseCode.FAILED_SequenceError, session_id=sid)

            if msg.ev_target_voltage < 0.0 or msg.ev_target_voltage > self.MAX_VOLTAGE:
                self.anomalies_detected.append(f"PreCharge voltage out of bounds: {msg.ev_target_voltage}V")
                return PreChargeRes(response_code=ResponseCode.FAILED_WrongChargeParameter, session_id=sid)

            self.precharged = True
            return PreChargeRes(response_code=ResponseCode.OK, session_id=sid, evse_present_voltage=msg.ev_target_voltage)

        # 8. Power Delivery
        if isinstance(msg, PowerDeliveryReq):
            if msg.charge_progress == ChargeProgress.Start:
                if not self.precharged:
                    self.anomalies_detected.append("CRITICAL: Power Delivery Start requested without PreCharge!")
                    self.emergency_stop = True
                    return PowerDeliveryRes(response_code=ResponseCode.FAILED_PowerDeliveryNotApplied, session_id=sid)
                self.contactor_closed = True
                return PowerDeliveryRes(response_code=ResponseCode.OK, session_id=sid)
            elif msg.charge_progress == ChargeProgress.Stop:
                self.contactor_closed = False
                return PowerDeliveryRes(response_code=ResponseCode.OK, session_id=sid)

        # 9. Current Demand
        if isinstance(msg, CurrentDemandReq):
            if not self.contactor_closed:
                self.anomalies_detected.append("CRITICAL: CurrentDemand requested while power contactor is OPEN!")
                return CurrentDemandRes(
                    response_code=ResponseCode.FAILED_SequenceError,
                    session_id=sid,
                    evse_status={"notification": "ContactorsOpenError"},
                )

            # Safety bounds check
            if msg.ev_target_voltage > self.MAX_VOLTAGE or msg.ev_target_current > self.MAX_CURRENT:
                self.anomalies_detected.append(
                    f"SAFETY HAZARD: Requested power exceeds SECC rating! V={msg.ev_target_voltage}V (Max {self.MAX_VOLTAGE}V), I={msg.ev_target_current}A (Max {self.MAX_CURRENT}A)"
                )
                self.emergency_stop = True
                return CurrentDemandRes(
                    response_code=ResponseCode.FAILED_WrongChargeParameter,
                    session_id=sid,
                    evse_status={"notification": "HardwareProtectionTrip"},
                )

            if msg.ev_target_voltage < 0 or msg.ev_target_current < 0:
                self.anomalies_detected.append(f"NEGATIVE POWER INJECTION: Target V={msg.ev_target_voltage}, Target I={msg.ev_target_current}")
                self.emergency_stop = True
                return CurrentDemandRes(
                    response_code=ResponseCode.FAILED_WrongChargeParameter,
                    session_id=sid,
                    evse_status={"notification": "NegativeCurrentViolation"},
                )

            return CurrentDemandRes(
                response_code=ResponseCode.OK,
                session_id=sid,
                evse_present_voltage=msg.ev_target_voltage,
                evse_present_current=msg.ev_target_current,
            )

        # 10. Session Stop
        if isinstance(msg, SessionStopReq):
            self.contactor_closed = False
            self.active_session_id = None
            return SessionStopRes(response_code=ResponseCode.OK, session_id=sid)

        return SupportedAppProtocolRes(response_code=ResponseCode.FAILED, session_id=sid)
