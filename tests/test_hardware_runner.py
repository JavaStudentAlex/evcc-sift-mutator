"""Hardware orchestration tests with every external device and codec faked."""

import importlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from connection.config import EvccConfig


@pytest.fixture
def hardware_fakes(monkeypatch):
    calls = []
    failures = SimpleNamespace(can=None, slac=None, session=None, machine=None)

    codec = ModuleType("connection.codec")
    codec.is_compliant = lambda: True
    codec.configure_logging = lambda verbose: calls.append(("codec_logging", verbose))
    codec.encode = lambda msg: b"fake-exi"
    codec.decode = lambda data: {"name": "FakeRes", "fields": {}}
    monkeypatch.setitem(sys.modules, "connection.codec", codec)

    import connection
    import connection.can_control
    import connection.session
    import connection.slac

    monkeypatch.setattr(connection, "codec", codec, raising=False)
    monkeypatch.setattr(
        connection,
        "capability_summary",
        lambda: {"can_control": True, "slac": True, "exi_codec_package": True},
    )

    class FakeCan:
        def __init__(self, cfg):
            calls.append(("can_init", cfg))

        def connect(self):
            calls.append("can_connect")
            if failures.can:
                raise failures.can

        def start_keep_alive(self):
            calls.append("keep_alive")

        def start_feedback_listener(self):
            calls.append("feedback")

        def set_pilot_gen_active_and_verify(self, *args, **kwargs):
            calls.append("pilot_verify")
            return {}

        def set_pp_state_and_verify(self, *args, **kwargs):
            calls.append("pp_verify")
            return {}

        def wait_for_hlc_request(self, *args, **kwargs):
            calls.append("hlc")
            return {"cp_state": "B", "duty_cycle_percent": 5.0}

        def close(self):
            calls.append("can_close")

    class FakeSlacTransport:
        def __init__(self, interface_name):
            calls.append(("slac_transport", interface_name))

    def run_slac(*args, **kwargs):
        calls.append("slac")
        if failures.slac:
            raise failures.slac
        return SimpleNamespace(evse_mac=b"\x01" * 6)

    class FakeSession:
        def __init__(self, *args, **kwargs):
            calls.append(("session_init", args, kwargs))

        def connect(self):
            calls.append("session_connect")
            if failures.session:
                raise failures.session
            return SimpleNamespace(secc_address="fe80::1", secc_port=15118, use_tls=False)

        def close(self):
            calls.append("session_close")

    class FakeMachine:
        def __init__(self, *args, **kwargs):
            calls.append(("machine_init", args, kwargs))

        def run_dc_session(self, current_demand_max_cycles=None):
            calls.append(("run_dc_session", current_demand_max_cycles))
            if failures.machine:
                raise failures.machine

    monkeypatch.setattr(connection.can_control, "CanControl", FakeCan)
    monkeypatch.setattr(connection.slac, "SlacTransport", FakeSlacTransport)
    monkeypatch.setattr(connection.slac, "get_interface_mac", lambda name: b"\x02" * 6)
    monkeypatch.setattr(connection.slac, "run_ev_slac_matching", run_slac)
    monkeypatch.setattr(connection.session, "EVCCSession", FakeSession)
    state_machine = importlib.import_module("connection.state_machine")
    monkeypatch.setattr(state_machine, "Iso15118EvccStateMachine", FakeMachine)

    return calls, failures, codec


def _report(tmp_path):
    return tmp_path / "report.md"


def _assert_report(path):
    assert path.is_file()
    companion = path.with_suffix(".json")
    assert companion.is_file()
    data = json.loads(companion.read_text(encoding="utf-8"))
    assert "metadata" in data
    assert "sessions" in data
    return data


def test_hardware_runner_bringup_order_and_bounded_cycles(hardware_fakes, tmp_path):
    from simulator.hardware_runner import run_hardware_session

    calls, _, _ = hardware_fakes
    cfg = EvccConfig()
    report = _report(tmp_path)
    status = run_hardware_session(cfg=cfg, cycles=2, report_path=str(report), current_demand_cycles=4)

    assert status == 0
    stages = [
        "can_connect", "keep_alive", "feedback", "pilot_verify", "pp_verify",
        "hlc", "slac", "session_connect", ("run_dc_session", 4), "session_close",
    ]
    positions = [calls.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert calls.count("can_close") >= 1
    assert calls.count(("run_dc_session", 4)) == 2
    assert len(_assert_report(report)["sessions"]) == 2


@pytest.mark.parametrize("stage", ["can", "slac", "session", "machine"])
def test_hardware_runner_cleans_up_after_failure(hardware_fakes, tmp_path, stage):
    from simulator.hardware_runner import run_hardware_session

    calls, failures, _ = hardware_fakes
    setattr(failures, stage, RuntimeError("fixture failure"))
    report = _report(tmp_path)
    assert run_hardware_session(cfg=EvccConfig(), cycles=1, report_path=str(report)) == 1
    assert "can_close" in calls
    if stage in ("session", "machine"):
        assert "session_close" in calls
    _assert_report(report)


def test_hardware_runner_interrupt_cleans_up(hardware_fakes, tmp_path):
    from simulator.hardware_runner import run_hardware_session

    calls, failures, _ = hardware_fakes
    failures.machine = KeyboardInterrupt()
    report = _report(tmp_path)
    assert run_hardware_session(cfg=EvccConfig(), cycles=1, report_path=str(report)) == 130
    assert "session_close" in calls
    assert "can_close" in calls
    _assert_report(report)


def test_hardware_runner_preflight_blocks_missing_dependencies(hardware_fakes, monkeypatch, tmp_path):
    import connection
    from simulator.hardware_runner import run_hardware_session

    calls, _, _ = hardware_fakes
    monkeypatch.setattr(
        connection,
        "capability_summary",
        lambda: {"can_control": False, "slac": False, "exi_codec_package": False},
    )
    report = _report(tmp_path)
    assert run_hardware_session(cfg=EvccConfig(), cycles=1, report_path=str(report)) == 2
    assert "can_connect" not in calls
    _assert_report(report)


def test_hardware_runner_preflight_blocks_noncompliant_codec(hardware_fakes, tmp_path):
    from simulator.hardware_runner import run_hardware_session

    calls, _, codec = hardware_fakes
    codec.is_compliant = lambda: False
    report = _report(tmp_path)
    assert run_hardware_session(cfg=EvccConfig(), cycles=1, report_path=str(report)) == 2
    assert "can_connect" not in calls
    _assert_report(report)


def test_hardware_runner_supports_unmutated_control(hardware_fakes, tmp_path):
    from simulator.hardware_runner import run_hardware_session

    calls, _, _ = hardware_fakes
    assert run_hardware_session(
        cfg=EvccConfig(),
        cycles=1,
        report_path=str(_report(tmp_path)),
        mutations_enabled=False,
    ) == 0
    session_inits = [c for c in calls if isinstance(c, tuple) and c[0] == "session_init"]
    assert len(session_inits) == 1
    encode_fn = session_inits[0][2]["encode_fn"]
    assert encode_fn.__self__.enabled is False
