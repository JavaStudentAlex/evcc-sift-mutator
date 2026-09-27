"""Real EVCC connection stack (vendored) for the EVCC-SIFT-Mutator harness.

This package is the hardware-verified "hands" that let the AI mutation harness
talk to a **real** charging station (SECC), instead of only the in-process
``SeccMockServer``. It was vendored from the YFN x EU Energy Hackathon 2026
reference simulator (``eMobMarkus/evcc_simulator``) -- see ``README.md`` in this
directory for provenance and licensing.

Connection bring-up order (see ``run_fuzzer.py`` hardware mode):

    CanControl (arm CP/PP over ixxat CAN)   [can_control]
        -> SLAC matching (join PLC network) [slac]
        -> SDP discovery (find the SECC)    [sdp]
        -> V2GTP/TCP(/TLS) transport        [session.EVCCSession]
        -> EXI codec (DIN SPEC 70121)       [codec]
        -> DIN session state machine        [state_machine]

Every layer degrades gracefully when its optional third-party dependency (or
the physical hardware) is missing, so the package imports cleanly in this
offline sandbox. Use the capability flags / helpers below to decide at runtime
which parts of the real path can actually run.

``codec`` and ``state_machine`` are intentionally *not* re-exported here,
because importing ``codec`` eagerly spins up the Java/EXIficient gateway. Import
them explicitly when needed (``from connection import codec``).
"""
from __future__ import annotations

import importlib.util

# -- Lightweight capability detection ---------------------------------------
# find_spec() only checks importability; it does not execute the module, so
# this stays fast and side-effect-free (no Java gateway, no socket, no CAN bus).


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


#: python-can present -> real CP/PP CAN control (ixxat) is importable.
HAS_CAN: bool = _installed("can")
#: scapy + pyslac present -> real EV-side SLAC matching is importable.
HAS_SLAC: bool = _installed("scapy") and _installed("pyslac")
#: iso15118 package present -> spec-compliant EXI codec *may* be available
#: (still needs a Java runtime at runtime; use exi_available() to confirm).
HAS_EXI_PACKAGE: bool = _installed("iso15118")


def exi_available() -> bool:
    """Whether the spec-compliant EXI codec is actually usable right now.

    Unlike :data:`HAS_EXI_PACKAGE` this imports ``connection.codec`` (spinning
    up the Java/EXIficient gateway on first call) and reports the real result.
    Returns ``False`` -- rather than raising -- when the package or its Java
    runtime is missing, in which case the codec falls back to raw/JSON
    passthrough (fine for transport/sequence tests, rejected by a real SECC).
    """
    try:
        from connection import codec

        return codec.is_compliant()
    except Exception:  # pragma: no cover - defensive, codec self-guards already
        return False


def capability_summary() -> dict:
    """One-glance dict of which real-connection layers are runnable here."""
    return {
        "can_control": HAS_CAN,
        "slac": HAS_SLAC,
        "exi_codec_package": HAS_EXI_PACKAGE,
    }


# -- Cheap re-exports (none of these trigger heavy imports) ------------------
from connection.config import (  # noqa: E402
    CanConfig,
    EvccConfig,
    NetworkConfig,
    ensure_local_config,
    load_local_config,
)
from connection.hooks import (  # noqa: E402
    HookManager,
    flip_byte_at,
    log_only,
    replace_field,
)
from connection.sdp import SDPResult, discover_secc, resolve_ifindex  # noqa: E402
from connection.session import EVCCSession  # noqa: E402

__all__ = [
    "HAS_CAN",
    "HAS_SLAC",
    "HAS_EXI_PACKAGE",
    "exi_available",
    "capability_summary",
    "EvccConfig",
    "NetworkConfig",
    "CanConfig",
    "ensure_local_config",
    "load_local_config",
    "HookManager",
    "flip_byte_at",
    "replace_field",
    "log_only",
    "SDPResult",
    "discover_secc",
    "resolve_ifindex",
    "EVCCSession",
]
