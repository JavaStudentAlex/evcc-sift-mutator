# ---------------------------------------------------------------------------
# Vendored from https://github.com/eMobMarkus/evcc_simulator (commit c884950)
# -- the YFN x EU Energy Hackathon 2026 (EnBW track) reference EVCC connection
# stack, shared by the organizers as an example to build on. Incorporated into
# EVCC-SIFT-Mutator on 2026-09-27 to provide the real hardware/charger
# transport backend for the AI mutation harness.
#
# Only intra-package imports were adjusted (flat -> `connection.*`); the
# protocol logic is upstream's, verified against real charging-station
# hardware. Upstream carried no LICENSE file. See connection/README.md.
# ---------------------------------------------------------------------------

"""EV-side SLAC (Signal Level Attenuation Characterization), ISO 15118-3 /
HomePlug GreenPHY.

Why this is needed: the actual attenuation measurement is performed
autonomously by the PLC modem chip's firmware, as soon as it sees the right
sounding/characterization frames. But WHICH logical network (AVLN) the
local modem subsequently joins is a decision that software has to make and
communicate to the modem via CM_SET_KEY.REQ -- without this step the modem
stays on its factory setting and never joins the charging station's
network. SDP (which builds on top of this PLC connection) then fails
permanently, no matter how often/long it is retried (see the retry logic
in sdp.py, which CANNOT fix this if SLAC never completed).

Uses the byte-exact message/header classes from the `pyslac` package
(https://github.com/EcoG-io/pyslac, Apache-2.0) for encoding/decoding --
not re-transcribed by hand, to avoid transmission errors when talking to
real hardware. `pyslac` itself, however, only implements the EVSE side
(SlacEvseSession); the EV-side sequencing here is written entirely from
scratch (modeled on pyslac/examples/ev_slac_scapy.py, which is explicitly
marked there as an unhardened test stub).

`pyslac` and `scapy` are deliberately imported ONLY lazily (inside
functions) -- so that this module remains importable without either
package installed, and the sequencing logic can be tested without real
hardware (see tests/test_slac.py, where both modules are faked).

Transport: `scapy` (raw Ethernet frame access on EtherType 0x88E1) --
on Windows this additionally requires Npcap (WinPcap-compatible mode) and
administrator rights.

Status: verified against real hardware -- the EV-side sequencing here
completes SLAC matching successfully against a real charging station's
PLC modem. The message formats themselves are taken from `pyslac` (tested
there against real Qualcomm/Atheros chips).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from os import urandom

logger = logging.getLogger(__name__)

# HomePlug GreenPHY / SLAC constants (values identical to pyslac.enums,
# kept here as plain integer/bytes constants so this module remains
# importable without pyslac installed).
ETH_TYPE_HPAV = 0x88E1
MMTYPE_REQ = 0x0000
MMTYPE_CNF = 0x0001
MMTYPE_IND = 0x0002
MMTYPE_RSP = 0x0003
CM_SET_KEY = 0x6008
CM_SLAC_PARM = 0x6064
CM_START_ATTEN_CHAR = 0x6068
CM_MNBC_SOUND = 0x6074
CM_ATTEN_CHAR = 0x606C
CM_SLAC_MATCH = 0x607C

SLAC_MSOUNDS = 10
SLAC_ATTEN_TIMEOUT = 6  # multiples of 100ms, see pyslac.enums
SLAC_PAUSE_S = 0.02
SLAC_SETTLE_TIME_S = 10.0
# Timeouts per ISO15118-3 table A.1 / pyslac.enums.Timers
SLAC_REQ_TIMEOUT_S = 0.4
SLAC_ATTEN_RESULTS_TIMEOUT_S = 1.2
SLAC_MATCH_TIMEOUT_S = 10.0
SLAC_RESP_TIMEOUT_S = 0.2

BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"

# Well-known MAC address of the local PLC modem chip (Qualcomm/Atheros
# QCA7000 etc., see open-plc-utils as well as pyslac/examples/ev_slac_scapy.py
# -- ATHEROS_CHIP_MAC there). CM_SET_KEY.REQ goes to THIS address (the own
# modem), NOT to the charging station. Adjust in SlacConfig.plc_modem_mac
# depending on the modem chip fitted.
DEFAULT_PLC_MODEM_MAC = bytes.fromhex("00b052000001")

# Minimum length of a raw frame so that the ETH+HomePlug header (19 bytes)
# is actually present before we evaluate mm_type.
_MIN_FRAME_LEN = 19


class SlacError(RuntimeError):
    """SLAC matching failed (timeout or unexpected response)."""


@dataclass
class SlacConfig:
    interface_name: str
    # 6 bytes, MAC address of the own network interface (the one connected
    # to the PLC modem). Must be provided -- see slac.get_interface_mac()
    # for how to determine it.
    own_mac: bytes
    plc_modem_mac: bytes = DEFAULT_PLC_MODEM_MAC
    parm_req_attempts: int = 5
    parm_req_timeout_s: float = SLAC_REQ_TIMEOUT_S
    atten_results_timeout_s: float = SLAC_ATTEN_RESULTS_TIMEOUT_S
    match_timeout_s: float = SLAC_MATCH_TIMEOUT_S
    set_key_timeout_s: float = SLAC_RESP_TIMEOUT_S
    settle_time_s: float = SLAC_SETTLE_TIME_S
    num_sounds: int = SLAC_MSOUNDS
    sound_pause_s: float = SLAC_PAUSE_S


@dataclass
class SlacMatchResult:
    evse_mac: bytes
    run_id: bytes
    nid: bytes
    nmk: bytes


def get_interface_mac(interface_name: str) -> bytes:
    """Determines the MAC address of a network interface via scapy."""
    from scapy.all import get_if_hwaddr

    return bytes.fromhex(get_if_hwaddr(interface_name).replace(":", ""))


class SlacTransport:
    """Wraps raw Ethernet frame access (scapy) -- lazy import, so slac.py
    remains importable without scapy/Npcap installed, and can be replaced
    by a fake in tests."""

    def __init__(self, interface_name: str):
        self.interface_name = interface_name

    def send(self, frame: bytes) -> None:
        from scapy.all import sendp

        sendp(frame, iface=self.interface_name, verbose=False)

    def recv(self, timeout_s: float, mm_type: int) -> bytes | None:
        """Waits up to timeout_s seconds for a HomePlug GreenPHY frame with
        a matching mm_type (MM-Base | MMTYPE_*). Returns the raw frame
        bytes (including Ethernet+HomePlug header), or None on timeout."""
        from scapy.all import sniff

        packets = sniff(
            iface=self.interface_name, timeout=timeout_s, count=1,
            lfilter=lambda pkt: _frame_matches(bytes(pkt), mm_type),
        )
        if not packets:
            return None
        return bytes(packets[0])


def _frame_matches(raw: bytes, mm_type: int) -> bool:
    if len(raw) < _MIN_FRAME_LEN or raw[12:14] != ETH_TYPE_HPAV.to_bytes(2, "big"):
        return False
    return int.from_bytes(raw[15:17], "little") == mm_type


def _build_frame(dst_mac: bytes, src_mac: bytes, mm_type: int, payload: bytes) -> bytes:
    from pyslac.layer_2_headers import EthernetHeader, HomePlugHeader

    eth = EthernetHeader(dst_mac=dst_mac, src_mac=src_mac)
    hp = HomePlugHeader(mm_type)
    return eth.pack_big() + hp.pack_big() + payload


def run_ev_slac_matching(transport: SlacTransport, cfg: SlacConfig) -> SlacMatchResult:
    """Runs the complete EV-side SLAC sequence and has the own PLC modem
    join the charging station's network (CM_SET_KEY.REQ). Raises SlacError
    on timeout/failure at any step. After a successful return the modem
    still needs cfg.settle_time_s before IP traffic (SDP) is possible over
    the PLC connection -- this function already waits for that itself."""
    from pyslac.messages import (
        AtennChar,
        AtennCharRsp,
        MatchCnf,
        MatchReq,
        MnbcSound,
        SetKeyCnf,
        SetKeyReq,
        SlacParmCnf,
        SlacParmReq,
        StartAtennChar,
    )

    run_id = urandom(8)
    logger.info("SLAC: starting matching (RunID=%s) on interface %s.",
                run_id.hex(), transport.interface_name)

    # 1) CM_SLAC_PARM.REQ (broadcast) -> wait for CM_SLAC_PARM.CNF (unicast)
    parm_req = SlacParmReq(run_id=run_id)
    parm_cnf_raw = None
    for attempt in range(1, cfg.parm_req_attempts + 1):
        frame = _build_frame(BROADCAST_MAC, cfg.own_mac, CM_SLAC_PARM | MMTYPE_REQ,
                              parm_req.pack_big())
        transport.send(frame)
        logger.info("SLAC: CM_SLAC_PARM.REQ sent (attempt %s/%s).",
                     attempt, cfg.parm_req_attempts)
        parm_cnf_raw = transport.recv(cfg.parm_req_timeout_s, CM_SLAC_PARM | MMTYPE_CNF)
        if parm_cnf_raw is not None:
            break
    if parm_cnf_raw is None:
        raise SlacError(
            f"No CM_SLAC_PARM.CNF received within {cfg.parm_req_attempts} attempts "
            f"-- no charging station is responding on the PLC segment."
        )
    evse_mac = parm_cnf_raw[6:12]
    parm_cnf = SlacParmCnf.from_bytes(parm_cnf_raw)
    logger.info("SLAC: CM_SLAC_PARM.CNF received from %s (NumSounds=%s).",
                evse_mac.hex(), parm_cnf.num_sounds)

    # 2) CM_START_ATTEN_CHAR.IND (broadcast)
    start_atten = StartAtennChar(
        num_sounds=cfg.num_sounds, time_out=SLAC_ATTEN_TIMEOUT,
        forwarding_sta=cfg.own_mac, run_id=run_id,
    )
    transport.send(_build_frame(BROADCAST_MAC, cfg.own_mac,
                                 CM_START_ATTEN_CHAR | MMTYPE_IND, start_atten.pack_big()))
    logger.info("SLAC: CM_START_ATTEN_CHAR.IND sent.")

    # 3) num_sounds x CM_MNBC_SOUND.IND (broadcast), with a pause in between
    for i in range(cfg.num_sounds):
        cnt = cfg.num_sounds - i
        sound = MnbcSound(cnt=cnt, run_id=run_id)
        transport.send(_build_frame(BROADCAST_MAC, cfg.own_mac,
                                     CM_MNBC_SOUND | MMTYPE_IND, sound.pack_big()))
        time.sleep(cfg.sound_pause_s)
    logger.info("SLAC: %s x CM_MNBC_SOUND.IND sent.", cfg.num_sounds)

    # 4) wait for CM_ATTEN_CHAR.IND (unicast, result from the charging station)
    atten_char_raw = transport.recv(cfg.atten_results_timeout_s, CM_ATTEN_CHAR | MMTYPE_IND)
    if atten_char_raw is None:
        raise SlacError("No CM_ATTEN_CHAR.IND (attenuation profile) received from the charging station.")
    atten_char = AtennChar.from_bytes(atten_char_raw)
    logger.info("SLAC: CM_ATTEN_CHAR.IND received (NumSounds=%s).", atten_char.num_sounds)

    # 5) CM_ATTEN_CHAR.RSP (unicast) -- confirm success
    atten_rsp = AtennCharRsp(
        source_address=cfg.own_mac, run_id=run_id, source_id=0x00, resp_id=0x00, result=0x00,
    )
    transport.send(_build_frame(evse_mac, cfg.own_mac,
                                 CM_ATTEN_CHAR | MMTYPE_RSP, atten_rsp.pack_big()))
    logger.info("SLAC: CM_ATTEN_CHAR.RSP sent.")

    # 6) CM_SLAC_MATCH.REQ (unicast) -> wait for CM_SLAC_MATCH.CNF
    #
    # WARNING workaround: pyslac.messages.MatchReq.pack_big() encodes
    # mvf_length big-endian -- its sibling MatchCnf (the reverse direction),
    # however, explicitly encodes the same field little-endian ("MVF is sent in
    # little endian", per the pyslac source), an inconsistency bug in pyslac
    # itself (only tested there on the EVSE side, where this field was never
    # checked on send against a real, independent peer). Empirically
    # confirmed: a real charging station does NOT respond to a big-endian
    # CM_SLAC_MATCH.REQ (no CM_SLAC_MATCH.CNF, no SlacError feedback -- the
    # message is simply ignored). We therefore pass the already byte-swapped
    # value (0x3E00 instead of 0x003E), so that pyslac's big-endian packer
    # effectively puts little-endian bytes on the wire.
    match_req = MatchReq(pev_mac=cfg.own_mac, evse_mac=evse_mac, run_id=run_id, mvf_length=0x3E00)
    transport.send(_build_frame(evse_mac, cfg.own_mac,
                                 CM_SLAC_MATCH | MMTYPE_REQ, match_req.pack_big()))
    logger.info("SLAC: CM_SLAC_MATCH.REQ sent.")
    match_cnf_raw = transport.recv(cfg.match_timeout_s, CM_SLAC_MATCH | MMTYPE_CNF)
    if match_cnf_raw is None:
        raise SlacError("No CM_SLAC_MATCH.CNF (network key) received from the charging station.")
    match_cnf = MatchCnf.from_bytes(match_cnf_raw)
    logger.info("SLAC: CM_SLAC_MATCH.CNF received, got NID/NMK.")

    # 7) CM_SET_KEY.REQ to the OWN PLC modem (not to the charging station!)
    #    -- this is the step that actually makes the own modem join the
    #    charging station's logical network.
    set_key_req = SetKeyReq(nid=match_cnf.nid, new_key=match_cnf.nmk)
    transport.send(_build_frame(cfg.plc_modem_mac, cfg.own_mac,
                                 CM_SET_KEY | MMTYPE_REQ, set_key_req.pack_big()))
    logger.info("SLAC: CM_SET_KEY.REQ sent to own PLC modem (%s).",
                cfg.plc_modem_mac.hex())
    set_key_cnf_raw = transport.recv(cfg.set_key_timeout_s, CM_SET_KEY | MMTYPE_CNF)
    if set_key_cnf_raw is None:
        raise SlacError(
            "No CM_SET_KEY.CNF received from the own PLC modem -- "
            "SlacConfig.plc_modem_mac may be wrong for this modem chip."
        )
    SetKeyCnf.from_bytes(set_key_cnf_raw)
    logger.info("SLAC: own PLC modem confirmed CM_SET_KEY.CNF -- "
                "network join initiated, waiting %ss to settle.",
                cfg.settle_time_s)

    time.sleep(cfg.settle_time_s)
    logger.info("SLAC: matching complete, PLC connection should now be up.")

    return SlacMatchResult(evse_mac=evse_mac, run_id=run_id, nid=match_cnf.nid, nmk=match_cnf.nmk)
