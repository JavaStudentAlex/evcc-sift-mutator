"""Offline regression checks for the DIN mutation bridge."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from mutation.bandit import MutationArm
from mutation.operators import MutationCandidate
from procedural_graph.graph_runtime import ProceduralFuzzingGraph
from protocols.iso15118_messages import (
    ChargeParameterDiscoveryReq,
    CurrentDemandReq,
    ResponseCode,
)
from simulator.real_transport import (
    AIMutationCodec,
    HardwareFuzzEngine,
    RealChargerTransport,
    din_req_to_typed,
    map_response_code,
    merge_mutated_fields,
    phys,
)


def request(*, complete=False):
    return {
        "name": "CurrentDemandReq",
        "session_id": "aa",
        "fields": {
            "DC_EVStatus": {"EVReady": True, "EVErrorCode": "NO_ERROR", "EVRESSSOC": 55},
            "EVTargetCurrent": phys(50),
            "EVTargetVoltage": phys(300),
            "ChargingComplete": complete,
            "BulkChargingComplete": False,
            "EVMaximumPowerLimit": phys(500_000, 2),
        },
    }


class FakeCodec:
    def __init__(self):
        self.encoded = []
        self.reject_mutation = False

    def is_compliant(self):
        return True

    def encode(self, msg):
        self.encoded.append(deepcopy(msg))
        if self.reject_mutation and msg.get("fields", {}).get("EVTargetCurrent") == phys(200):
            raise ValueError("out-of-range DIN value")
        return b"exi"

    def decode(self, payload):
        if payload == b"bad":
            raise ValueError("malformed EXI response")
        return deepcopy(payload)


class FakeEngine:
    def __init__(self):
        self.mutations = 0
        self.observed = []

    def mutate(self, base):
        self.mutations += 1
        changed = base.model_copy(update={"ev_target_current": 200.0})
        winner = MutationCandidate(MutationArm.ARM_NUMERICAL_BOUNDARY, "200A", changed)
        return changed, {"arm": winner.arm, "winner": winner, "repaired": [False]}

    def observe(self, ctx, response):
        self.observed.append(response)
        return (10.0 if response is None else 0.5), {"outcome": "observed"}


def response(name="CurrentDemandRes", sid="aa"):
    return {"name": name, "session_id": sid, "fields": {"ResponseCode": "OK"}}


def test_mutated_request_preserves_wire_only_fields_and_cpd_power_limit():
    original = request()
    typed = din_req_to_typed(original["name"], original["fields"], "aa")
    changed = typed.model_copy(update={"ev_target_current": 200.0})
    merged = merge_mutated_fields(original["name"], original["fields"], changed)
    assert merged["EVTargetCurrent"] == phys(200)
    assert merged["EVMaximumPowerLimit"] == original["fields"]["EVMaximumPowerLimit"]
    assert merged["DC_EVStatus"] == original["fields"]["DC_EVStatus"]
    assert original["fields"]["EVTargetCurrent"] == phys(50)

    cpd = {
        "EVRequestedEnergyTransferType": "DC_extended",
        "DC_EVChargeParameter": {
            "DC_EVStatus": {"EVReady": True},
            "EVMaximumCurrentLimit": phys(600),
            "EVMaximumVoltageLimit": phys(900),
            "EVMaximumPowerLimit": phys(500_000, 2),
        },
    }
    typed_cpd = din_req_to_typed("ChargeParameterDiscoveryReq", cpd, "aa")
    assert isinstance(typed_cpd, ChargeParameterDiscoveryReq)
    typed_cpd.dc_ev_charge_parameter.dc_max_current_limit = 999
    merged_cpd = merge_mutated_fields("ChargeParameterDiscoveryReq", cpd, typed_cpd)
    assert merged_cpd["DC_EVChargeParameter"]["EVMaximumCurrentLimit"] == phys(999)
    assert merged_cpd["DC_EVChargeParameter"]["EVMaximumPowerLimit"] == phys(500_000, 2)


def test_codec_skips_terminal_request_and_non_compliant_codec():
    engine = FakeEngine()
    codec = FakeCodec()
    bridge = AIMutationCodec(engine, codec_module=codec)
    terminal = request(complete=True)
    assert bridge.encode(terminal) == b"exi"
    assert codec.encoded == [terminal]
    assert engine.mutations == bridge.mutations_applied == 0
    with pytest.raises(RuntimeError, match="compliant EXI"):
        AIMutationCodec(engine, codec_module=SimpleNamespace(is_compliant=lambda: False))
    with pytest.raises(RuntimeError, match="compliant EXI"):
        RealChargerTransport(None, SimpleNamespace(is_compliant=lambda: False))


def test_empty_targets_disable_mutations_without_changing_default():
    engine = FakeEngine()
    codec = FakeCodec()
    disabled = AIMutationCodec(engine, target_request_names=set(), codec_module=codec)
    assert disabled.encode(request()) == b"exi"
    assert disabled.targets == set()
    assert disabled.mutations_applied == engine.mutations == 0
    assert AIMutationCodec(engine, codec_module=codec).targets == {"CurrentDemandReq"}


def test_codec_observes_only_matching_response_and_records_wire_request():
    engine, codec = FakeEngine(), FakeCodec()
    bridge = AIMutationCodec(engine, codec_module=codec)
    original = request()
    bridge.encode(original)
    assert bridge.mutations_applied == 1
    assert codec.encoded[0]["fields"]["EVTargetCurrent"] == phys(200)
    assert codec.encoded[0]["fields"]["EVMaximumPowerLimit"] == original["fields"]["EVMaximumPowerLimit"]

    assert bridge.decode(response(sid="bb"))["session_id"] == "bb"
    assert bridge.decode(response(name="PreChargeRes"))["name"] == "PreChargeRes"
    assert bridge.decode({"name": "CurrentDemandRes", "session_id": "aa", "fields": {}})["fields"] == {}
    assert not engine.observed
    assert bridge.decode(response())["name"] == "CurrentDemandRes"
    assert len(engine.observed) == 1
    assert engine.observed[0].response_code == ResponseCode.OK
    assert bridge.observations[0]["request"] == codec.encoded[0]
    assert bridge.observations[0]["response"] == response()
    assert bridge.observations[0]["reward"] == 0.5
    assert bridge.observations[0]["selected_arm"] == MutationArm.ARM_NUMERICAL_BOUNDARY
    bridge.flush_crash()
    assert len(bridge.observations) == 1


def test_codec_fallback_and_pending_lifecycle():
    engine, codec = FakeEngine(), FakeCodec()
    bridge = AIMutationCodec(engine, codec_module=codec)
    codec.reject_mutation = True
    original = request()
    assert bridge.encode(original) == b"exi"
    assert codec.encoded[-1] == original
    assert bridge.encoding_fallbacks == 1
    assert bridge.mutations_applied == 0
    bridge.flush_crash()
    assert not engine.observed

    codec.reject_mutation = False
    bridge.encode(original)
    with pytest.raises(ValueError, match="malformed EXI"):
        bridge.decode(b"bad")
    assert bridge.observations[-1]["outcome"] == "decode_error"
    assert bridge.observations[-1]["reward"] is None
    bridge.flush_crash()
    assert not engine.observed

    bridge.encode(original)
    bridge.discard_pending()
    bridge.flush_crash()
    assert bridge.pending_abandoned == 2
    assert bridge.observations[-1]["outcome"] == "local_error"
    assert not engine.observed

    bridge.encode(original)
    bridge.flush_crash()
    assert bridge.observations[-1]["outcome"] == "connection_drop"
    assert bridge.observations[-1]["reward"] == 10.0
    assert engine.observed == [None]
    bridge.flush_crash()
    assert engine.observed == [None]


def test_hardware_engine_uses_only_available_arms_and_validates_winner(monkeypatch):
    graph = ProceduralFuzzingGraph(config={"candidate_pool_size": 1})
    engine = HardwareFuzzEngine(graph, SimpleNamespace(inspect_v2g_response=lambda *_: None))
    assert graph.bandit.arms == [
        MutationArm.ARM_NUMERICAL_BOUNDARY,
        MutationArm.ARM_SEMANTIC_SPOOF,
    ]
    _, ctx = engine.mutate(CurrentDemandReq())
    assert ctx["arm"] == MutationArm.ARM_NUMERICAL_BOUNDARY
    graph.bandit.update(ctx["arm"], 0.0)
    _, ctx = engine.mutate(CurrentDemandReq())
    assert ctx["arm"] == MutationArm.ARM_SEMANTIC_SPOOF

    bad = MutationCandidate("invalid", "bad", object())
    monkeypatch.setattr(graph.judge, "rank_tournament", lambda _: (bad, 0.0, {}))
    base = CurrentDemandReq()
    returned, ctx = engine.mutate(base)
    assert returned == base
    assert ctx["winner"].arm == "FALLBACK"


def test_rejected_candidate_and_failed_repair_fall_back_to_canonical(monkeypatch):
    graph = ProceduralFuzzingGraph(config={"candidate_pool_size": 1})
    engine = HardwareFuzzEngine(graph, SimpleNamespace(inspect_v2g_response=lambda *_: None))
    invalid = MutationCandidate("bad", "invalid", object())
    monkeypatch.setattr("mutation.operators.mutate_numerical_boundary", lambda _: invalid)
    monkeypatch.setattr(
        graph.self_healing, "execute_with_self_healing", lambda base, generate: (invalid, False)
    )
    codec = FakeCodec()
    bridge = AIMutationCodec(engine, codec_module=codec)
    canonical = request()
    bridge.encode(canonical)
    assert codec.encoded == [canonical]
    assert bridge.mutations_applied == 0
    assert bridge.observations == []


def test_enum_response_code_and_processing_timeout(monkeypatch):
    assert map_response_code(ResponseCode.OK_NewSessionEstablished) == ResponseCode.OK_NewSessionEstablished
    session = SimpleNamespace(
        send_message=lambda msg: None,
        receive_message=lambda: {"fields": {"EVSEProcessing": "Ongoing"}},
    )
    transport = RealChargerTransport(
        session, FakeCodec(), max_processing_polls=2, poll_interval_s=0
    )
    monkeypatch.setattr("simulator.real_transport.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="after 2 polls"):
        transport._exchange("CableCheckReq", "aa", {}, poll=True)
