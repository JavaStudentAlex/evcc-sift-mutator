"""HomePlug Green PHY / SLAC (Signal Level Attenuation Characterization) Protocol.

Implements ISO 15118-3 and HomePlug Green PHY MME (Management Message Entity) frames.
SLAC pairs the electric vehicle to the physical charging cable over PLC (Powerline Comm)
by measuring signal attenuation across 58 OFDM carrier frequencies before IP networking starts.
"""
from __future__ import annotations

import struct
from enum import IntEnum
from typing import List, Optional, Tuple


class SlacMmeType(IntEnum):
    CM_SLAC_PARM_REQ = 0x6064
    CM_SLAC_PARM_CNF = 0x6065
    CM_START_ATTEN_CHAR_IND = 0x6068
    CM_MNBC_SOUND_IND = 0x606C
    CM_ATTEN_CHAR_IND = 0x606E
    CM_ATTEN_CHAR_RSP = 0x606F
    CM_SLAC_MATCH_REQ = 0x607C
    CM_SLAC_MATCH_CNF = 0x607D


HOMEPLUG_ETHERTYPE = 0x88E1
QUALCOMM_OUI = b"\x00\xb0\x52"  # Qualcomm Atheros OUI 00:B0:52


class SlacFrame:
    """Represents a HomePlug Green PHY Management Message Entity (MME) frame."""

    def __init__(
        self,
        mme_type: SlacMmeType,
        run_id: bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08",
        payload: bytes = b"",
        src_mac: bytes = b"\x02\x00\x00\x00\x00\x01",
        dst_mac: bytes = b"\xff\xff\xff\xff\xff\xff",
    ):
        self.mme_type = mme_type
        self.run_id = run_id if len(run_id) == 8 else (run_id + b"\x00" * 8)[:8]
        self.payload = payload
        self.src_mac = src_mac if len(src_mac) == 6 else b"\x02\x00\x00\x00\x00\x01"
        self.dst_mac = dst_mac if len(dst_mac) == 6 else b"\xff\xff\xff\xff\xff\xff"

    def pack(self) -> bytes:
        """Packs into raw Ethernet frame bytes."""
        # 14 bytes Ethernet header
        eth_hdr = self.dst_mac + self.src_mac + struct.pack("!H", HOMEPLUG_ETHERTYPE)
        # 3 bytes HomePlug AV header: MMV (1), MMTYPE (2 bytes little-endian)
        hp_hdr = struct.pack("<BH", 0x01, self.mme_type)
        # 3 bytes Vendor OUI
        oui = QUALCOMM_OUI
        # Body starts with 8-byte RunID
        body = self.run_id + self.payload
        return eth_hdr + hp_hdr + oui + body

    @classmethod
    def unpack(cls, data: bytes) -> Optional[SlacFrame]:
        """Unpacks raw Ethernet frame bytes into SlacFrame."""
        if len(data) < 14 + 3 + 3 + 8:
            return None
        dst = data[0:6]
        src = data[6:12]
        eth_type = struct.unpack("!H", data[12:14])[0]
        if eth_type != HOMEPLUG_ETHERTYPE:
            return None
        mmv, mme_raw = struct.unpack("<BH", data[14:17])
        # data[17:20] is OUI
        run_id = data[20:28]
        payload = data[28:]
        try:
            mme = SlacMmeType(mme_raw)
        except ValueError:
            return None
        return cls(mme_type=mme, run_id=run_id, payload=payload, src_mac=src, dst_mac=dst)


def build_slac_param_req(run_id: bytes) -> SlacFrame:
    """Builds CM_SLAC_PARM.REQ (Initial handshake request from EV)."""
    # Payload contains application type (0x00 for PEV) and security type (0x00)
    payload = b"\x00\x00"
    return SlacFrame(mme_type=SlacMmeType.CM_SLAC_PARM_REQ, run_id=run_id, payload=payload)


def build_mnbc_sound_ind(run_id: bytes, sound_idx: int) -> SlacFrame:
    """Builds CM_MNBC_SOUND.IND (Multicast Sounding Packet from EV to measure cable loss)."""
    # 1 byte count, 1 byte sound index, 10 bytes random sounding payload
    payload = struct.pack("!BB", 10, sound_idx) + b"\xAA" * 10
    return SlacFrame(mme_type=SlacMmeType.CM_MNBC_SOUND_IND, run_id=run_id, payload=payload)


def build_slac_match_req(run_id: bytes, evse_mac: bytes) -> SlacFrame:
    """Builds CM_SLAC_MATCH.REQ (EV requests pairing with the EVSE with best attenuation)."""
    # Payload: 1 byte match status, 6 bytes EVSE MAC, 6 bytes PEV MAC
    pev_mac = b"\x02\x00\x00\x00\x00\x01"
    payload = b"\x00" + evse_mac + pev_mac
    return SlacFrame(mme_type=SlacMmeType.CM_SLAC_MATCH_REQ, run_id=run_id, payload=payload, dst_mac=evse_mac)
