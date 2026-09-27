"""CLI routing and mock-mode regression tests without device access."""

import sys
from types import ModuleType

import pytest

import run_fuzzer


def test_hardware_cli_routes_to_real_runner(monkeypatch, tmp_path):
    calls = []
    fake_runner = ModuleType("simulator.hardware_runner")

    def run_hardware_session(**kwargs):
        calls.append(kwargs)
        return 2

    fake_runner.run_hardware_session = run_hardware_session
    monkeypatch.setitem(sys.modules, "simulator.hardware_runner", fake_runner)
    report = tmp_path / "hardware.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_fuzzer.py", "--mode", "hardware", "--cycles", "2", "--report", str(report)],
    )

    with pytest.raises(SystemExit) as exit_info:
        run_fuzzer.main()

    assert exit_info.value.code == 2
    assert len(calls) == 1
    assert calls[0]["cycles"] == 2
    assert calls[0]["report_path"] == str(report)


def test_mock_cli_does_not_load_hardware_runner(monkeypatch, tmp_path):
    fake_runner = ModuleType("simulator.hardware_runner")

    def no_hardware(**kwargs):
        pytest.fail("mock mode must not use the hardware runner")

    fake_runner.run_hardware_session = no_hardware
    monkeypatch.setitem(sys.modules, "simulator.hardware_runner", fake_runner)
    report = tmp_path / "mock.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_fuzzer.py", "--mode", "mock", "--cycles", "1", "--report", str(report)],
    )

    with pytest.raises(SystemExit) as exit_info:
        run_fuzzer.main()

    assert exit_info.value.code == 0
    assert report.exists()
