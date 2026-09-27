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

"""Manipulation hooks at the raw-byte level.

Since we are the EVCC ourselves here (no longer just a MITM on someone
else's traffic), WinDivert is no longer needed -- we own the socket and
bytes directly. The hooks still allow every outgoing/incoming V2GTP payload
to be modified in a targeted way before sending or after receiving (fuzzing,
invalid values, session breaking, etc.), without touching the session logic
itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

Hook = Callable[[bytes], bytes]


@dataclass
class HookManager:
    pre_send: List[Hook] = field(default_factory=list)
    post_receive: List[Hook] = field(default_factory=list)

    def run_pre_send(self, payload: bytes) -> bytes:
        for hook in self.pre_send:
            payload = hook(payload)
        return payload

    def run_post_receive(self, payload: bytes) -> bytes:
        for hook in self.post_receive:
            payload = hook(payload)
        return payload


def flip_byte_at(index: int) -> Hook:
    """Example hook: flips a fixed byte -- for quick breakage tests."""

    def _hook(payload: bytes) -> bytes:
        if len(payload) <= index:
            return payload
        data = bytearray(payload)
        data[index] ^= 0xFF
        return bytes(data)

    return _hook


def replace_field(search: bytes, replacement: bytes) -> Hook:
    """Example hook: replaces a byte sequence (e.g. an EXI field).

    Caution: replacement should usually be the same length as search,
    otherwise the rest of the EXI encoding shifts and the message becomes
    invalid in places you didn't intend.
    """

    def _hook(payload: bytes) -> bytes:
        return payload.replace(search, replacement)

    return _hook


def log_only(label: str) -> Hook:
    def _hook(payload: bytes) -> bytes:
        print(f"[{label}] {len(payload)} bytes: {payload.hex()}")
        return payload

    return _hook
