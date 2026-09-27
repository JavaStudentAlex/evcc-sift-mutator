"""PCAP and Network Capture Analyzer for EV Fast Charging Communications.

Parses recorded packet captures (Wireshark PCAP / PCAPNG) to extract V2GTP headers,
HomePlug Green PHY SLAC timings, and handshake duration metrics.
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Dict, List, Optional
from protocols.v2gtp import V2GTPHeader, V2GTPPayloadType

logger = logging.getLogger("pcap_analyzer")


class PcapPacketSummary:
    def __init__(self, timestamp: float, proto: str, length: int, details: str):
        self.timestamp = timestamp
        self.proto = proto
        self.length = length
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "protocol": self.proto,
            "length": self.length,
            "details": self.details,
        }


class PcapAnalyzer:
    """Parses network traces generated during physical or simulated penetration tests."""

    def __init__(self):
        self.records: List[PcapPacketSummary] = []

    def parse_raw_v2gtp_stream(self, data: bytes, base_timestamp: float = 0.0) -> List[PcapPacketSummary]:
        """Scans a raw byte stream for V2GTP headers and extracts transaction records."""
        offset = 0
        packets = []

        while offset + V2GTPHeader.HEADER_LENGTH <= len(data):
            try:
                hdr = V2GTPHeader.unpack(data[offset:])
                is_valid, _ = hdr.validate()
                if is_valid and offset + V2GTPHeader.HEADER_LENGTH + hdr.payload_len <= len(data):
                    ptype_name = V2GTPPayloadType(hdr.payload_type).name if hdr.payload_type in V2GTPPayloadType._value2member_map_ else f"0x{hdr.payload_type:04X}"
                    summary = PcapPacketSummary(
                        timestamp=base_timestamp + (offset * 0.001),
                        proto=f"V2GTP ({ptype_name})",
                        length=V2GTPHeader.HEADER_LENGTH + hdr.payload_len,
                        details=f"Payload len={hdr.payload_len} bytes",
                    )
                    packets.append(summary)
                    self.records.append(summary)
                    offset += V2GTPHeader.HEADER_LENGTH + hdr.payload_len
                    continue
            except Exception:
                pass
            offset += 1

        return packets
