"""Error and Anomaly Detection Oracle for EV Fast Chargers.

Monitors behavioral distress signals from the charging station:
  - Abnormal ISO 15118 ResponseCodes
  - CAN bus error frames and emergency shutoff signals
  - Socket resets and protocol timeouts
  - Hardware contactor trips and isolation warnings
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from protocols.can_j1939 import CanFrame, PGN_EVSE_EMERGENCY_STOP
from protocols.iso15118_messages import ResponseCode, V2GMessage

logger = logging.getLogger("error_detector")


class AnomalyEvent:
    def __init__(self, category: str, severity: float, description: str, context: Optional[Dict[str, Any]] = None):
        self.category = category  # "SAFETY", "SEQUENCE", "NETWORK", "CAN"
        self.severity = severity  # 0.0 to 10.0
        self.description = description
        self.context = context or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "context": self.context,
        }


class ChargerErrorDetector:
    """Analyzes runtime interactions and classifies anomalies."""

    def __init__(self):
        self.detected_anomalies: List[AnomalyEvent] = []

    def inspect_v2g_response(self, req: V2GMessage, res: Optional[V2GMessage]) -> Optional[AnomalyEvent]:
        """Classifies V2G response anomalies."""
        if res is None:
            event = AnomalyEvent(
                category="NETWORK",
                severity=8.0,
                description="SECC terminated connection unexpectedly (potential crash or watchdog trip)",
                context={"last_request": type(req).__name__},
            )
            self.detected_anomalies.append(event)
            return event

        code = getattr(res, "response_code", ResponseCode.OK)
        if code == ResponseCode.FAILED_SequenceError:
            event = AnomalyEvent(
                category="SEQUENCE",
                severity=6.0,
                description=f"SECC reported FAILED_SequenceError during {type(req).__name__}",
                context={"request": type(req).__name__, "response_code": str(code)},
            )
            self.detected_anomalies.append(event)
            return event

        elif code == ResponseCode.FAILED_PowerDeliveryNotApplied:
            event = AnomalyEvent(
                category="SAFETY",
                severity=7.5,
                description="Power contactors failed to close or were tripped by safety interlock",
                context={"request": type(req).__name__, "response_code": str(code)},
            )
            self.detected_anomalies.append(event)
            return event

        elif code == ResponseCode.FAILED_WrongChargeParameter:
            event = AnomalyEvent(
                category="SAFETY",
                severity=5.0,
                description="SECC rejected electrical parameters as hazardous or incompatible",
                context={"request": type(req).__name__, "response_code": str(code)},
            )
            self.detected_anomalies.append(event)
            return event

        return None

    def inspect_can_frame(self, frame: CanFrame) -> Optional[AnomalyEvent]:
        """Inspects CAN frame for error or emergency shutoff signals."""
        if frame.is_error_frame:
            event = AnomalyEvent(
                category="CAN",
                severity=7.0,
                description=f"CAN Error Frame detected on bus! ID=0x{frame.arbitration_id:08X}",
                context={"raw_data": frame.data.hex()},
            )
            self.detected_anomalies.append(event)
            return event

        if frame.arbitration_id == PGN_EVSE_EMERGENCY_STOP:
            event = AnomalyEvent(
                category="SAFETY",
                severity=9.5,
                description="Hardware Emergency Stop CAN message received from EVSE controller!",
                context={"raw_data": frame.data.hex()},
            )
            self.detected_anomalies.append(event)
            return event

        return None
