"""ISO 15118 EVCC State Machine Implementation.

Manages the complete electric vehicle charging lifecycle, integrating with
the hook framework for AI-directed protocol mutation and fuzzing.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from protocols.iso15118_messages import (
    AuthorizationReq,
    CableCheckReq,
    ChargeParameterDiscoveryReq,
    ChargeProgress,
    CurrentDemandReq,
    DCEVChargeParameter,
    EnergyTransferMode,
    PaymentServiceSelectionReq,
    PowerDeliveryReq,
    PreChargeReq,
    ResponseCode,
    SessionSetupReq,
    SessionStopReq,
    SupportedAppProtocolReq,
    V2GMessage,
)
from simulator.hooks import GLOBAL_HOOKS

logger = logging.getLogger("evcc_state_machine")


class EvccState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    SLAC_MATCHING = "SLAC_MATCHING"
    SUPPORTED_APP_PROTOCOL = "SUPPORTED_APP_PROTOCOL"
    SESSION_SETUP = "SESSION_SETUP"
    SERVICE_DISCOVERY = "SERVICE_DISCOVERY"
    PAYMENT_SELECTION = "PAYMENT_SELECTION"
    AUTHORIZATION = "AUTHORIZATION"
    CHARGE_PARAMETERS = "CHARGE_PARAMETERS"
    CABLE_CHECK = "CABLE_CHECK"
    PRE_CHARGE = "PRE_CHARGE"
    POWER_DELIVERY_START = "POWER_DELIVERY_START"
    CURRENT_DEMAND = "CURRENT_DEMAND"
    POWER_DELIVERY_STOP = "POWER_DELIVERY_STOP"
    SESSION_STOP = "SESSION_STOP"
    COMPLETED = "COMPLETED"
    FAULT = "FAULT"


class SessionExchangeRecord:
    """Records a single request-response exchange."""

    def __init__(self, state: EvccState, request: V2GMessage, response: Optional[V2GMessage] = None, error: Optional[str] = None):
        self.state = state
        self.request = request
        self.response = response
        self.error = error


class Iso15118EvccStateMachine:
    """Electric Vehicle Communication Controller (EVCC) State Machine."""

    def __init__(self, evcc_id: str = "020000000001", transport_sender: Optional[Callable[[V2GMessage], V2GMessage]] = None):
        self.evcc_id = evcc_id
        self.session_id = ""
        self.current_state = EvccState.UNINITIALIZED
        self.transport_sender = transport_sender
        self.history: List[SessionExchangeRecord] = []
        self.last_response_code: Optional[ResponseCode] = None
        self.state_override_map: Dict[EvccState, EvccState] = {}

    def transition_to(self, new_state: EvccState) -> None:
        """Transitions state, applying any active state transition mutation hooks."""
        perturbed = GLOBAL_HOOKS.run_state_transition(self.current_state.value, new_state.value)
        target_state = EvccState(perturbed)
        logger.debug(f"State Transition: {self.current_state.value} -> {target_state.value}")
        self.current_state = target_state

    def send_and_receive(self, request: V2GMessage) -> Optional[V2GMessage]:
        """Runs pre_send hook, dispatches via transport sender, runs post_receive hook."""
        # 1. Apply pre_send mutation hook
        mutated_req = GLOBAL_HOOKS.run_pre_send(request, self.current_state.value)

        response: Optional[V2GMessage] = None
        error_msg: Optional[str] = None

        # 2. Transmit via transport
        if self.transport_sender:
            try:
                response = self.transport_sender(mutated_req)
                if hasattr(response, "response_code"):
                    self.last_response_code = getattr(response, "response_code")
                if hasattr(response, "session_id") and getattr(response, "session_id"):
                    self.session_id = getattr(response, "session_id")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Transport error in state {self.current_state.value}: {e}")

        # 3. Apply post_receive hook
        if response:
            GLOBAL_HOOKS.run_post_receive(response, self.current_state.value)

        # 4. Record history
        record = SessionExchangeRecord(self.current_state, mutated_req, response, error_msg)
        self.history.append(record)
        return response

    def run_full_session(self, current_demand_cycles: int = 3) -> bool:
        """Executes a full standard DC fast-charging session handshake."""
        try:
            # 1. Supported App Protocol
            self.transition_to(EvccState.SUPPORTED_APP_PROTOCOL)
            res = self.send_and_receive(SupportedAppProtocolReq())
            if not self._check_ok(res):
                return False

            # 2. Session Setup
            self.transition_to(EvccState.SESSION_SETUP)
            res = self.send_and_receive(SessionSetupReq(evcc_id=self.evcc_id))
            if not self._check_ok(res):
                return False

            # 3. Payment Selection
            self.transition_to(EvccState.PAYMENT_SELECTION)
            res = self.send_and_receive(PaymentServiceSelectionReq(session_id=self.session_id))
            if not self._check_ok(res):
                return False

            # 4. Authorization
            self.transition_to(EvccState.AUTHORIZATION)
            res = self.send_and_receive(AuthorizationReq(session_id=self.session_id))
            if not self._check_ok(res):
                return False

            # 5. Charge Parameter Discovery
            self.transition_to(EvccState.CHARGE_PARAMETERS)
            params = DCEVChargeParameter(
                dc_max_current_limit=350.0,
                dc_max_voltage_limit=900.0,
                dc_target_current=150.0,
                dc_target_voltage=400.0,
            )
            res = self.send_and_receive(ChargeParameterDiscoveryReq(session_id=self.session_id, dc_ev_charge_parameter=params))
            if not self._check_ok(res):
                return False

            # 6. Cable Check
            self.transition_to(EvccState.CABLE_CHECK)
            res = self.send_and_receive(CableCheckReq(session_id=self.session_id))
            if not self._check_ok(res):
                return False

            # 7. PreCharge
            self.transition_to(EvccState.PRE_CHARGE)
            res = self.send_and_receive(PreChargeReq(session_id=self.session_id, ev_target_voltage=400.0, ev_target_current=2.0))
            if not self._check_ok(res):
                return False

            # 8. Power Delivery Start
            self.transition_to(EvccState.POWER_DELIVERY_START)
            res = self.send_and_receive(PowerDeliveryReq(session_id=self.session_id, charge_progress=ChargeProgress.Start))
            if not self._check_ok(res):
                return False

            # 9. Current Demand Loop
            self.transition_to(EvccState.CURRENT_DEMAND)
            for cycle in range(current_demand_cycles):
                soc = 50.0 + cycle * 5.0
                res = self.send_and_receive(
                    CurrentDemandReq(
                        session_id=self.session_id,
                        ev_target_current=150.0,
                        ev_target_voltage=400.0,
                        state_of_charge=soc,
                    )
                )
                if not self._check_ok(res):
                    return False

            # 10. Power Delivery Stop
            self.transition_to(EvccState.POWER_DELIVERY_STOP)
            res = self.send_and_receive(PowerDeliveryReq(session_id=self.session_id, charge_progress=ChargeProgress.Stop))
            if not self._check_ok(res):
                return False

            # 11. Session Stop
            self.transition_to(EvccState.SESSION_STOP)
            res = self.send_and_receive(SessionStopReq(session_id=self.session_id))
            if not self._check_ok(res):
                return False

            self.transition_to(EvccState.COMPLETED)
            return True

        except Exception as e:
            logger.error(f"Fatal session exception in state {self.current_state.value}: {e}")
            self.transition_to(EvccState.FAULT)
            return False

    def _check_ok(self, res: Optional[V2GMessage]) -> bool:
        """Validates that response exists and returned OK status code."""
        if res is None:
            self.transition_to(EvccState.FAULT)
            return False
        code = getattr(res, "response_code", None)
        if code in [ResponseCode.OK, ResponseCode.OK_NewSessionEstablished, ResponseCode.OK_OldSessionJoined]:
            return True
        logger.warning(f"Abnormal ResponseCode encountered: {code}")
        self.transition_to(EvccState.FAULT)
        return False
