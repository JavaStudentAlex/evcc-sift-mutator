# ---------------------------------------------------------------------------
# Vendored from https://github.com/eMobMarkus/evcc_simulator (commit c884950)
# -- the YFN x EU Energy Hackathon 2026 (EnBW track) reference EVCC connection
# stack, shared by the organizers as an example to build on. Incorporated into
# EVCC-SIFT-Mutator on 2026-09-27 to provide the real hardware/charger
# transport backend for the AI mutation harness.
#
# Integration fixes to socket cleanup and response validation are applied
# here. Upstream carried no LICENSE file; see connection/README.md.
# ---------------------------------------------------------------------------

"""SECC Discovery Protocol (ISO 15118-2, Annex A) -- client side (EVCC).

Sends an SDPRequest via UDP multicast (ff02::1, port 15118) over the given
interface and evaluates the SDPResponse from the SECC to obtain its actual
(dynamic) TCP/TLS address+port.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass

from connection.config import NetworkConfig

logger = logging.getLogger(__name__)

V2GTP_VERSION = 0x01
V2GTP_VERSION_INV = 0xFE
SDP_REQUEST_PAYLOAD_TYPE = 0x9000
SDP_RESPONSE_PAYLOAD_TYPE = 0x9001

SECURITY_TLS = 0x00
SECURITY_NO_TLS = 0x10
TRANSPORT_TCP = 0x00


@dataclass
class SDPResult:
    secc_address: str
    secc_port: int
    security: int
    transport: int

    @property
    def use_tls(self) -> bool:
        return self.security == SECURITY_TLS


def _build_v2gtp(payload_type: int, payload: bytes) -> bytes:
    header = struct.pack(
        "!BBHI",
        V2GTP_VERSION,
        V2GTP_VERSION_INV,
        payload_type,
        len(payload),
    )
    return header + payload


def _parse_v2gtp(data: bytes) -> tuple[int, bytes]:
    if len(data) < 8:
        raise ValueError("V2GTP packet too short")
    version, version_inv, payload_type, payload_len = struct.unpack(
        "!BBHI", data[:8]
    )
    if version != V2GTP_VERSION or version_inv != V2GTP_VERSION_INV:
        raise ValueError(f"Unexpected V2GTP version: {version:#x}")
    if len(data) != 8 + payload_len:
        raise ValueError(f"Unexpected V2GTP packet length: {len(data)}")
    payload = data[8 : 8 + payload_len]
    return payload_type, payload


def resolve_ifindex(interface_name: str) -> int:
    try:
        return socket.if_nametoindex(interface_name)
    except (OSError, AttributeError) as exc:
        raise RuntimeError(
            f"Could not determine interface index for '{interface_name}'. "
            "On Windows, try using the exact adapter name from "
            "`netsh interface ipv6 show interface`, or adjust "
            "NetworkConfig.interface_name."
        ) from exc


def discover_secc(
    cfg: NetworkConfig,
    security: int = SECURITY_NO_TLS,
    transport: int = TRANSPORT_TCP,
) -> SDPResult:
    ifindex = cfg.interface_index if cfg.interface_index is not None else resolve_ifindex(cfg.interface_name)

    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, ifindex)
        sock.settimeout(cfg.sdp_timeout_s)

        if cfg.local_link_local_addr:
            addr = cfg.local_link_local_addr.split("%")[0]
            sock.bind((addr, 0, 0, ifindex))
        else:
            sock.bind(("", 0))

        request_payload = struct.pack("!BB", security, transport)
        packet = _build_v2gtp(SDP_REQUEST_PAYLOAD_TYPE, request_payload)
        dest = (cfg.sdp_multicast_addr, cfg.sdp_port, 0, ifindex)

        logger.info(
            "SDP discovery: interface index=%s, local bind=%s, destination=%s (timeout per attempt=%ss, "
            "max. %s attempts).", ifindex, cfg.local_link_local_addr or "(auto)", dest,
            cfg.sdp_timeout_s, cfg.sdp_max_attempts,
        )

        if cfg.sdp_max_attempts < 1:
            raise ValueError("sdp_max_attempts must be positive")
        for attempt in range(1, cfg.sdp_max_attempts + 1):
            sock.sendto(packet, dest)
            logger.info("SDPRequest sent (attempt %s/%s), waiting for SDPResponse...",
                        attempt, cfg.sdp_max_attempts)
            try:
                data, _from = sock.recvfrom(2048)
                break
            except TimeoutError:
                logger.warning(
                    "No SDPResponse within %ss (attempt %s/%s) -- SLAC/PLC matching "
                    "may not be finished yet, or the charge point is not responding. Retrying.",
                    cfg.sdp_timeout_s, attempt, cfg.sdp_max_attempts,
                )
                if attempt == cfg.sdp_max_attempts:
                    raise
    finally:
        sock.close()
    payload_type, payload = _parse_v2gtp(data)
    if payload_type != SDP_RESPONSE_PAYLOAD_TYPE:
        raise ValueError(f"Unexpected SDP payload type: {payload_type:#x}")
    if len(payload) != 20:
        raise ValueError(f"Unexpected SDPResponse length: {len(payload)}")

    secc_addr_bytes = payload[0:16]
    secc_port, resp_security, resp_transport = struct.unpack(
        "!HBB", payload[16:20]
    )
    if secc_port == 0 or resp_security not in (SECURITY_TLS, SECURITY_NO_TLS) or resp_transport != TRANSPORT_TCP:
        raise ValueError("Unsupported SDPResponse port, security, or transport")
    secc_addr = socket.inet_ntop(socket.AF_INET6, secc_addr_bytes)
    if secc_addr.startswith("fe80"):
        secc_addr = f"{secc_addr}%{ifindex}"

    logger.info("SDPResponse received: SECC=%s:%s (TLS=%s).", secc_addr, secc_port,
                resp_security == SECURITY_TLS)

    return SDPResult(
        secc_address=secc_addr,
        secc_port=secc_port,
        security=resp_security,
        transport=resp_transport,
    )
