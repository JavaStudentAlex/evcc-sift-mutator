"""V2GTP (Vehicle-to-Grid Transfer Protocol) Header Implementation.

Implements ISO 15118-2:2014 Section 7.2 (V2G Transfer Protocol).
The V2GTP header precedes every packet sent over TCP/UDP between EVCC and SECC.
Header Structure (8 octets):
  - Octet 0: Protocol Version (0x01)
  - Octet 1: Inverse Protocol Version (0xFE)
  - Octet 2..3: Payload Type (e.g. 0x8001 for EXI V2G, 0x9000 for SDP Req, 0x9001 for SDP Res)
  - Octet 4..7: Payload Length (uint32 big-endian)
"""
from __future__ import annotations

import struct
from enum import IntEnum
from typing import Tuple


class V2GTPPayloadType(IntEnum):
    """V2GTP Payload Types defined in ISO 15118-2 Annex A."""
    EXI_ENCODED_V2G = 0x8001
    SDP_REQUEST = 0x9000
    SDP_RESPONSE = 0x9001
    SECURE_SDP_REQUEST = 0x9002
    SECURE_SDP_RESPONSE = 0x9003


class V2GTPHeader:
    HEADER_LENGTH = 8
    CURRENT_VERSION = 0x01
    INVERSE_VERSION = 0xFE

    def __init__(self, payload_type: int = V2GTPPayloadType.EXI_ENCODED_V2G, payload_len: int = 0):
        self.protocol_version = self.CURRENT_VERSION
        self.inverse_version = self.INVERSE_VERSION
        self.payload_type = payload_type
        self.payload_len = payload_len

    def pack(self) -> bytes:
        """Packs the 8-byte header into binary."""
        return struct.pack("!BBHI", self.protocol_version, self.inverse_version, self.payload_type, self.payload_len)

    @classmethod
    def unpack(cls, data: bytes) -> V2GTPHeader:
        """Unpacks 8 bytes into a V2GTPHeader instance."""
        if len(data) < cls.HEADER_LENGTH:
            raise ValueError(f"Data too short for V2GTP header: {len(data)} < {cls.HEADER_LENGTH}")
        ver, inv_ver, p_type, p_len = struct.unpack("!BBHI", data[:cls.HEADER_LENGTH])
        hdr = cls(payload_type=p_type, payload_len=p_len)
        hdr.protocol_version = ver
        hdr.inverse_version = inv_ver
        return hdr

    def validate(self) -> Tuple[bool, str]:
        """Validates standard conformance."""
        if self.protocol_version != self.CURRENT_VERSION:
            return False, f"Invalid protocol version: 0x{self.protocol_version:02X} != 0x{self.CURRENT_VERSION:02X}"
        if (self.protocol_version ^ self.inverse_version) != 0xFF:
            return False, f"Inverse version check failed: 0x{self.protocol_version:02X} ^ 0x{self.inverse_version:02X} != 0xFF"
        return True, "Valid V2GTP header"


def wrap_v2gtp(payload: bytes, payload_type: int = V2GTPPayloadType.EXI_ENCODED_V2G) -> bytes:
    """Wraps raw payload with standard V2GTP header."""
    hdr = V2GTPHeader(payload_type=payload_type, payload_len=len(payload))
    return hdr.pack() + payload


def unwrap_v2gtp(packet: bytes) -> Tuple[V2GTPHeader, bytes]:
    """Unwraps V2GTP packet into header and payload."""
    hdr = V2GTPHeader.unpack(packet)
    payload = packet[V2GTPHeader.HEADER_LENGTH : V2GTPHeader.HEADER_LENGTH + hdr.payload_len]
    return hdr, payload
