"""Bridge between the AI mutation harness and the real charger connection stack.

This module is the seam that lets the search-based harness (typed
``V2GMessage`` objects, the UCB1 bandit, the SIFT tournament, the oracles) drive
a **real** charging station through the vendored :mod:`connection` stack, instead
of only the in-process :class:`~simulator.secc_mock.SeccMockServer`.

Two message representations meet here:

* **Harness side** — typed ``pydantic`` ``V2GMessage`` objects
  (:mod:`protocols.iso15118_messages`), which the bandit/operators mutate and the
  oracles inspect.
* **Wire side** — the vendored codec's DIN SPEC 70121 dict form
  ``{"name", "session_id", "fields"}`` that :mod:`connection.codec` encodes to
  EXI and :class:`connection.session.EVCCSession` frames over V2GTP/TCP(/TLS).

It provides three things:

``RealChargerTransport``
    A drop-in ``transport_sender(typed_req) -> typed_res`` for the harness's own
    :class:`~simulator.state_machine.Iso15118EvccStateMachine` (the ``ai-fsm``
    hardware driver). Handles the SAP handshake, ``EVSEProcessing`` polling and
    the physical CP B→C callback transparently, so the typed state machine needs
    no changes.

``HardwareFuzzEngine`` + ``AIMutationCodec``
    The default (``simulator``) hardware driver runs the *verified* vendored DIN
    sequencer (:class:`connection.state_machine.Iso15118EvccStateMachine`) — which
    speaks the dict form natively and gets the real-charger sequence exactly
    right (ServiceDiscovery, EVSEProcessing polling, welding detection, …). AI
    intelligence is injected at the **pre-EXI-encode layer**: ``AIMutationCodec``
    wraps the codec, converts a targeted outgoing message to the typed form, lets
    the bandit/operators/SIFT choose a mutation, converts it back and encodes it.

    Per the ``AGENTS.md`` self-healing invariant, a mutated frame that fails EXI
    encoding is **never** put on the wire — the codec falls back to the canonical,
    schema-valid frame. The reward loop is closed on the matching decode.

Everything degrades gracefully: :mod:`connection.codec` is imported lazily
(inside :class:`AIMutationCodec`), so importing this module never spins up the
Java/EXIficient gateway and it stays usable in a fully offline sandbox (the unit
tests exercise the translation with a fake codec/session).
"""
from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

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
    DCEVChargeParameter,
    EnergyTransferMode,
    PaymentServiceSelectionReq,
    PaymentServiceSelectionRes,
    PowerDeliveryReq,
    PowerDeliveryRes,
    PreChargeReq,
    PreChargeRes,
    ResponseCode,
    ServiceDiscoveryReq,
    ServiceDiscoveryRes,
    SessionSetupReq,
    SessionSetupRes,
    SessionStopReq,
    SessionStopRes,
    SupportedAppProtocolReq,
    SupportedAppProtocolRes,
    V2GMessage,
)

logger = logging.getLogger("real_transport")

# DIN SPEC 70121 request message names that carry an EVSEProcessing field and
# must be repeated until the charger reports "Finished" (see the vendored
# connection.state_machine._exchange_until_finished).
PROCESSING_POLL_NAMES: set[str] = {
    "ContractAuthenticationReq",
    "ChargeParameterDiscoveryReq",
    "CableCheckReq",
}

# Default set of DIN request names the AI layer mutates on a real charger.
# CurrentDemandReq is the safe, high-value default: it is the repeated in-loop
# message where the safety-critical vectors live (negative current, overvoltage,
# 100% SoC + max current), and mutating one iteration does not desynchronize the
# earlier one-shot handshake that a real SECC gates progression on. Mutating a
# gating message (PreCharge/ChargeParameterDiscovery) is opt-in because a
# rejected value legitimately ends the session before later states are reached.
DEFAULT_TARGET_REQUEST_NAMES: set[str] = {"CurrentDemandReq"}


# ---------------------------------------------------------------------------
# Physical value + status helpers (mirror connection.state_machine)
# ---------------------------------------------------------------------------

def phys(value: float, multiplier: int = 0) -> dict[str, int]:
    """Encode a float as a DIN PhysicalValue ``{"Value", "Multiplier"}``."""
    return {"Value": round(value / (10 ** multiplier)), "Multiplier": multiplier}


def unphys(pv: dict[str, Any] | None, default: float = 0.0) -> float:
    """Decode a DIN PhysicalValue dict back to a float."""
    if not isinstance(pv, dict) or "Value" not in pv:
        return default
    try:
        return float(pv.get("Value", 0)) * (10 ** int(pv.get("Multiplier", 0)))
    except (TypeError, ValueError):
        return default


def dc_ev_status(soc: int = 50) -> dict[str, Any]:
    """The DC_EVStatus block the vehicle reports (EVReady stays True; see the
    real-trace notes in connection.state_machine._dc_ev_status)."""
    return {"EVReady": True, "EVErrorCode": "NO_ERROR", "EVRESSSOC": int(soc)}


def map_response_code(raw: Any) -> ResponseCode:
    """Map a DIN ResponseCode string onto the harness's typed enum.

    Unknown codes bucket to OK (any ``OK*``) or FAILED, so the oracle and the
    state machine's ``_check_ok`` keep working against firmware that emits codes
    outside the harness's subset.
    """
    s = str(getattr(raw, "value", raw))
    for rc in ResponseCode:
        if rc.value == s:
            return rc
    return ResponseCode.OK if s.startswith("OK") else ResponseCode.FAILED


# ---------------------------------------------------------------------------
# typed  ->  DIN dict  (request encoding)
# ---------------------------------------------------------------------------

def typed_to_din(msg: V2GMessage) -> tuple[str, dict[str, Any]]:
    """Translate a typed request into ``(din_name, fields)`` for codec.encode.

    Raises ``ValueError`` for a message with no DIN mapping.
    ``SupportedAppProtocolReq`` maps to its DIN name but is handled out-of-band
    (the SAP handshake uses a different EXI namespace), never via codec.encode.
    """
    if isinstance(msg, SupportedAppProtocolReq):
        return "SupportedAppProtocolReq", {}
    if isinstance(msg, SessionSetupReq):
        return "SessionSetupReq", {"EVCCID": msg.evcc_id}
    if isinstance(msg, ServiceDiscoveryReq):
        return "ServiceDiscoveryReq", {}
    if isinstance(msg, PaymentServiceSelectionReq):
        return "ServicePaymentSelectionReq", {
            "SelectedPaymentOption": msg.selected_payment_option,
            "SelectedServiceList": {
                "SelectedService": [{"ServiceID": sid} for sid in msg.selected_service_ids]
            },
        }
    if isinstance(msg, AuthorizationReq):
        return "ContractAuthenticationReq", {}
    if isinstance(msg, ChargeParameterDiscoveryReq):
        p = msg.dc_ev_charge_parameter
        return "ChargeParameterDiscoveryReq", {
            "EVRequestedEnergyTransferType": _emode(msg.requested_energy_mode),
            "DC_EVChargeParameter": {
                "DC_EVStatus": dc_ev_status(),
                "EVMaximumCurrentLimit": phys(p.dc_max_current_limit),
                "EVMaximumVoltageLimit": phys(p.dc_max_voltage_limit),
                "EVMaximumPowerLimit": phys(p.dc_max_voltage_limit * p.dc_max_current_limit, 2),
            },
        }
    if isinstance(msg, CableCheckReq):
        return "CableCheckReq", {"DC_EVStatus": dc_ev_status()}
    if isinstance(msg, PreChargeReq):
        return "PreChargeReq", {
            "DC_EVStatus": dc_ev_status(),
            "EVTargetVoltage": phys(msg.ev_target_voltage),
            "EVTargetCurrent": phys(msg.ev_target_current),
        }
    if isinstance(msg, PowerDeliveryReq):
        return "PowerDeliveryReq", {
            "ReadyToChargeState": msg.charge_progress == ChargeProgress.Start,
            "DC_EVPowerDeliveryParameter": {
                "DC_EVStatus": dc_ev_status(),
                "ChargingComplete": False,
            },
        }
    if isinstance(msg, CurrentDemandReq):
        return "CurrentDemandReq", {
            "DC_EVStatus": dc_ev_status(int(msg.state_of_charge)),
            "EVTargetCurrent": phys(msg.ev_target_current),
            "EVTargetVoltage": phys(msg.ev_target_voltage),
            "ChargingComplete": bool(msg.charging_complete),
        }
    if isinstance(msg, SessionStopReq):
        return "SessionStopReq", {}
    raise ValueError(f"No DIN mapping for request type {type(msg).__name__}")


def _emode(mode: Any) -> str:
    return mode.value if isinstance(mode, EnergyTransferMode) else str(mode)


# ---------------------------------------------------------------------------
# DIN dict  ->  typed  (request decoding, for the AI mutation targets only)
# ---------------------------------------------------------------------------

def din_req_to_typed(name: str, fields: dict[str, Any], session_id: str = "00") -> V2GMessage | None:
    """Reverse of :func:`typed_to_din` for the messages the AI layer mutates.

    Returns ``None`` (mutation skipped, canonical frame sent) when the message is
    not a mutation target or its fields fall outside the typed schema's bounds.
    """
    try:
        if name == "CurrentDemandReq":
            soc = fields.get("DC_EVStatus", {}).get("EVRESSSOC", 50)
            return CurrentDemandReq(
                session_id=session_id,
                ev_target_current=unphys(fields.get("EVTargetCurrent")),
                ev_target_voltage=unphys(fields.get("EVTargetVoltage")),
                charging_complete=bool(fields.get("ChargingComplete", False)),
                state_of_charge=float(soc),
            )
        if name == "PreChargeReq":
            return PreChargeReq(
                session_id=session_id,
                ev_target_voltage=unphys(fields.get("EVTargetVoltage")),
                ev_target_current=unphys(fields.get("EVTargetCurrent"), default=1.0),
            )
        if name == "ChargeParameterDiscoveryReq":
            p = fields.get("DC_EVChargeParameter", {})
            return ChargeParameterDiscoveryReq(
                session_id=session_id,
                requested_energy_mode=fields.get("EVRequestedEnergyTransferType", "DC_extended"),
                dc_ev_charge_parameter=DCEVChargeParameter(
                    dc_max_current_limit=unphys(p.get("EVMaximumCurrentLimit"), default=350.0),
                    dc_max_voltage_limit=unphys(p.get("EVMaximumVoltageLimit"), default=920.0),
                ),
            )
    except (ValidationError, AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("din_req_to_typed(%s) skipped: %s", name, exc)
        return None
    return None


def merge_mutated_fields(
    name: str, original: dict[str, Any], mutated: V2GMessage
) -> dict[str, Any]:
    """Keep wire-only DIN fields intact while applying typed mutation fields."""
    fields = copy.deepcopy(original)
    if name == "CurrentDemandReq" and isinstance(mutated, CurrentDemandReq):
        fields["EVTargetCurrent"] = phys(mutated.ev_target_current)
        fields["EVTargetVoltage"] = phys(mutated.ev_target_voltage)
        fields.setdefault("DC_EVStatus", {})["EVRESSSOC"] = int(mutated.state_of_charge)
        fields["ChargingComplete"] = mutated.charging_complete
    elif name == "PreChargeReq" and isinstance(mutated, PreChargeReq):
        fields["EVTargetVoltage"] = phys(mutated.ev_target_voltage)
        fields["EVTargetCurrent"] = phys(mutated.ev_target_current)
    elif name == "ChargeParameterDiscoveryReq" and isinstance(mutated, ChargeParameterDiscoveryReq):
        params = fields.setdefault("DC_EVChargeParameter", {})
        params["EVMaximumCurrentLimit"] = phys(mutated.dc_ev_charge_parameter.dc_max_current_limit)
        params["EVMaximumVoltageLimit"] = phys(mutated.dc_ev_charge_parameter.dc_max_voltage_limit)
    else:
        raise ValueError(f"No mutation mapping for {name} and {type(mutated).__name__}")
    return fields


# ---------------------------------------------------------------------------
# DIN dict  ->  typed  (response decoding)
# ---------------------------------------------------------------------------

def din_res_to_typed(name: str, fields: dict[str, Any], session_id: str = "00") -> V2GMessage:
    """Translate a decoded DIN response into a typed ``*Res`` object.

    Only ``response_code`` and ``session_id`` are load-bearing for the oracle and
    the state machine; other fields are best-effort. Unknown response names fall
    back to a generic carrier that still exposes ``response_code``.
    """
    rc = map_response_code(fields.get("ResponseCode", "OK"))
    if name == "SessionSetupRes":
        return SessionSetupRes(response_code=rc, session_id=session_id, evse_id=fields.get("EVSEID", ""))
    if name == "ServiceDiscoveryRes":
        return ServiceDiscoveryRes(response_code=rc, session_id=session_id)
    if name == "ServicePaymentSelectionRes":
        return PaymentServiceSelectionRes(response_code=rc, session_id=session_id)
    if name == "ContractAuthenticationRes":
        return AuthorizationRes(
            response_code=rc, session_id=session_id,
            evse_processing=fields.get("EVSEProcessing", "Finished"),
        )
    if name == "ChargeParameterDiscoveryRes":
        return ChargeParameterDiscoveryRes(response_code=rc, session_id=session_id)
    if name == "CableCheckRes":
        return CableCheckRes(
            response_code=rc, session_id=session_id,
            evse_processing=fields.get("EVSEProcessing", "Finished"),
        )
    if name == "PreChargeRes":
        return PreChargeRes(
            response_code=rc, session_id=session_id,
            evse_present_voltage=unphys(fields.get("EVSEPresentVoltage"), default=0.0),
        )
    if name == "PowerDeliveryRes":
        return PowerDeliveryRes(response_code=rc, session_id=session_id)
    if name == "CurrentDemandRes":
        return CurrentDemandRes(
            response_code=rc, session_id=session_id,
            evse_present_voltage=unphys(fields.get("EVSEPresentVoltage"), default=0.0),
            evse_present_current=unphys(fields.get("EVSEPresentCurrent"), default=0.0),
            evse_status=fields.get("DC_EVSEStatus", {}) or {},
        )
    if name == "SessionStopRes":
        return SessionStopRes(response_code=rc, session_id=session_id)
    if name == "SupportedAppProtocolRes":
        return SupportedAppProtocolRes(response_code=rc, session_id=session_id)
    # Unknown/unmodeled response -> generic carrier exposing response_code.
    return SupportedAppProtocolRes(response_code=rc, session_id=session_id)


# ---------------------------------------------------------------------------
# RealChargerTransport -- typed transport_sender over the real socket
# ---------------------------------------------------------------------------

class RealChargerTransport:
    """``transport_sender(typed_req) -> typed_res`` backed by a real EVCCSession.

    Intended as the ``transport_sender`` for the harness's own typed state
    machine (the ``ai-fsm`` hardware driver). The session must already be
    connected and constructed with the real ``encode_fn=codec.encode`` /
    ``decode_fn=codec.decode``.

    A dropped connection is surfaced as ``None`` (not an exception), which the
    :class:`~oracles.error_detector.ChargerErrorDetector` classifies as a
    high-severity crash/watchdog anomaly.
    """

    def __init__(
        self,
        session: Any,
        codec_module: Any,
        cp_state_callback: Callable[[bool], None] | None = None,
        verbose: bool = False,
        max_processing_polls: int = 600,
        poll_interval_s: float = 1.0,
    ):
        if not getattr(codec_module, "is_compliant", lambda: True)():
            raise RuntimeError("Real charger transport requires a compliant EXI codec")
        self.session = session
        self.codec = codec_module
        self.cp_state_callback = cp_state_callback
        self.verbose = verbose
        self.max_processing_polls = max_processing_polls
        self.poll_interval_s = poll_interval_s

    def send(self, msg: V2GMessage) -> V2GMessage | None:
        try:
            if isinstance(msg, SupportedAppProtocolReq):
                return self._sap_handshake(msg)

            name, fields = typed_to_din(msg)
            sid = msg.session_id or "00"

            if isinstance(msg, PowerDeliveryReq) and self.cp_state_callback is not None:
                # Keep the physical CP state (resistor network) in sync with the
                # digital PowerDeliveryReq before sending it.
                self.cp_state_callback(msg.charge_progress == ChargeProgress.Start)

            resp = self._exchange(name, sid, fields, poll=name in PROCESSING_POLL_NAMES)
            return din_res_to_typed(resp.get("name", ""), resp.get("fields", {}), resp.get("session_id", sid))
        except ConnectionError as exc:
            logger.warning("SECC closed the connection during %s: %s", type(msg).__name__, exc)
            return None

    def _sap_handshake(self, msg: SupportedAppProtocolReq) -> SupportedAppProtocolRes:
        req_bytes = self.codec.encode_sap_req()
        self.session.send_raw(req_bytes)
        sap = self.codec.decode_sap_res(self.session.receive_raw())
        return SupportedAppProtocolRes(
            response_code=map_response_code(sap.get("ResponseCode", "OK")),
            session_id=msg.session_id or "00",
        )

    def _exchange(self, name: str, sid: str, fields: dict[str, Any], poll: bool) -> dict[str, Any]:
        dmsg = {"name": name, "session_id": sid, "fields": fields}
        self.session.send_message(dmsg)
        resp = self.session.receive_message()
        if not poll:
            return resp
        attempts = 0
        while (
            resp.get("fields", {}).get("EVSEProcessing") == "Ongoing"
            and attempts < self.max_processing_polls
        ):
            attempts += 1
            time.sleep(self.poll_interval_s)
            self.session.send_message(dmsg)
            resp = self.session.receive_message()
        if resp.get("fields", {}).get("EVSEProcessing") == "Ongoing":
            raise TimeoutError(f"{name}: EVSEProcessing remained Ongoing after {attempts} polls")
        return resp


# ---------------------------------------------------------------------------
# HardwareFuzzEngine -- reuses the harness's bandit / self-healing / SIFT
# ---------------------------------------------------------------------------

class HardwareFuzzEngine:
    """Single-shot AI mutation for a live wire.

    Reuses an existing :class:`~procedural_graph.graph_runtime.ProceduralFuzzingGraph`
    so the same UCB1 bandit, self-healing engine, SIFT tournament and reward
    weights apply on hardware as in mock mode (and the final bandit stats include
    the hardware pulls). Unlike ``graph.execute_fuzzing_cycle`` the dispatch is
    split out: :meth:`mutate` selects+generates+heals+judges offline and hands
    back the winner, then :meth:`observe` scores the real response and updates the
    bandit once the charger has actually replied.
    """

    def __init__(self, graph: Any, error_detector: Any):
        from mutation.bandit import MutationArm

        self.graph = graph
        self.error_detector = error_detector
        self.graph.bandit.arms = [
            MutationArm.ARM_NUMERICAL_BOUNDARY,
            MutationArm.ARM_SEMANTIC_SPOOF,
        ]

    def mutate(self, base: V2GMessage) -> tuple[V2GMessage, dict[str, Any]]:
        # Import here to avoid a hard import cycle at module load.
        from mutation.bandit import MutationArm
        from mutation.operators import (
            MutationCandidate,
            mutate_numerical_boundary,
            mutate_semantic_spoof,
        )

        valid_base = self.graph.self_healing.validate_payload(base)
        if not valid_base.is_valid:
            raise ValueError(f"Canonical request is invalid: {valid_base.error_message}")

        # Only CurrentDemandReq has a semantic operator; the other mapped
        # requests have a numerical operator only.
        allowed_arms = (
            self.graph.bandit.arms
            if isinstance(base, CurrentDemandReq)
            else [MutationArm.ARM_NUMERICAL_BOUNDARY]
        )
        if len(allowed_arms) == 1:
            arm = allowed_arms[0]
            self.graph.bandit.total_pulls += 1
        else:
            arm = self.graph.bandit.select_arm()
        pool_size = max(1, int(self.graph.config.get("candidate_pool_size", 3)))

        def gen(b: V2GMessage):
            if arm == MutationArm.ARM_SEMANTIC_SPOOF:
                return mutate_semantic_spoof(b)
            return mutate_numerical_boundary(b)

        validated: list[Any] = []
        repaired: list[bool] = []
        for _ in range(pool_size):
            cand = gen(base)
            # AGENTS.md self-healing invariant: only validated payloads advance.
            if self.graph.self_healing.validate_payload(cand.mutated_payload).is_valid:
                validated.append(cand)
                repaired.append(False)
            else:
                rep, was = self.graph.self_healing.execute_with_self_healing(
                    base, lambda b, err: gen(b)
                )
                if not self.graph.self_healing.validate_payload(rep.mutated_payload).is_valid:
                    rep = MutationCandidate(
                        arm="FALLBACK", description="Invalid repaired request",
                        mutated_payload=base,
                    )
                    was = False
                validated.append(rep)
                repaired.append(was)

        winner, bt_score, _ = self.graph.judge.rank_tournament(validated)
        if not self.graph.self_healing.validate_payload(winner.mutated_payload).is_valid:
            logger.warning("Tournament returned an invalid request; retaining canonical request")
            winner = MutationCandidate(
                arm="FALLBACK", description="Invalid tournament winner",
                mutated_payload=base,
            )
        ctx = {"arm": arm, "repaired": repaired, "winner": winner, "bt_score": bt_score}
        return winner.mutated_payload, ctx

    def observe(self, ctx: dict[str, Any], response: V2GMessage | None) -> tuple[float, dict[str, Any]]:
        reward, meta = self.graph._compute_reward(response, ctx["repaired"])
        self.graph.bandit.update(ctx["arm"], reward)
        self.error_detector.inspect_v2g_response(ctx["winner"].mutated_payload, response)
        return reward, meta


# ---------------------------------------------------------------------------
# AIMutationCodec -- inject AI mutations at the pre-EXI-encode layer
# ---------------------------------------------------------------------------

class AIMutationCodec:
    """Codec facade that mutates targeted requests just before EXI encoding.

    Plugged into an :class:`connection.session.EVCCSession` as its
    ``encode_fn``/``decode_fn`` so the *verified* vendored DIN state machine
    drives the wire while the AI layer still gets to mutate high-value messages.

    Self-healing invariant (``AGENTS.md`` §3B): a mutated frame that raises during
    EXI encoding is dropped and the canonical, schema-valid frame is sent instead
    — an unvalidated/malformed frame never reaches ``transport_sender``.
    """

    def __init__(
        self,
        engine: HardwareFuzzEngine,
        target_request_names: set[str] | None = None,
        enabled: bool = True,
        codec_module: Any = None,
    ):
        if codec_module is None:
            from connection import codec as codec_module  # lazy: spins up EXI gateway
        if not getattr(codec_module, "is_compliant", lambda: True)():
            raise RuntimeError("Hardware mutation requires a compliant EXI codec")
        self._codec = codec_module
        self.engine = engine
        self.targets = (
            set(DEFAULT_TARGET_REQUEST_NAMES)
            if target_request_names is None else set(target_request_names)
        )
        self.enabled = enabled
        self._pending: dict[str, Any] | None = None
        self.mutations_applied = 0
        self.encoding_fallbacks = 0
        self.pending_abandoned = 0
        self.observations: list[dict[str, Any]] = []

    # -- session encode_fn / decode_fn -------------------------------------
    def encode(self, msg: dict[str, Any]) -> bytes:
        if self._pending is not None:
            self.discard_pending()
        name = msg.get("name")
        if (
            not self.enabled or "raw" in msg or name not in self.targets
            or msg.get("fields", {}).get("ChargingComplete") is True
        ):
            return self._codec.encode(msg)

        typed = din_req_to_typed(name, msg.get("fields", {}), msg.get("session_id", "00"))
        if typed is None:
            return self._codec.encode(msg)

        mutated_typed, ctx = self.engine.mutate(typed)
        if ctx["winner"].arm == "FALLBACK":
            return self._codec.encode(msg)
        mutated_msg = copy.deepcopy(msg)
        mutated_msg["fields"] = merge_mutated_fields(name, msg["fields"], mutated_typed)
        if mutated_msg == msg:
            return self._codec.encode(msg)
        try:
            payload = self._codec.encode(mutated_msg)
        except Exception as exc:  # noqa: BLE001 - EXI codecs can raise backend-specific exceptions.
            # Self-healing invariant: never put a malformed frame on the wire.
            logger.warning(
                "Mutated %s failed EXI encoding (%s) -- sending canonical frame instead.",
                name, exc,
            )
            self.encoding_fallbacks += 1
            return self._codec.encode(msg)

        self._pending = {
            "ctx": ctx,
            "request": mutated_msg,
            "expected_name": name[:-3] + "Res",
        }
        self.mutations_applied += 1
        logger.info("  -> AI mutation on %s: Arm=%s | %s", name, ctx["arm"], ctx["winner"].description)
        return payload

    def decode(self, payload: bytes) -> dict[str, Any]:
        try:
            din = self._codec.decode(payload)
        except Exception:
            self.discard_pending(outcome="decode_error")
            raise
        pending = self._pending
        if pending is not None and (
            din.get("name") == pending["expected_name"]
            and din.get("session_id") == pending["request"]["session_id"]
            and "ResponseCode" in din.get("fields", {})
        ):
            self._pending = None
            typed_res = din_res_to_typed(
                din["name"], din.get("fields", {}), din["session_id"]
            )
            reward, meta = self.engine.observe(pending["ctx"], typed_res)
            self._record(pending, din, "response", reward, meta)
            logger.info("  -> Procedural reward=%.2f (%s)", reward, meta.get("outcome"))
        return din

    def _record(
        self, pending: dict[str, Any], response: dict[str, Any] | None,
        outcome: str, reward: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        ctx = pending["ctx"]
        self.observations.append({
            "request": copy.deepcopy(pending["request"]),
            "response": copy.deepcopy(response),
            "outcome": outcome,
            "selected_arm": ctx["arm"],
            "winner_description": ctx["winner"].description,
            "reward": reward,
            "reward_meta": meta or {},
        })

    def discard_pending(self, outcome: str = "local_error") -> None:
        """Clear an unanswered request without attributing a charger failure."""
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self.pending_abandoned += 1
            self._record(pending, None, outcome)

    def flush_crash(self) -> None:
        """Attribute a connection drop to the outstanding mutation.

        Called by the orchestrator when the session raises mid-exchange, so the
        bandit still learns that the pending mutation provoked a teardown
        (``_compute_reward(None, ...)`` yields the emergency-shutdown reward).
        """
        if self._pending is not None:
            pending, self._pending = self._pending, None
            reward, meta = self.engine.observe(pending["ctx"], None)
            self._record(pending, None, "connection_drop", reward, meta)
            logger.info("  -> Connection drop attributed to pending mutation: reward=%.2f", reward)

    # Convenience passthroughs so this object can fully stand in for the codec.
    def encode_sap_req(self, *a: Any, **k: Any) -> bytes:
        return self._codec.encode_sap_req(*a, **k)

    def decode_sap_res(self, payload: bytes) -> dict[str, Any]:
        return self._codec.decode_sap_res(payload)

    def is_compliant(self) -> bool:
        return bool(getattr(self._codec, "is_compliant", lambda: False)())
