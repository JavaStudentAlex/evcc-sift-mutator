"""ISO 15118-2 and DIN SPEC 70121 Message Definitions and Schemas.

Implements the standard DC fast-charging message sequence with Pydantic validation,
simulated EXI serialization/deserialization, and semantic field constraints.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound="V2GMessage")


class ResponseCode(str, Enum):
    OK = "OK"
    OK_NewSessionEstablished = "OK_NewSessionEstablished"
    OK_OldSessionJoined = "OK_OldSessionJoined"
    FAILED = "FAILED"
    FAILED_SequenceError = "FAILED_SequenceError"
    FAILED_ServiceIDInvalid = "FAILED_ServiceIDInvalid"
    FAILED_UnknownSession = "FAILED_UnknownSession"
    FAILED_CertificateRevoked = "FAILED_CertificateRevoked"
    FAILED_SignatureError = "FAILED_SignatureError"
    FAILED_PowerDeliveryNotApplied = "FAILED_PowerDeliveryNotApplied"
    FAILED_TariffSelectionInvalid = "FAILED_TariffSelectionInvalid"
    FAILED_WrongChargeParameter = "FAILED_WrongChargeParameter"
    FAILED_EVSEPresentVoltageToLow = "FAILED_EVSEPresentVoltageToLow"


class EnergyTransferMode(str, Enum):
    AC_single_phase_core = "AC_single_phase_core"
    AC_three_phase_core = "AC_three_phase_core"
    DC_core = "DC_core"
    DC_extended = "DC_extended"
    DC_combo_core = "DC_combo_core"
    DC_unique = "DC_unique"


class ChargeProgress(str, Enum):
    Start = "Start"
    Stop = "Stop"
    Renegotiate = "Renegotiate"


from typing import Any, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound="V2GMessage")


class V2GMessage(BaseModel):
    """Base class for all ISO 15118 messages."""
    session_id: Optional[str] = Field(default="", description="Hex-encoded 8-byte session ID")

    def to_exi_payload(self) -> bytes:
        """Serializes message to a simulated EXI bitstream (JSON-wrapped binary)."""
        data_str = self.model_dump_json()
        return data_str.encode("utf-8")

    @classmethod
    def from_exi_payload(cls: Type[T], data: bytes) -> T:
        """Deserializes simulated EXI payload into typed message."""
        data_str = data.decode("utf-8")
        return cls.model_validate_json(data_str)


# ==========================================
# 1. Supported App Protocol Handshake
# ==========================================

class AppProtocolEntry(BaseModel):
    protocol_namespace: str = Field(default="urn:iso:15118:2:2013:MsgDef")
    version_number_major: int = Field(default=2, ge=1)
    version_number_minor: int = Field(default=0, ge=0)
    schema_id: int = Field(default=1, ge=0)
    priority: int = Field(default=1, ge=1)


class SupportedAppProtocolReq(V2GMessage):
    app_protocols: List[AppProtocolEntry] = Field(default_factory=lambda: [AppProtocolEntry()])


class SupportedAppProtocolRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    schema_id: Optional[int] = 1


# ==========================================
# 2. Session Setup
# ==========================================

class SessionSetupReq(V2GMessage):
    evcc_id: str = Field(default="020000000001", min_length=12, max_length=12, description="MAC or unique EVCC identifier")


class SessionSetupRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK_NewSessionEstablished
    evse_id: str = Field(default="DE*EBW*E12345*1")


# ==========================================
# 3. Service Discovery & Payment Selection
# ==========================================

class ServiceDiscoveryReq(V2GMessage):
    service_scope: Optional[str] = None
    service_category: Optional[str] = "EVCharging"


class ServiceDiscoveryRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    payment_options: List[str] = Field(default_factory=lambda: ["Contract", "ExternalPayment"])
    supported_energy_modes: List[EnergyTransferMode] = Field(default_factory=lambda: [EnergyTransferMode.DC_extended])


class PaymentServiceSelectionReq(V2GMessage):
    selected_payment_option: str = "ExternalPayment"
    selected_service_ids: List[int] = Field(default_factory=lambda: [1])


class PaymentServiceSelectionRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK


# ==========================================
# 4. Authorization
# ==========================================

class AuthorizationReq(V2GMessage):
    gen_challenge: Optional[str] = None
    id_token: Optional[str] = Field(default="EMAID123456789")


class AuthorizationRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    evse_processing: str = "Finished"


# ==========================================
# 5. Charge Parameter Discovery (DC Fast Charge)
# ==========================================

class DCEVChargeParameter(BaseModel):
    dc_max_current_limit: float = Field(default=350.0, ge=0.0, le=1000.0, description="Amperes")
    dc_max_voltage_limit: float = Field(default=920.0, ge=0.0, le=1200.0, description="Volts")
    dc_target_current: float = Field(default=150.0, ge=0.0, le=1000.0, description="Amperes")
    dc_target_voltage: float = Field(default=400.0, ge=0.0, le=1200.0, description="Volts")
    dc_min_current_limit: float = Field(default=0.0, ge=0.0)
    dc_min_voltage_limit: float = Field(default=200.0, ge=0.0)
    full_soc: Optional[float] = Field(default=100.0, ge=0.0, le=100.0)
    bulk_soc: Optional[float] = Field(default=80.0, ge=0.0, le=100.0)


class ChargeParameterDiscoveryReq(V2GMessage):
    requested_energy_mode: EnergyTransferMode = EnergyTransferMode.DC_extended
    dc_ev_charge_parameter: DCEVChargeParameter = Field(default_factory=DCEVChargeParameter)


class ChargeParameterDiscoveryRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    evse_max_current_limit: float = 400.0
    evse_max_voltage_limit: float = 1000.0
    evse_min_current_limit: float = 1.0
    evse_min_voltage_limit: float = 200.0
    evse_max_power_limit: float = 350000.0  # 350 kW


# ==========================================
# 6. Cable Check
# ==========================================

class CableCheckReq(V2GMessage):
    pass


class CableCheckRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    evse_processing: str = "Finished"  # "Finished" or "Ongoing"


# ==========================================
# 7. PreCharge
# ==========================================

class PreChargeReq(V2GMessage):
    ev_target_voltage: float = Field(default=400.0, ge=0.0, le=1200.0)
    ev_target_current: float = Field(default=2.0, ge=0.0, le=20.0)


class PreChargeRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    evse_present_voltage: float = 400.0


# ==========================================
# 8. Power Delivery (Relay Closes / Charge Commences)
# ==========================================

class PowerDeliveryReq(V2GMessage):
    charge_progress: ChargeProgress = ChargeProgress.Start
    sa_schedule_tuple_id: int = 1


class PowerDeliveryRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK


# ==========================================
# 9. Current Demand Loop (Active High Power)
# ==========================================

class CurrentDemandReq(V2GMessage):
    ev_target_current: float = Field(default=150.0, description="Requested current (A)")
    ev_target_voltage: float = Field(default=400.0, description="Requested voltage (V)")
    charging_complete: bool = False
    bulk_charging_complete: bool = False
    bulk_soc: float = Field(default=50.0, ge=0.0, le=100.0)
    state_of_charge: float = Field(default=50.0, ge=0.0, le=100.0)
    ev_max_current_limit: float = Field(default=350.0)
    ev_max_voltage_limit: float = Field(default=920.0)
    ev_max_power_limit: float = Field(default=320000.0)


class CurrentDemandRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
    evse_present_voltage: float = 400.0
    evse_present_current: float = 150.0
    evse_status: Dict[str, Any] = Field(default_factory=lambda: {"notification": "None", "isolation_status": "Valid"})


# ==========================================
# 10. Session Stop
# ==========================================

class SessionStopReq(V2GMessage):
    charging_session: str = "Terminate"


class SessionStopRes(V2GMessage):
    response_code: ResponseCode = ResponseCode.OK
