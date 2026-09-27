import builtins
import socket
import struct

import pytest

from connection import codec, config, sdp, session as transport
from connection.config import EvccConfig, NetworkConfig
from connection.hooks import HookManager
from connection.state_machine import Iso15118EvccStateMachine


class FakeExchangeSession:
    def __init__(self, response_fields=None):
        self.sent = []
        self.response_fields = response_fields or {}

    def send_message(self, message):
        self.sent.append(message)

    def receive_message(self):
        name = self.sent[-1]["name"].removesuffix("Req") + "Res"
        return {
            "name": name,
            "session_id": "01",
            "fields": {"ResponseCode": "OK", **self.response_fields},
        }


def test_can_config_wizard_uses_packaged_channel_detector(monkeypatch):
    monkeypatch.setattr(
        "connection.can_control.list_available_channels",
        lambda: [{"channel": 3, "interface": "ixxat"}],
    )
    monkeypatch.setattr(builtins, "input", lambda _: "0")

    selected = config._prompt_can_channel()

    assert selected == {"channel": 3, "bustype": "ixxat", "bitrate": 1_000_000}


def test_fixed_current_demand_cycles_do_not_read_stdin(monkeypatch):
    exchange = FakeExchangeSession()
    machine = Iso15118EvccStateMachine(exchange, EvccConfig())
    sleeps = []
    monkeypatch.setattr(builtins, "input", lambda _: pytest.fail("finite loop read stdin"))
    monkeypatch.setattr("connection.state_machine.time.sleep", sleeps.append)

    machine.current_demand_loop(interval_s=0.05, max_cycles=3)

    assert [msg["fields"]["ChargingComplete"] for msg in exchange.sent] == [
        False, False, True,
    ]
    assert sleeps == [0.05, 0.05]


@pytest.mark.parametrize(
    ("status_code", "fails"),
    [
        ("EVSE_Shutdown", False),
        ("EVSE_Malfunction", True),
        ("EVSE_EmergencyShutdown", True),
        ("EVSE_UtilityInterruptEvent", True),
    ],
)
def test_terminal_current_demand_status_distinguishes_shutdown_from_failure(status_code, fails):
    exchange = FakeExchangeSession(
        {"DC_EVSEStatus": {"EVSEStatusCode": status_code}}
    )
    machine = Iso15118EvccStateMachine(exchange, EvccConfig())

    if fails:
        with pytest.raises(RuntimeError, match="after ChargingComplete"):
            machine.current_demand_loop(max_cycles=1)
    else:
        machine.current_demand_loop(max_cycles=1)

    assert exchange.sent[-1]["fields"]["ChargingComplete"] is True


def test_interactive_current_demand_still_accepts_enter(monkeypatch):
    exchange = FakeExchangeSession()
    machine = Iso15118EvccStateMachine(exchange, EvccConfig())
    monkeypatch.setattr(builtins, "input", lambda _: "")

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("connection.state_machine.threading.Thread", ImmediateThread)
    machine.current_demand_loop(max_cycles=None)

    assert len(exchange.sent) == 1
    assert exchange.sent[0]["fields"]["ChargingComplete"] is True


@pytest.mark.parametrize("cycles", [0, -1])
def test_nonpositive_cycle_count_fails_before_session_start(cycles):
    machine = Iso15118EvccStateMachine(FakeExchangeSession(), EvccConfig())
    with pytest.raises(ValueError, match="positive"):
        machine.run_dc_session(current_demand_max_cycles=cycles)


@pytest.mark.parametrize(
    "response",
    [
        {"name": "PowerDeliveryRes", "fields": {"ResponseCode": "OK"}},
        {"name": "SessionSetupRes", "fields": {}},
        {"name": "SessionSetupRes", "fields": {"ResponseCode": "FAILED"}},
    ],
)
def test_exchange_rejects_unexpected_or_failed_response(response):
    exchange = FakeExchangeSession()
    exchange.receive_message = lambda: response
    machine = Iso15118EvccStateMachine(exchange, EvccConfig())
    with pytest.raises(RuntimeError):
        machine.session_setup()


def test_early_evse_failure_propagates_and_restores_cp(monkeypatch):
    exchange = FakeExchangeSession()
    cp_transitions = []
    machine = Iso15118EvccStateMachine(
        exchange, EvccConfig(), cp_state_callback=cp_transitions.append
    )
    monkeypatch.setattr(codec, "encode_sap_req", lambda: b"sap")
    monkeypatch.setattr(codec, "decode_sap_res", lambda _: {"ResponseCode": "OK_Success"})
    exchange.send_raw = lambda _: None
    exchange.receive_raw = lambda: b"sap-res"
    original_receive = exchange.receive_message

    def response():
        msg = original_receive()
        if exchange.sent[-1]["name"] == "CurrentDemandReq":
            msg["fields"]["DC_EVSEStatus"] = {"EVSEStatusCode": "EVSE_Malfunction"}
        else:
            msg["fields"]["EVSEProcessing"] = "Finished"
        return msg

    exchange.receive_message = response
    monkeypatch.setattr("connection.state_machine.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="before charging completed"):
        machine.run_dc_session(current_demand_max_cycles=2)

    assert cp_transitions == [True, False]
    assert [msg["name"] for msg in exchange.sent].count("PowerDeliveryReq") == 1


def test_polling_bound_does_not_sleep_after_last_attempt(monkeypatch):
    exchange = FakeExchangeSession({"EVSEProcessing": "Ongoing"})
    machine = Iso15118EvccStateMachine(exchange, EvccConfig())
    sleeps = []
    monkeypatch.setattr("connection.state_machine.time.sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="2 attempts"):
        machine._exchange_until_finished("CableCheckReq", {}, max_attempts=2)

    assert len(exchange.sent) == 2
    assert sleeps == [1.0]


class FakeSocket:
    def __init__(self, incoming=b"", fail=None, recv_limit=None):
        self.incoming = incoming
        self.fail = fail
        self.recv_limit = recv_limit
        self.closed = False
        self.reads = []
        self.sent = []

    def settimeout(self, value):
        pass

    def bind(self, address):
        if self.fail == "bind":
            raise OSError("bind failed")

    def connect(self, address):
        if self.fail == "connect":
            raise OSError("connect failed")

    def recv(self, count):
        self.reads.append(count)
        if self.recv_limit is not None:
            count = min(count, self.recv_limit)
        chunk, self.incoming = self.incoming[:count], self.incoming[count:]
        return chunk

    def sendall(self, payload):
        if self.fail == "send":
            raise OSError("write failed")
        self.sent.append(payload)

    def close(self):
        self.closed = True


def make_tcp_session():
    return transport.EVCCSession(
        NetworkConfig(interface_index=2, local_link_local_addr="fe80::1", tcp_connect_attempts=1),
        HookManager(),
    )


def test_send_failure_preserves_unknown_delivery_state():
    raw = FakeSocket(fail="send")
    sess = make_tcp_session()
    sess._sock = raw
    sess.encode_fn = lambda _: b"exi"

    with pytest.raises(transport.MessageSendError) as info:
        sess.send_message({"name": "CurrentDemandReq"})

    assert isinstance(info.value.__cause__, OSError)
    assert isinstance(info.value, ConnectionError)
    assert raw.closed is False


def test_fallback_encoder_requires_bytes():
    with pytest.raises(TypeError, match="No EXI codec"):
        transport._passthrough_encode({"raw": "invalid"})


def test_tcp_bind_failure_closes_socket(monkeypatch):
    raw = FakeSocket(fail="bind")
    monkeypatch.setattr(transport.socket, "socket", lambda *args: raw)
    monkeypatch.setattr(
        transport, "discover_secc",
        lambda _: sdp.SDPResult("fe80::2%2", 15118, sdp.SECURITY_NO_TLS, sdp.TRANSPORT_TCP),
    )
    sess = make_tcp_session()

    with pytest.raises(OSError, match="bind failed"):
        sess.connect()

    assert raw.closed
    assert sess._sock is None


def test_tcp_tls_failure_closes_socket(monkeypatch):
    raw = FakeSocket()
    monkeypatch.setattr(transport.socket, "socket", lambda *args: raw)
    monkeypatch.setattr(
        transport, "discover_secc",
        lambda _: sdp.SDPResult("fe80::2%2", 15118, sdp.SECURITY_TLS, sdp.TRANSPORT_TCP),
    )

    class FailingContext:
        def wrap_socket(self, sock):
            raise OSError("TLS failed")

    monkeypatch.setattr(transport.ssl, "SSLContext", lambda *args: FailingContext())

    with pytest.raises(OSError, match="TLS failed"):
        make_tcp_session().connect()
    assert raw.closed


def test_tcp_receive_valid_partial_reads_and_hook():
    raw = FakeSocket(struct.pack("!BBHI", 1, 0xFE, 0x8001, 3) + b"abc", recv_limit=2)
    sess = make_tcp_session()
    sess._sock = raw
    sess.hooks.post_receive.append(lambda payload: payload.upper())

    assert sess.receive_raw() == b"ABC"
    assert raw.reads == [8, 6, 4, 2, 3, 1]


@pytest.mark.parametrize(
    "header",
    [
        struct.pack("!BBHI", 2, 0xFD, 0x8001, 0),
        struct.pack("!BBHI", 1, 0xFE, 0x9001, 0),
        struct.pack("!BBHI", 1, 0xFE, 0x8001, transport.MAX_EXI_PAYLOAD_LEN + 1),
    ],
)
def test_tcp_receive_rejects_bad_frame_before_payload_read(header):
    raw = FakeSocket(header)
    sess = make_tcp_session()
    sess._sock = raw

    with pytest.raises(ValueError):
        sess.receive_raw()
    assert raw.reads == [8]


class FakeUDPSocket(FakeSocket):
    def __init__(self, incoming=b"", fail=None):
        super().__init__(incoming, fail)

    def setsockopt(self, *args):
        pass

    def sendto(self, packet, destination):
        if self.fail == "send":
            raise OSError("send failed")

    def recvfrom(self, count):
        return self.incoming, ("fe80::2", 15118)


@pytest.mark.parametrize("failure", ["bind", "send"])
def test_sdp_closes_socket_on_failure(monkeypatch, failure):
    raw = FakeUDPSocket(fail=failure)
    monkeypatch.setattr(sdp.socket, "socket", lambda *args: raw)

    with pytest.raises(OSError):
        sdp.discover_secc(NetworkConfig(interface_index=2))
    assert raw.closed


def test_sdp_validates_response_and_closes_socket(monkeypatch):
    address = socket.inet_pton(socket.AF_INET6, "fe80::2")
    payload = address + struct.pack("!HBB", 15118, sdp.SECURITY_NO_TLS, sdp.TRANSPORT_TCP)
    raw = FakeUDPSocket(sdp._build_v2gtp(sdp.SDP_RESPONSE_PAYLOAD_TYPE, payload))
    monkeypatch.setattr(sdp.socket, "socket", lambda *args: raw)

    result = sdp.discover_secc(NetworkConfig(interface_index=2))

    assert result.secc_address == "fe80::2%2"
    assert result.secc_port == 15118
    assert raw.closed


@pytest.mark.parametrize(
    "packet",
    [
        sdp._build_v2gtp(sdp.SDP_RESPONSE_PAYLOAD_TYPE, b"short"),
        sdp._build_v2gtp(sdp.SDP_RESPONSE_PAYLOAD_TYPE, b"x" * 20)[:-1],
    ],
)
def test_sdp_rejects_malformed_response(monkeypatch, packet):
    raw = FakeUDPSocket(packet)
    monkeypatch.setattr(sdp.socket, "socket", lambda *args: raw)
    with pytest.raises(ValueError):
        sdp.discover_secc(NetworkConfig(interface_index=2))
    assert raw.closed
