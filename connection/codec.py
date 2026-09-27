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

"""EXI codec adapter for DIN SPEC 70121, built on top of the open-source
`iso15118` package (https://github.com/ecog-io/iso15118, PyPI: iso15118).

We deliberately target DIN SPEC 70121 rather than full ISO 15118-2: many
real-world DC charging stations in the field speak primarily/only DIN
70121, and our flow (SAP handshake -> SessionSetupReq -> ... ->
SessionStopReq, without Plug&Charge/certificates) fits that structure
better.

The package internally wraps the Java-based EXIficient codec (hence the
Java runtime requirement, see install.sh/install.ps1). This adapter
encapsulates access to it:
  - encode_sap_req()/decode_sap_res() for the SupportedAppProtocol handshake
    (BEFORE the actual V2G session, done once right after the TCP connect)
  - encode()/decode() for the actual DIN-SPEC-70121 V2GMessages
    (SessionSetupReq...SessionStopReq), including the correct
    Header(SessionID)+Body envelope -- the previous version of this adapter
    only encoded the bare body without a Header/envelope, which no real
    SECC accepts (see
    `iso15118.shared.messages.din_spec.msgdef.V2GMessage`).

If the import fails (package/Java not installed), we automatically fall
back to raw-passthrough mode (msg['raw'] is sent 1:1). That is sufficient
for quick break/fuzzing tests, but is NOT spec-compliant against a real
SECC implementation.
"""

from __future__ import annotations

# -- Pydantic v2 compatibility shim (must run before any iso15118 import) ----
# iso15118 <=0.23 uses @root_validator without skip_on_failure=True, which
# Pydantic >=2.10 rejects.  Patching here is safe because this module is
# the only entry point for the EXI codec in the project.
try:
    import pydantic.deprecated.class_validators as _cv
    _orig_rv = _cv.root_validator

    def _safe_rv(*a, **kw):
        kw.setdefault("skip_on_failure", True)
        if a and callable(a[0]):
            return _orig_rv(skip_on_failure=True)(a[0])
        return _orig_rv(*a, **kw)
    _cv.root_validator = _safe_rv
except Exception:
    pass

import json
import logging
import os

logger = logging.getLogger(__name__)

_codec = None
_available = False


def _patch_py4j_jar_lookup() -> None:
    """Workaround for a known py4j packaging issue on Windows:
    depending on the installation method, pip does not reliably place the
    bundled py4j server jar (py4j's own internal Java<->Python bridge, NOT
    the EXIficient EXI codec jar from iso15118) under
    <sys.prefix>/share/py4j/, where py4j.java_gateway.find_jar_path()
    expects it -- launch_gateway() then fails with "Could not find py4j jar
    at " (empty path), even though Java AND iso15118 are correctly
    installed. We therefore search for the jar ourselves more broadly
    (site-packages, sys.prefix/sys.exec_prefix recursively) and patch
    find_jar_path() if the built-in search finds nothing.
    """
    import glob
    import site

    import py4j.java_gateway as py4j_gw

    if py4j_gw.find_jar_path():
        return  # the built-in search already found the jar

    jar_name_pattern = f"py4j*{py4j_gw.__version__}*.jar"
    search_roots = {sys_prefix for sys_prefix in (site.getuserbase(), *site.getsitepackages())}
    import sys

    search_roots |= {sys.prefix, sys.exec_prefix}
    for root in search_roots:
        matches = glob.glob(os.path.join(root, "**", jar_name_pattern), recursive=True)
        if matches:
            found = matches[0]
            py4j_gw.find_jar_path = lambda: found
            logger.info("Found py4j server jar via fallback search: %s", found)
            return

    logger.warning(
        "py4j server jar not found (neither via the built-in search nor "
        "via fallback search in %s) -- the EXI codec will likely fail.",
        sorted(search_roots),
    )


def _patch_relaxed_sa_schedule_tuple_id() -> None:
    """Real-hardware compatibility: DIN SPEC 70121 (and the underlying
    ISO 15118-2 XSD) define SAScheduleTupleID as an unsignedByte in range
    [1..255], and the iso15118 package enforces exactly that
    (SAScheduleTupleEntry.sa_schedule_tuple_id = Field(..., ge=1, le=255)).
    At least one real charging station firmware seen in testing sends
    SAScheduleTupleID=0 in ChargeParameterDiscoveryRes -- a spec violation
    on the EVSE's part, but strict pydantic validation would abort the
    whole session with an unhandled ValidationError instead of just
    accepting the field. We relax the lower bound to 0 to tolerate this.

    IMPORTANT: mutating field_info.ge alone does NOT work -- pydantic v1's
    Field(ge=..., le=...) on a plain int does not keep the bound as a
    side-channel attribute read at validation time. Instead,
    pydantic/schema.py's get_annotation_with_constraints() calls
    conint(ge=1, le=255), which dynamically creates a whole new
    ConstrainedIntValue subclass with ge/le baked in as CLASS attributes,
    and that subclass becomes the field's actual type (field.type_ /
    field.outer_type_). pydantic/validators.py's number_size_validator
    reads field.type_.ge at call time, never field.field_info.ge -- so the
    class attribute on field.type_ is what must be mutated. It IS read
    fresh on every validation call, so this mutation after class creation
    is sufficient -- no subclassing/re-registration/field.prepare() needed.
    This project's session logic does not otherwise use this field's
    value."""
    try:
        from iso15118.shared.messages.din_spec.datatypes import SAScheduleTupleEntry

        field = SAScheduleTupleEntry.__fields__["sa_schedule_tuple_id"]
        field.type_.ge = 0
        field.outer_type_.ge = 0
    except Exception:
        logger.exception(
            "Could not relax SAScheduleTupleID's lower bound -- charging "
            "stations that send SAScheduleTupleID=0 will still cause EXI "
            "decode to fail."
        )


_exi = None

try:
    _patch_py4j_jar_lookup()

    from iso15118.shared.exi_codec import EXI
    from iso15118.shared.exificient_exi_codec import ExificientEXICodec
    try:
        from iso15118.shared.settings import load_shared_settings
    except ImportError:
        load_shared_settings = None  # v0.23+: shared_settings auto-populated

    # EXI.to_exi()/from_exi() read shared_settings[...] (MESSAGE_LOG_JSON/
    # MESSAGE_LOG_EXI) directly -- without this call the dict stays empty
    # and the very first encode()/decode() call raises a KeyError.
    if load_shared_settings is not None:
        load_shared_settings()

    _patch_relaxed_sa_schedule_tuple_id()

    _exi = EXI()
    _exi.set_exi_codec(ExificientEXICodec())
    _available = True
except Exception as exc:  # ImportError, or Java/JAR missing
    logger.warning(
        "iso15118 EXI codec not available (%s) -- falling back to "
        "raw passthrough (not spec-compliant, for tests only).",
        exc,
    )


def is_compliant() -> bool:
    return _available


def configure_logging(verbose: bool) -> None:
    """Controls whether iso15118's own EXI.to_exi()/from_exi() logs the
    full JSON payload of every message ("Message to encode (ns=...): {...}"
    / "Decoded message (ns=...): {...}") -- controlled via
    shared_settings[MESSAGE_LOG_JSON]/[MESSAGE_LOG_EXI], which
    load_shared_settings() set to True (the default) on module import.
    Without calling this, that verbose default stays active."""
    if not _available:
        return
    from iso15118.shared.settings import shared_settings
    try:
        from iso15118.shared.settings import SettingKey
        json_key = SettingKey.MESSAGE_LOG_JSON
        exi_key = SettingKey.MESSAGE_LOG_EXI
    except ImportError:
        json_key = "MESSAGE_LOG_JSON"
        exi_key = "MESSAGE_LOG_EXI"

    shared_settings[json_key] = verbose
    shared_settings[exi_key] = verbose


# -- SAP handshake (SupportedAppProtocol) -----------------------------------
#
# Every ISO 15118 family (DIN SPEC 70121, ISO 15118-2, -20) starts the TCP
# session with this protocol negotiation step, BEFORE the actual V2G
# message sequence (SessionSetupReq etc.) begins -- regardless of which
# protocol is spoken afterwards. It is transported directly via
# session.send_raw()/receive_raw() (bypassing encode_fn/decode_fn, since a
# different EXI namespace applies here than for the DIN body messages).

def encode_sap_req(
    protocol_ns: str = "urn:din:70121:2012:MsgDef",
    major_version: int = 2,
    minor_version: int = 0,
    schema_id: int = 1,
    priority: int = 1,
) -> bytes:
    from iso15118.shared.messages.app_protocol import AppProtocol, SupportedAppProtocolReq
    from iso15118.shared.messages.enums import Namespace

    req = SupportedAppProtocolReq(
        AppProtocol=[
            AppProtocol(
                ProtocolNamespace=protocol_ns,
                VersionNumberMajor=major_version,
                VersionNumberMinor=minor_version,
                SchemaID=schema_id,
                Priority=priority,
            )
        ]
    )
    return _exi.to_exi(req, Namespace.SAP)


def decode_sap_res(payload: bytes) -> dict:
    from iso15118.shared.messages.enums import Namespace

    res = _exi.from_exi(payload, Namespace.SAP)
    return res.dict(by_alias=True, exclude_none=True)


# -- DIN-SPEC-70121 V2GMessages (Header+Body) -------------------------------

def encode(msg: dict) -> bytes:
    """msg: {'name': 'SessionSetupReq', 'session_id': '00', 'fields': {...}}
    or {'raw': b'...'}"""
    if "raw" in msg:
        raw = msg["raw"]
        return bytes(raw) if isinstance(raw, (bytes, bytearray)) else bytes()

    if not _available:
        # No real codec available: fall back to "sending" UTF-8 JSON -- a
        # real SECC will reject this as invalid EXI/abort the session.
        # Good enough for transport/sequence tests against our own test
        # peer, but not for real interop.
        return json.dumps(msg).encode("utf-8")

    name = msg["name"]
    session_id = msg.get("session_id", "00")
    fields = msg.get("fields", {})
    try:
        from iso15118.shared.messages.din_spec.body import Body, get_msg_type
        from iso15118.shared.messages.din_spec.header import MessageHeader
        from iso15118.shared.messages.din_spec.msgdef import V2GMessage
        from iso15118.shared.messages.enums import Namespace

        msg_cls = get_msg_type(name)
        if msg_cls is None:
            raise ValueError(f"Unknown DIN-SPEC-70121 message type: {name}")
        body_instance = msg_cls(**fields)
        body = Body(**{name: body_instance})
        header = MessageHeader(SessionID=session_id)
        v2g_msg = V2GMessage(Header=header, Body=body)
        return _exi.to_exi(v2g_msg, Namespace.DIN_MSG_DEF)
    except Exception:
        logger.exception(
            "EXI encode for '%s' failed -- check field names/structure "
            "(iso15118.shared.messages.din_spec.body.%s).",
            name,
            name,
        )
        raise


def decode(payload: bytes) -> dict:
    """Returns {'name': <response message name>, 'session_id': str, 'fields': {...}}."""
    if not _available:
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:
            return {"raw": payload}

    try:
        from iso15118.shared.messages.enums import Namespace

        v2g_msg = _exi.from_exi(payload, Namespace.DIN_MSG_DEF)
        message, name = v2g_msg.body.get_message_and_name()
        fields = message.dict(by_alias=True, exclude_none=True) if message else {}
        return {"name": name, "session_id": v2g_msg.header.session_id, "fields": fields}
    except Exception:
        logger.exception("EXI decode failed.")
        raise
