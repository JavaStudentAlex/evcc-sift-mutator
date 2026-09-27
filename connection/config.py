# ---------------------------------------------------------------------------
# Vendored from https://github.com/eMobMarkus/evcc_simulator (commit c884950)
# -- the YFN x EU Energy Hackathon 2026 (EnBW track) reference EVCC connection
# stack, shared by the organizers as an example to build on. Incorporated into
# EVCC-SIFT-Mutator on 2026-09-27 to provide the real hardware/charger
# transport backend for the AI mutation harness.
#
# Integration fixes are limited to config imports; upstream carried no LICENSE
# file. See connection/README.md for provenance and licensing details.
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import platform
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LOCAL_CONFIG_PATH = Path(__file__).parent / "local_config.json"


@dataclass
class NetworkConfig:
    # Name/link-local address of the "Ethernet to EVSE" Ethernet adapter.
    # Since we take on the role of the EVCC (vehicle side) here, we talk to
    # the (simulated) EVSE THROUGH this adapter.
    interface_name: str = "Ethernet to EVSE"
    # Link-local IPv6 address of this adapter, including the scope ID if
    # known, e.g. "fe80::1234:5678:9abc:def0%12". On Windows, determine it
    # via `netsh interface ipv6 show interface` / `ipconfig`.
    local_link_local_addr: str = ""
    # Optional: specify the interface index directly (e.g. the number after
    # "%" in the link-local address from ipconfig). Windows'
    # socket.if_nametoindex() often doesn't match the adapter name from
    # ipconfig exactly -- if set, this index is used and interface_name is
    # ignored.
    interface_index: int | None = None
    sdp_multicast_addr: str = "ff02::1"
    sdp_port: int = 15118
    sdp_timeout_s: float = 5.0
    # Retry the SDPRequest multiple times instead of giving up after a
    # single timeout -- in case something needs a little extra time to
    # catch up somewhere.
    sdp_max_attempts: int = 10
    tcp_connect_timeout_s: float = 5.0
    tcp_connect_attempts: int = 5
    tcp_connect_retry_delay_s: float = 1.0
    use_tls: bool = False  # the SDP response actually determines whether TLS is needed
    # MAC address of the local PLC modem chip for SLAC (see slac.py) --
    # default is the well-known Qualcomm/Atheros QCA7000 address; adjust as
    # needed depending on the modem fitted.
    plc_modem_mac_hex: str = "00b052000001"


@dataclass
class CanConfig:
    # Direct control of the CP/PP simulation hardware on the test bench's
    # analyzer CAN bus, see can_control.py.
    channel: int = 0
    bustype: str = "ixxat"
    # Was set to 500000 for a long time (a wrong assumption, never
    # verified). Empirically confirmed via can_bus_monitor.py:
    # canControlGetStatus() shows btr0=0x00/btr1=0x14 in the healthy state
    # -- these are IXXAT's own presets for 1000000 (CAN_BT0_1000KB/
    # CAN_BT1_1000KB), NOT for 500000 (CAN_BT0_500KB/CAN_BT1_500KB would be
    # btr1=0x1C). This turned out to be the actual root cause of a longer
    # series of CAN init failures.
    bitrate: int = 1_000_000
    r2_ohm: int = 1300
    r3_ohm: int = 2740


@dataclass
class EvccConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    can: CanConfig = field(default_factory=CanConfig)
    evcc_id: str = "DEADBEEF1234"
    session_id: str = "00"


def load_local_config(cfg: EvccConfig, path: Path = LOCAL_CONFIG_PATH) -> EvccConfig:
    """Overrides machine-specific values (network interface, CAN channel)
    from a local, NON-versioned configuration file. If the file is missing,
    the defaults from NetworkConfig/CanConfig remain unchanged (a warning is
    logged) -- callers should call ensure_local_config() beforehand so that
    the file actually exists."""
    if not path.exists():
        logger.warning(
            "No local config found (%s) -- using defaults from config.py.",
            path,
        )
        return cfg
    data = json.loads(path.read_text())
    for key, value in data.get("network", {}).items():
        setattr(cfg.network, key, value)
    for key, value in data.get("can", {}).items():
        setattr(cfg.can, key, value)
    return cfg


def _list_network_adapters() -> list[tuple[int, str, str]]:
    """Returns (index, name, readable description) for all network adapters.

    On Windows, Python's socket.if_nameindex() only returns generic names
    like "ethernet_32768" -- useless for identifying the right adapter.
    There, query Get-NetAdapter via PowerShell instead, which provides the
    actual hardware description (e.g. "Intel(R) Ethernet Server Adapter
    I210"). On other systems (Linux/macOS), if_nameindex() remains the only
    practical source."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-NetAdapter | Select-Object ifIndex,Name,InterfaceDescription "
                    "| ConvertTo-Json -Compress",
                ],
                capture_output=True, text=True, timeout=10, check=True,
            )
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            return [(item["ifIndex"], item["Name"], item["InterfaceDescription"]) for item in data]
        except Exception:
            logger.exception(
                "Get-NetAdapter failed -- falling back to socket.if_nameindex() "
                "(which may then only return generic names)."
            )
    try:
        return [(idx, name, name) for idx, name in socket.if_nameindex()]
    except (OSError, AttributeError):
        return []


def _detect_windows_link_local(index: int) -> str | None:
    """Determines an interface's link-local IPv6 address via PowerShell
    (Get-NetIPAddress) -- with the scope ID (%index) appended, just as
    NetworkConfig.local_link_local_addr expects. Returns None if this fails
    (e.g. not Windows, no link-local entry found)."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"Get-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv6 "
                "| Where-Object { $_.IPAddress -like 'fe80::*' } "
                "| Select-Object -First 1 -ExpandProperty IPAddress",
            ],
            capture_output=True, text=True, timeout=10, check=True,
        )
        addr = result.stdout.strip()
        if addr:
            return f"{addr}%{index}"
    except Exception:
        logger.exception("Automatic detection of the link-local address failed.")
    return None


def _prompt_network_interface() -> dict:
    print("\n=== Network interface (for SDP/ISO-15118, PLC adapter) ===")
    adapters = _list_network_adapters()
    if adapters:
        for idx, name, description in adapters:
            print(f"  [{idx}] {name} -- {description}")
    else:
        print("  (No automatic interface list available on this system.)")
    while True:
        choice = input("Interface index (from the list above, or `ipconfig`/`netsh"
                        " interface ipv6 show interface`): ").strip()
        try:
            index = int(choice)
        except ValueError:
            print("Please enter a number.")
            continue
        break
    matching = [name for idx, name, _ in adapters if idx == index]
    name = matching[0] if matching else input("Interface name: ").strip()

    detected = _detect_windows_link_local(index) if platform.system() == "Windows" else None
    if detected:
        override = input(
            f"Link-local IPv6 address auto-detected: {detected}\n"
            "Press Enter to accept, or enter your own value: "
        ).strip()
        link_local = override or detected
    else:
        link_local = input(
            f"Link-local IPv6 address of '{name}' including scope (e.g. fe80::...%{index}), "
            f"from `ipconfig`: "
        ).strip()
    return {"interface_name": name, "interface_index": index, "local_link_local_addr": link_local}


def _prompt_can_channel() -> dict:
    print("\n=== CAN channel (ixxat adapter on the test bench's analyzer bus) ===")
    channels = []
    try:
        from connection.can_control import list_available_channels

        channels = list_available_channels()
    except Exception:
        logger.exception("Automatic CAN channel detection failed.")
    channel = None
    bustype = None
    if channels:
        for i, ch in enumerate(channels):
            print(f"  [{i}] {ch}")
        choice = input(
            "Adopt channel number from the list above (or press Enter for manual entry): "
        ).strip()
        if choice:
            selected = channels[int(choice)]
            channel = selected.get("channel", 0)
            bustype = selected.get("interface", "ixxat")
    if channel is None:
        print("Manual entry (e.g. determine via Ixxat's \"VCI Device Manager\"):")
        channel = int(input("CAN channel index [0]: ").strip() or "0")
        bustype = input("CAN bus type [ixxat]: ").strip() or "ixxat"
    # Bitrate is not prompted for -- on the analyzer bus it is effectively
    # always CanConfig.bitrate (see the comment there: empirically confirmed
    # as 1000000, not 500000). Anyone needing a different value can edit
    # local_config.json directly afterwards.
    return {"channel": channel, "bustype": bustype, "bitrate": CanConfig().bitrate}


def ensure_local_config(path: Path = LOCAL_CONFIG_PATH) -> None:
    """Interactively creates local_config.json if it doesn't exist yet --
    asks for the network interface and CAN channel (with auto-detection
    where possible) and writes the result out. Files that already exist are
    left untouched and are not prompted for again."""
    if path.exists():
        return
    print(
        f"No local configuration found ({path}).\n"
        "One-time setup wizard -- the answers will be saved locally to "
        f"{path.name} (not versioned, see .gitignore)."
    )
    data = {
        "network": _prompt_network_interface(),
        "can": _prompt_can_channel(),
    }
    path.write_text(json.dumps(data, indent=2))
    print(f"Saved: {path}\n")
