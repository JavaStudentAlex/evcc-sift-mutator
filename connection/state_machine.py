# ---------------------------------------------------------------------------
# Vendored from https://github.com/eMobMarkus/evcc_simulator (commit c884950)
# -- the YFN x EU Energy Hackathon 2026 (EnBW track) reference EVCC connection
# stack, shared by the organizers as an example to build on. Incorporated into
# EVCC-SIFT-Mutator on 2026-09-27 to provide the real hardware/charger
# transport backend for the AI mutation harness.
#
# Integration fixes to finite-session control and response validation are
# applied here. Upstream carried no LICENSE file; see connection/README.md.
# ---------------------------------------------------------------------------

"""Complete DIN SPEC 70121 EVCC session flow.

We deliberately target DIN SPEC 70121 rather than the full ISO 15118-2 --
see the codec.py docstring for the rationale. Runs the standard message
sequence for a DC charging session autonomously. Every message sent/received
passes through the hooks in hooks.py -- that is where you hook in your
manipulation (change a field, flip a bit, delay/drop a message, etc.)
without having to touch the flow control itself.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from connection import codec
from connection.config import EvccConfig
from connection.session import EVCCSession

logger = logging.getLogger(__name__)

# DCEVSEStatusCode values (see iso15118.shared.messages.datatypes) that
# indicate an EVSE error state and should abort the CurrentDemand loop.
EVSE_FAILURE_STATUS_CODES = {
    "EVSE_Shutdown",
    "EVSE_UtilityInterruptEvent",
    "EVSE_EmergencyShutdown",
    "EVSE_Malfunction",
}


@dataclass
class SessionState:
    session_id: str = "00"
    evseid: str = ""
    payment_option: str = "ExternalPayment"
    selected_service_id: int = 1
    # Values taken from a comparison with a real trace (information/real
    # trace with auth.pcap, evaluated via EXI decode): EVMaximumCurrentLimit/
    # -VoltageLimit/-PowerLimit are the vehicle's max. BMS limit values
    # (reported once in ChargeParameterDiscoveryReq, not the actual charging
    # value).
    ev_max_current_limit: float = 600.0
    ev_max_voltage_limit: float = 900.0
    ev_max_power_limit: float = 500_000.0
    # PreChargeReq target values -- kept consistent with dc_target_voltage
    # below (PreCharge is supposed to bring the output close to the later
    # CurrentDemand target voltage before the contactors close).
    precharge_target_voltage: float = 300.0
    precharge_target_current: float = 1.0
    # CurrentDemandReq: TargetCurrent deliberately NOT a ramp (a real trace
    # showed 101A->250A->0A) -- stays constant.
    dc_target_current: float = 50.0
    dc_target_voltage: float = 300.0
    charging_complete: bool = False


class Iso15118EvccStateMachine:
    """Drives the EVCC side of a DIN SPEC 70121 DC session.

    Flow:
      SAP handshake -> SessionSetup -> ServiceDiscovery
      -> ServicePaymentSelection -> ContractAuthentication
      -> ChargeParameterDiscovery -> CableCheck -> PreCharge
      -> PowerDelivery(Start) -> CurrentDemand(Loop)
      -> PowerDelivery(Stop) -> WeldingDetection -> SessionStop
    """

    def __init__(
        self,
        session: EVCCSession,
        cfg: EvccConfig,
        cp_state_callback: Optional[Callable[[bool], None]] = None,
        verbose: bool = False,
    ):
        self.session = session
        self.cfg = cfg
        self.state = SessionState(session_id=cfg.session_id)
        # Called in power_delivery(start) with the start flag so the
        # physical CP state (resistor network, see can_control.py) can be
        # kept in sync with the digital PowerDeliveryReq. Empirically
        # observed: without this, the script keeps the CP permanently at
        # state C (charging requested), even after "Stop" has been signaled
        # digitally -- the charging station then reports "Wrong CP State"
        # and goes into EVSE_Shutdown.
        self._cp_state_callback = cp_state_callback
        # Controls whether _exchange() logs the full field/response payload
        # (useful for debugging) or just the message name + timestamp (the
        # standard log format supplies the timestamp automatically). Queried
        # interactively at script startup, see main.py.
        self.verbose = verbose

    def _exchange(self, name: str, fields: dict, check_response_code: bool = True) -> dict:
        msg = {"name": name, "session_id": self.state.session_id, "fields": fields}
        if self.verbose:
            logger.info("-> %s %s", name, fields)
        else:
            logger.info("-> %s", name)
        self.session.send_message(msg)
        response = self.session.receive_message()
        if self.verbose:
            logger.info("<- %s", response)
        else:
            logger.info("<- %s", response.get("name"))
        expected_name = f"{name.removesuffix('Req')}Res"
        if response.get("name") != expected_name:
            raise RuntimeError(
                f"{name}: expected {expected_name}, received {response.get('name')!r}."
            )
        if check_response_code:
            # ResponseCode is a (str, Enum) member (e.g.
            # ResponseCode.OK_NEW_SESSION_ESTABLISHED). Calling str(...) on
            # it yields "ResponseCode.OK_NEW_SESSION_ESTABLISHED"
            # (Enum.__str__, the qualified name), NOT the actual value
            # "OK_NewSessionEstablished" -- .startswith("OK") would then
            # ALWAYS fail, even on genuine success. Since the member itself
            # is a str subtype, check directly on it (no str() call).
            response_code = response.get("fields", {}).get("ResponseCode")
            if not isinstance(response_code, str) or not response_code.startswith("OK"):
                raise RuntimeError(
                    f"{name}: charging station rejected with ResponseCode='{response_code}' "
                    f"(response: {response})."
                )
        return response

    def _exchange_until_finished(
        self, name: str, fields: dict, max_attempts: int = 60, poll_interval_s: float = 1.0
    ) -> dict:
        """CableCheckRes/ChargeParameterDiscoveryRes carry an EVSEProcessing
        field (Finished/Ongoing) -- per DIN SPEC 70121 the request must be
        repeated as long as the charging station reports "Ongoing" (e.g.
        during the insulation measurement in CableCheck), and the next
        message of the sequence may ONLY be sent after "Finished". Proceeding
        early is rejected by the charging station as FAILED_SequenceError
        (empirically confirmed) -- so strictly wait for "Finished", no
        shortcut based on the data content."""
        for attempt in range(1, max_attempts + 1):
            resp = self._exchange(name, fields)
            processing = resp.get("fields", {}).get("EVSEProcessing")
            if processing == "Finished":
                return resp
            logger.info(
                "%s: EVSEProcessing=%s (attempt %s/%s), retrying...",
                name, processing, attempt, max_attempts,
            )
            if attempt < max_attempts:
                time.sleep(poll_interval_s)
        raise RuntimeError(
            f"{name}: EVSEProcessing did not become 'Finished' after "
            f"{max_attempts} attempts."
        )

    def _dc_ev_status(self) -> dict:
        # EVReady is NOT coupled to "ready to charge"/ReadyToChargeState --
        # confirmed via a real trace (information/real trace with
        # auth.pcap), EVReady stays constant True for the ENTIRE session
        # (CableCheck, PreCharge, PowerDelivery Start AND Stop,
        # WeldingDetection). Previously ev_ready=False was sent during
        # CableCheck/PreCharge/PowerDelivery(Stop)/WeldingDetection, which
        # presumably caused the observed ResponseCode=FAILED responses
        # during WeldingDetection/SessionStop.
        return {
            "EVReady": True,
            "EVErrorCode": "NO_ERROR",
            "EVRESSSOC": 50,
        }

    @staticmethod
    def _physical_value(value: float, multiplier: int = 0) -> dict:
        return {"Value": int(round(value / (10**multiplier))), "Multiplier": multiplier}

    def run_dc_session(self, current_demand_max_cycles: Optional[int] = None) -> None:
        if current_demand_max_cycles is not None and current_demand_max_cycles < 1:
            raise ValueError("current_demand_max_cycles must be positive")
        try:
            self.sap_handshake()
            self.session_setup()
            self.service_discovery()
            self.service_payment_selection()
            self.contract_authentication()
            self.charge_parameter_discovery()
            self.cable_check()
            self.pre_charge()
            self.power_delivery(start=True)
            self.current_demand_loop(max_cycles=current_demand_max_cycles)
            self.power_delivery(start=False)
            try:
                # A station may close TCP after charging completes; only
                # these optional final checks may tolerate that disconnect.
                self.welding_detection()
                self.session_stop()
            except ConnectionError:
                logger.warning(
                    "Charging station already closed the connection during "
                    "WeldingDetection/SessionStop (not fatal, the actual "
                    "charging process was successful)."
                )
        finally:
            if self._cp_state_callback is not None:
                # Take the CP physically back to state B, even if the
                # connection is already gone -- see comment in
                # power_delivery().
                self._cp_state_callback(False)

    # -- einzelne Schritte -------------------------------------------------

    def sap_handshake(self) -> dict:
        """SupportedAppProtocol handshake -- MUST occur as the very first
        message directly after the TCP connect, before the actual
        DIN SPEC 70121 V2G session (SessionSetupReq...) begins. Uses its own
        EXI namespace (urn:iso:15118:2:2010:AppProtocol), hence via
        send_raw()/receive_raw() instead of send_message()/receive_message().
        """
        req_bytes = codec.encode_sap_req()
        logger.info("-> SupportedAppProtocolReq (DIN SPEC 70121 offered)")
        self.session.send_raw(req_bytes)
        res_bytes = self.session.receive_raw()
        sap_res = codec.decode_sap_res(res_bytes)
        if self.verbose:
            logger.info("<- SupportedAppProtocolRes %s", sap_res)
        else:
            logger.info("<- SupportedAppProtocolRes")
        if sap_res.get("ResponseCode", "").startswith("Failed"):
            raise RuntimeError(f"SAP handshake rejected by SECC: {sap_res}")
        return sap_res

    def session_setup(self) -> dict:
        resp = self._exchange("SessionSetupReq", {"EVCCID": self.cfg.evcc_id})
        self.state.session_id = resp.get("session_id", self.state.session_id)
        self.state.evseid = resp.get("fields", {}).get("EVSEID", "")
        return resp

    def service_discovery(self) -> dict:
        # In DIN SPEC 70121, ServiceScope/ServiceCategory must not/should not
        # be set (see body.py docstring).
        return self._exchange("ServiceDiscoveryReq", {})

    def service_payment_selection(self) -> dict:
        return self._exchange(
            "ServicePaymentSelectionReq",
            {
                "SelectedPaymentOption": self.state.payment_option,
                "SelectedServiceList": {
                    "SelectedService": [{"ServiceID": self.state.selected_service_id}]
                },
            },
        )

    def contract_authentication(self) -> dict:
        # In DIN SPEC 70121, GenChallenge/Id should not be used.
        #
        # ContractAuthenticationRes carries (like CableCheckRes/
        # ChargeParameterDiscoveryRes) an optional EVSEProcessing field --
        # this is where user authentication happens (e.g. an RFID tap).
        # Confirmed by comparing two real traces (see information/*.pcap):
        # without prior authentication, another system repeats this request
        # hundreds of times (identical 13-byte EXI message) until the user
        # authenticates -- only then does it proceed to
        # ChargeParameterDiscovery. With prior authentication, a single
        # exchange suffices. So poll for "Finished" exactly like CableCheck/
        # ChargeParameterDiscovery, instead of sending only once.
        return self._exchange_until_finished(
            "ContractAuthenticationReq", {}, max_attempts=600, poll_interval_s=1.0
        )

    def charge_parameter_discovery(self) -> dict:
        resp = self._exchange_until_finished(
            "ChargeParameterDiscoveryReq",
            {
                "EVRequestedEnergyTransferType": "DC_extended",
                "DC_EVChargeParameter": {
                    "DC_EVStatus": self._dc_ev_status(),
                    "EVMaximumCurrentLimit": self._physical_value(
                        self.state.ev_max_current_limit
                    ),
                    "EVMaximumVoltageLimit": self._physical_value(
                        self.state.ev_max_voltage_limit
                    ),
                    "EVMaximumPowerLimit": self._physical_value(
                        self.state.ev_max_power_limit, multiplier=2
                    ),
                },
            },
        )
        return resp

    def cable_check(self) -> dict:
        return self._exchange_until_finished(
            "CableCheckReq", {"DC_EVStatus": self._dc_ev_status()}
        )

    def pre_charge(self) -> dict:
        return self._exchange(
            "PreChargeReq",
            {
                "DC_EVStatus": self._dc_ev_status(),
                "EVTargetVoltage": self._physical_value(self.state.precharge_target_voltage),
                "EVTargetCurrent": self._physical_value(self.state.precharge_target_current),
            },
        )

    def power_delivery(self, start: bool) -> dict:
        if start and self._cp_state_callback is not None:
            # Bring the physical CP state to state C BEFORE the digital
            # message. Deliberately, the fallback to state B on stop does
            # NOT happen here, but only at the very end of run_dc_session()
            # (after WeldingDetection+SessionStop) -- see the comment there:
            # an overly early CP fallback to B presumably made the charging
            # station interpret it as "vehicle disconnecting" and it then
            # answered WeldingDetection/SessionStop with
            # FAILED/EVSEPresentVoltage=0 or a full connection drop.
            self._cp_state_callback(True)
        return self._exchange(
            "PowerDeliveryReq",
            {
                "ReadyToChargeState": start,
                "DC_EVPowerDeliveryParameter": {
                    "DC_EVStatus": self._dc_ev_status(),
                    # Per the real trace: ChargingComplete stays ALWAYS
                    # False in PowerDeliveryReq, even on stop -- the actual
                    # "done charging" signal is ChargingComplete=True on the
                    # last CurrentDemandReq, not this field here.
                    "ChargingComplete": False,
                },
            },
        )

    def current_demand_loop(
        self, interval_s: float = 1.0, max_cycles: Optional[int] = None
    ) -> None:
        """Send a fixed number of cycles for benchmarks, or wait for Enter
        in interactive mode. The final request has ChargingComplete=True."""
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        stop_event = threading.Event()

        def _wait_for_user_stop() -> None:
            try:
                input(
                    "Charging in progress -- press Enter to stop "
                    "(ChargingComplete will then be sent)...\n"
                )
            except EOFError:
                pass
            stop_event.set()

        if max_cycles is None:
            threading.Thread(target=_wait_for_user_stop, daemon=True).start()

        cycle = 0
        while True:
            cycle += 1
            stop_requested = stop_event.is_set() or (
                max_cycles is not None and cycle >= max_cycles
            )
            self.state.charging_complete = stop_requested
            resp = self._exchange(
                "CurrentDemandReq",
                {
                    "DC_EVStatus": self._dc_ev_status(),
                    "EVTargetCurrent": self._physical_value(self.state.dc_target_current),
                    "EVTargetVoltage": self._physical_value(self.state.dc_target_voltage),
                    "ChargingComplete": self.state.charging_complete,
                },
            )
            status_code = (
                resp.get("fields", {}).get("DC_EVSEStatus", {}).get("EVSEStatusCode")
            )
            if status_code in EVSE_FAILURE_STATUS_CODES and (
                not stop_requested or status_code != "EVSE_Shutdown"
            ):
                stage = "after ChargingComplete" if stop_requested else "before charging completed"
                raise RuntimeError(
                    f"EVSE reports error status {status_code!r} {stage}."
                )
            if stop_requested:
                break
            time.sleep(interval_s)

    def welding_detection(self) -> dict:
        """See section 9.4.2.5 in DIN SPEC 70121 -- optional safety check
        AFTER PowerDelivery(Stop): the EVSE checks whether the DC contactors
        have actually opened (not welded shut) before the cable is
        considered safe to unlock. No EVSEProcessing field on the response,
        so a single exchange like PreCharge, no polling.

        Empirically observed: the charging station had already shut down at
        this point (PowerDeliveryRes already with EVSEStatusCode=
        EVSE_Shutdown) and then rejects WeldingDetectionReq with
        ResponseCode=FAILED/EVSEStatusCode=EVSE_NotReady -- as with
        SessionStopReq (see there), this is only an optional check after the
        actual end of the session, so it is not fatal."""
        resp = self._exchange(
            "WeldingDetectionReq",
            {"DC_EVStatus": self._dc_ev_status()},
            check_response_code=False,
        )
        response_code = resp.get("fields", {}).get("ResponseCode", "OK")
        if not response_code.startswith("OK"):
            logger.warning(
                "WeldingDetectionReq: charging station responded with ResponseCode='%s' "
                "(not fatal, the session had already ended at this point).",
                response_code,
            )
        return resp

    def session_stop(self) -> dict:
        # SessionStopReq is the last message of the sequence -- nothing
        # follows after it. Empirically observed: the charging station
        # responded to SessionStopReq with ResponseCode=FAILED after a
        # successful session (PowerDeliveryRes already with
        # EVSEStatusCode=EVSE_Shutdown) -- from its point of view the
        # session had already ended at that point. A FAILED here should not
        # make the otherwise successful charging session appear as a crash,
        # so just log it instead of raising.
        resp = self._exchange("SessionStopReq", {}, check_response_code=False)
        response_code = resp.get("fields", {}).get("ResponseCode", "OK")
        if not response_code.startswith("OK"):
            logger.warning(
                "SessionStopReq: charging station responded with ResponseCode='%s' "
                "(not fatal, the session had already ended at this point).",
                response_code,
            )
        return resp
