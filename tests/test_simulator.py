"""Unit Tests for State Machine and SECC Mock Server."""
from simulator.state_machine import EvccState, Iso15118EvccStateMachine
from simulator.secc_mock import SeccMockServer
from simulator.hooks import GLOBAL_HOOKS
from protocols.iso15118_messages import (
    CableCheckReq,
    ChargeProgress,
    CurrentDemandReq,
    PowerDeliveryReq,
    ResponseCode,
    V2GMessage,
)


def test_nominal_charging_session():
    secc = SeccMockServer()
    sm = Iso15118EvccStateMachine(transport_sender=secc.process_message)

    success = sm.run_full_session(current_demand_cycles=2)
    assert success is True
    assert sm.current_state == EvccState.COMPLETED
    assert len(sm.history) >= 10
    assert secc.cable_checked is True
    assert secc.authorized is True


def test_secc_detects_out_of_order_power_delivery():
    secc = SeccMockServer()
    sm = Iso15118EvccStateMachine(transport_sender=secc.process_message)

    # Intercept CableCheckReq and prematurely send PowerDeliveryReq(Start)
    def premature_power_delivery(msg: V2GMessage, state_name: str) -> V2GMessage:
        if isinstance(msg, CableCheckReq):
            return PowerDeliveryReq(session_id=msg.session_id, charge_progress=ChargeProgress.Start)
        return msg

    GLOBAL_HOOKS.clear()
    GLOBAL_HOOKS.register_pre_send(premature_power_delivery)

    success = sm.run_full_session(current_demand_cycles=1)
    GLOBAL_HOOKS.clear()

    assert success is False
    assert sm.current_state == EvccState.FAULT
    assert any("CRITICAL: Power Delivery Start requested without PreCharge" in a for a in secc.anomalies_detected)
