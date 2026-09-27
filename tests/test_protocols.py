"""Unit Tests for Protocol Parsing and Encoding."""
from protocols.v2gtp import V2GTPHeader, V2GTPPayloadType, wrap_v2gtp, unwrap_v2gtp
from protocols.iso15118_messages import (
    SupportedAppProtocolReq,
    SessionSetupReq,
    ChargeParameterDiscoveryReq,
    CurrentDemandReq,
    DCEVChargeParameter,
    ResponseCode,
)
from protocols.slac import SlacFrame, SlacMmeType, build_slac_param_req
from protocols.can_j1939 import encode_bms_status, decode_bms_status


def test_v2gtp_packing():
    payload = b"TEST_PAYLOAD"
    packed = wrap_v2gtp(payload, V2GTPPayloadType.EXI_ENCODED_V2G)
    assert len(packed) == 8 + len(payload)

    hdr, extracted = unwrap_v2gtp(packed)
    assert hdr.protocol_version == 0x01
    assert hdr.inverse_version == 0xFE
    assert hdr.payload_type == V2GTPPayloadType.EXI_ENCODED_V2G
    assert extracted == payload


def test_iso15118_serialization_roundtrip():
    req = CurrentDemandReq(
        session_id="1234567890ABCDEF",
        ev_target_current=125.5,
        ev_target_voltage=450.0,
        state_of_charge=65.0,
    )
    exi_bytes = req.to_exi_payload()
    assert len(exi_bytes) > 0

    decoded = CurrentDemandReq.from_exi_payload(exi_bytes)
    assert decoded.session_id == "1234567890ABCDEF"
    assert decoded.ev_target_current == 125.5
    assert decoded.ev_target_voltage == 450.0
    assert decoded.state_of_charge == 65.0


def test_slac_mme_packing():
    run_id = b"\x11\x22\x33\x44\x55\x66\x77\x88"
    frame = build_slac_param_req(run_id)
    raw = frame.pack()
    assert len(raw) >= 28

    unpacked = SlacFrame.unpack(raw)
    assert unpacked is not None
    assert unpacked.mme_type == SlacMmeType.CM_SLAC_PARM_REQ
    assert unpacked.run_id == run_id


def test_can_j1939_codec():
    frame = encode_bms_status(voltage_v=405.5, current_a=150.2, soc_percent=72.0)
    decoded = decode_bms_status(frame)
    assert abs(decoded["voltage_v"] - 405.5) < 0.15
    assert abs(decoded["current_a"] - 150.2) < 0.15
    assert decoded["soc_percent"] == 72.0
