"""IXXAT USB-to-CAN and Linux SocketCAN Hardware Abstraction.

Enables seamless switching between physical hardware (IXXAT adapter / socketCAN)
and software loopback emulation for unit testing and offline development.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional
from protocols.can_j1939 import CanFrame

logger = logging.getLogger("ixxat_driver")

try:
    import can  # type: ignore # python-can
    CAN_AVAILABLE = True
except ImportError:
    can = None  # type: ignore
    CAN_AVAILABLE = False


class CanDriverInterface:
    """Abstract interface for CAN bus communications."""

    def send(self, frame: CanFrame) -> bool:
        raise NotImplementedError

    def recv(self, timeout_s: float = 1.0) -> Optional[CanFrame]:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass


class MockCanDriver(CanDriverInterface):
    """In-memory virtual CAN driver for local offline testing."""

    def __init__(self):
        self.tx_queue: List[CanFrame] = []
        self.rx_queue: List[CanFrame] = []
        self.is_open = True

    def send(self, frame: CanFrame) -> bool:
        if not self.is_open:
            return False
        frame.timestamp = time.time()
        self.tx_queue.append(frame)
        return True

    def recv(self, timeout_s: float = 1.0) -> Optional[CanFrame]:
        if not self.is_open or not self.rx_queue:
            return None
        return self.rx_queue.pop(0)

    def inject_rx(self, frame: CanFrame) -> None:
        """Helper to simulate frames received from the physical charger."""
        frame.timestamp = time.time()
        self.rx_queue.append(frame)

    def shutdown(self) -> None:
        self.is_open = False


class HardwareSocketCanDriver(CanDriverInterface):
    """Real hardware driver interfacing with SocketCAN or IXXAT VCI."""

    def __init__(self, channel: str = "can0", bustype: str = "socketcan", bitrate: int = 500000):
        if not CAN_AVAILABLE or can is None:
            raise RuntimeError("python-can is not installed. Install via `pip install python-can`.")
        self.channel = channel
        self.bus = can.interface.Bus(channel=channel, bustype=bustype, bitrate=bitrate)
        logger.info(f"Connected to physical CAN bus: {channel} ({bustype}) at {bitrate}bps")

    def send(self, frame: CanFrame) -> bool:
        if can is None:
            return False
        msg = can.Message(
            arbitration_id=frame.arbitration_id,
            data=frame.data,
            is_extended_id=frame.is_extended_id,
            is_error_frame=frame.is_error_frame,
        )
        try:
            self.bus.send(msg)
            return True
        except Exception as e:
            logger.error(f"Failed to transmit CAN frame: {e}")
            return False

    def recv(self, timeout_s: float = 1.0) -> Optional[CanFrame]:
        try:
            msg = self.bus.recv(timeout=timeout_s)
            if msg is None:
                return None
            return CanFrame(
                arbitration_id=msg.arbitration_id,
                data=bytes(msg.data),
                is_extended_id=msg.is_extended_id,
                is_error_frame=msg.is_error_frame,
                timestamp=msg.timestamp,
            )
        except Exception as e:
            logger.error(f"Error receiving CAN frame: {e}")
            return None

    def shutdown(self) -> None:
        try:
            self.bus.shutdown()
        except Exception:
            pass


def get_can_driver(channel: Optional[str] = None, use_hardware: bool = False) -> CanDriverInterface:
    """Factory creating appropriate CAN driver (hardware or mock)."""
    if use_hardware and CAN_AVAILABLE:
        try:
            return HardwareSocketCanDriver(channel=channel or "can0")
        except Exception as e:
            logger.warning(f"Failed to initialize hardware CAN driver: {e}. Falling back to MockCanDriver.")
    return MockCanDriver()
