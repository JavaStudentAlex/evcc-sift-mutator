# ---------------------------------------------------------------------------
# Vendored from https://github.com/eMobMarkus/evcc_simulator (commit c884950)
# -- the YFN x EU Energy Hackathon 2026 (EnBW track) reference EVCC connection
# stack, shared by the organizers as an example to build on. Incorporated into
# EVCC-SIFT-Mutator on 2026-09-27 to provide the real hardware/charger
# transport backend for the AI mutation harness.
#
# Integration fixes to connection cleanup and V2GTP validation are applied
# here. Upstream carried no LICENSE file; see connection/README.md.
# ---------------------------------------------------------------------------

"""V2GTP/TCP transport for the EVCC session, including manipulation hooks.

This class only models the transport layer (V2GTP framing over TCP/TLS).
The actual EXI encoding/decoding of the V2G messages (SessionSetupReq,
ServiceDiscoveryReq, ChargeParameterDiscoveryReq, PowerDeliveryReq, ...)
is deliberately NOT reimplemented -- for that, use the open-source package
`iso15118` (https://github.com/SwitchEV/iso15118), whose EXI codec API
changes between versions. Plug in your installed codec via
`Codec`/`encode_fn`/`decode_fn`, or work directly with raw
EXI bytes (e.g. from Wireshark's "Copy as Hex Stream" on the TCP payload,
or from your own test vectors).
"""

from __future__ import annotations

import logging
import socket
import ssl
import struct
import time
from dataclasses import dataclass
from typing import Callable, Optional

from connection.config import NetworkConfig
from connection.hooks import HookManager
from connection.sdp import SDPResult, discover_secc, resolve_ifindex

logger = logging.getLogger(__name__)

V2GTP_VERSION = 0x01
V2GTP_VERSION_INV = 0xFE
EXI_PAYLOAD_TYPE = 0x8001
MAX_EXI_PAYLOAD_LEN = 1024 * 1024

EncodeFn = Callable[[dict], bytes]
DecodeFn = Callable[[bytes], dict]


class MessageSendError(ConnectionError):
    """A send failed; the peer may have received zero or only part of the frame."""


def _passthrough_encode(msg: dict) -> bytes:
    """Fallback codec: expects already-finished bytes under msg['raw']."""
    raw = msg.get("raw")
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError(
            "No EXI codec plugged in -- either pass your own encode_fn "
            "or supply the message as {'raw': b'...'}."
        )
    return bytes(raw)


def _passthrough_decode(payload: bytes) -> dict:
    return {"raw": payload}


@dataclass
class EVCCSession:
    network: NetworkConfig
    hooks: HookManager
    encode_fn: EncodeFn = _passthrough_encode
    decode_fn: DecodeFn = _passthrough_decode

    def __post_init__(self):
        self._sock: Optional[socket.socket] = None
        self.secc: Optional[SDPResult] = None

    def connect(self) -> SDPResult:
        self.close()
        self.secc = discover_secc(self.network)

        # Explicitly bind the source interface/address for the TCP connect
        # instead of trusting the OS route selection -- with multiple
        # link-local addresses on different interfaces, automatic source
        # selection could otherwise pick a different interface than the one
        # SDP succeeded on, resulting in a plain connect timeout (no
        # response, no ECONNREFUSED).
        ifindex = (
            self.network.interface_index
            if self.network.interface_index is not None
            else resolve_ifindex(self.network.interface_name)
        )

        if self.network.tcp_connect_attempts < 1:
            raise ValueError("tcp_connect_attempts must be positive")
        last_exc: Optional[OSError] = None
        for attempt in range(1, self.network.tcp_connect_attempts + 1):
            raw_sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            try:
                raw_sock.settimeout(self.network.tcp_connect_timeout_s)
                if self.network.local_link_local_addr:
                    local_addr = self.network.local_link_local_addr.split("%")[0]
                    raw_sock.bind((local_addr, 0, 0, ifindex))
                logger.info(
                    "TCP connect to SECC %s:%s (interface index=%s, attempt %s/%s)...",
                    self.secc.secc_address, self.secc.secc_port, ifindex,
                    attempt, self.network.tcp_connect_attempts,
                )
                raw_sock.connect((self.secc.secc_address, self.secc.secc_port))
                if self.secc.use_tls or self.network.use_tls:
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE  # test bench: no PKI trust needed
                    self._sock = ctx.wrap_socket(raw_sock)
                else:
                    self._sock = raw_sock
                return self.secc
            except OSError as exc:
                last_exc = exc
                raw_sock.close()
                logger.warning(
                    "TCP connect to SECC failed (%s, attempt %s/%s).",
                    exc, attempt, self.network.tcp_connect_attempts,
                )
                if attempt < self.network.tcp_connect_attempts:
                    time.sleep(self.network.tcp_connect_retry_delay_s)
            except Exception:
                raw_sock.close()
                raise
        assert last_exc is not None
        raise last_exc

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send_message(self, msg: dict) -> None:
        assert self._sock is not None, "call connect() first"
        exi_payload = self.encode_fn(msg)
        exi_payload = self.hooks.run_pre_send(exi_payload)
        header = struct.pack(
            "!BBHI",
            V2GTP_VERSION,
            V2GTP_VERSION_INV,
            EXI_PAYLOAD_TYPE,
            len(exi_payload),
        )
        try:
            self._sock.sendall(header + exi_payload)
        except OSError as exc:
            raise MessageSendError("Could not confirm complete frame transmission") from exc

    def send_raw(self, payload: bytes) -> None:
        """Total free-for-all: send your own V2GTP packet as-is (hooks active)."""
        self.send_message({"raw": payload})

    def receive_message(self) -> dict:
        return self.decode_fn(self.receive_raw())

    def receive_raw(self) -> bytes:
        """Counterpart to send_raw(): returns the raw EXI payload bytes
        without passing them through decode_fn (e.g. for the SAP handshake,
        which uses a different EXI namespace than the V2G body messages)."""
        assert self._sock is not None, "call connect() first"
        header = self._recv_exact(8)
        version, version_inv, payload_type, payload_len = struct.unpack(
            "!BBHI", header
        )
        if version != V2GTP_VERSION or version_inv != V2GTP_VERSION_INV:
            raise ValueError("Unexpected V2GTP version")
        if payload_type != EXI_PAYLOAD_TYPE:
            raise ValueError(f"Unexpected V2GTP payload type: {payload_type:#x}")
        if payload_len > MAX_EXI_PAYLOAD_LEN:
            raise ValueError(f"EXI payload exceeds {MAX_EXI_PAYLOAD_LEN} bytes")
        payload = self._recv_exact(payload_len)
        return self.hooks.run_post_receive(payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("SECC closed the connection")
            buf += chunk
        return buf
