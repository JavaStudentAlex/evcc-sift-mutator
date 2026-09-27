"""Unit Tests for Self-Healing Mutation Validation Loop."""
from typing import Optional
from mutation.self_healing import SelfHealingEngine, SIFT_MUTATION_RETRIES
from mutation.operators import MutationCandidate
from protocols.iso15118_messages import CurrentDemandReq, V2GMessage


def test_self_healing_catches_and_repairs():
    engine = SelfHealingEngine(max_retries=3)
    base_req = CurrentDemandReq(ev_target_voltage=400.0, ev_target_current=150.0)

    attempt_counter = 0

    def faulty_mutator(msg: V2GMessage, last_err: Optional[str]):
        nonlocal attempt_counter
        attempt_counter += 1
        if attempt_counter == 1:
            # First attempt: invalid type (non-V2GMessage object)
            return MutationCandidate(arm="ARM_NUMERICAL_BOUNDARY", description="Broken candidate", mutated_payload="INVALID_STRING")
        else:
            # Repaired attempt
            return MutationCandidate(
                arm="ARM_NUMERICAL_BOUNDARY",
                description="Repaired candidate",
                mutated_payload=CurrentDemandReq(ev_target_voltage=420.0, ev_target_current=160.0),
            )

    winner, was_repaired = engine.execute_with_self_healing(base_req, faulty_mutator)

    assert was_repaired is True
    assert attempt_counter == 2
    assert isinstance(winner.mutated_payload, CurrentDemandReq)
    assert winner.mutated_payload.ev_target_voltage == 420.0


def test_self_healing_fallback_on_exhaustion():
    engine = SelfHealingEngine(max_retries=2)
    base_req = CurrentDemandReq(ev_target_voltage=400.0, ev_target_current=150.0)

    def persistently_broken(msg: V2GMessage, last_err: Optional[str]):
        return MutationCandidate(arm="ARM_TEST", description="Persistent bug", mutated_payload=None)

    winner, was_repaired = engine.execute_with_self_healing(base_req, persistently_broken)

    assert was_repaired is False
    assert winner.arm == "FALLBACK"
    assert winner.mutated_payload == base_req
