"""Synapse PDM Classic CAN client.

Unlike R2 Racing Dash, Synapse PDM uses unsegmented 11-bit Classic CAN
messages, not the dash 29-bit bulk transport. Message layout is taken from
SynapsePDM.dbc and firmware CANComms.cpp.

The client is transport-agnostic: pass a python-can BusABC or a fake bus.
No output is enabled or configuration changed merely by connecting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import struct
import sys
import time
from typing import Callable, Optional


@dataclass(frozen=True)
class CanIds:
    channels: int = 0x700
    digital: int = 0x710
    analogue: int = 0x712
    system: int = 0x720
    system_config: int = 0x730
    config: int = 0x740

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not 0 <= value <= 0x7FF:
                raise ValueError(f"{name}: standard CAN ID must be 0..0x7FF")
        # One response per request ID and a 24-message configuration range.
        ranges = [
            {self.channels, self.channels + 1},
            {self.digital, self.digital + 1},
            {self.analogue, self.analogue + 1},
            {self.system, self.system + 1},
            set(range(self.system_config, self.system_config + 7)),
            set(range(self.config, self.config + 24)),
        ]
        if max(max(s) for s in ranges) > 0x7FF:
            raise ValueError("CAN ID range exceeds 11-bit identifiers")
        for index, group in enumerate(ranges):
            if any(group & other for other in ranges[index + 1:]):
                raise ValueError("PDM CAN ID ranges overlap")


@dataclass
class SystemTelemetry:
    alive: int = 0
    total_current_a: float = 0.0
    battery_v: float = 0.0
    temperature_c: int = 0
    current_limit_a: int = 0
    error_flags: int = 0
    mobile_data: bool = False
    gps: bool = False
    motion_detect: bool = False
    last_seen: float = 0.0
    secondary_last_seen: float = 0.0


@dataclass
class ChannelTelemetry:
    number: int
    name: str = ""
    kind: int = 0
    current_a: float = 0.0
    enabled: bool = False
    current_low_a: float = 0.0
    current_high_a: float = 0.0
    retry_count: int = 0
    inrush_ms: int = 0
    active_high: bool = True
    delayed_on: bool = False
    delayed_on_ms: int = 0
    inrush_limit_a: int = 0
    delayed_off: bool = False
    delayed_off_trigger: int = 0
    delayed_off_ms: int = 0
    last_seen: float = 0.0
    attribution: str = "uncorrelated diagnostic"


@dataclass
class AnalogueTelemetry:
    number: int
    kind: int = 0
    units: int = 0
    flags: int = 0
    voltage_v: float = 0.0
    value: float = 0.0
    last_seen: float = 0.0
    attribution: str = "uncorrelated diagnostic"


@dataclass
class ConfigReadback:
    number: int
    expected: dict[str, int]
    sent_at: float
    status: str = "pending"
    discrepancies: dict[str, tuple[int, object]] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        """Legacy replies carry no channel or transaction ID, even if matching."""
        return False


def _unpack_name(packed: int) -> str:
    letters = (((packed >> 10) & 31), ((packed >> 5) & 31), (packed & 31))
    return "".join(chr(65 + v) if v <= 25 else "?" for v in letters)


def decode_system(frame: bytes, status: SystemTelemetry, now: float, second: bool = False) -> None:
    if len(frame) != 8:
        raise ValueError("System status DLC must be 8")
    if second:
        status.mobile_data = bool(frame[3])
        status.gps = bool(frame[4])
        status.motion_detect = bool(frame[5])
        status.secondary_last_seen = now
    else:
        status.alive = frame[0]
        status.current_limit_a = frame[1]
        status.temperature_c = frame[2]
        status.battery_v = frame[3] / 10
        status.total_current_a = int.from_bytes(frame[4:6], "big") / 10
        status.error_flags = int.from_bytes(frame[6:8], "big")
        status.last_seen = now


def decode_channel(frames: dict[int, bytes], number: int, now: float) -> ChannelTelemetry:
    if set(frames) != {0, 1, 2, 3} or any(len(f) != 8 for f in frames.values()):
        raise ValueError("Incomplete four-frame channel response")
    f0, f1, f2, f3 = (frames[i] for i in range(4))
    return ChannelTelemetry(
        number=number, kind=f0[1], current_a=f0[2] / 10, enabled=bool(f0[3]),
        name=_unpack_name(int.from_bytes(f0[4:6], "big")),
        current_low_a=f0[6] / 10, current_high_a=f0[7] / 10,
        retry_count=f1[1], inrush_ms=int.from_bytes(f1[2:6], "big"),
        active_high=bool(f1[6]), delayed_on=bool(f1[7]),
        delayed_on_ms=int.from_bytes(f2[1:5], "big"),
        inrush_limit_a=f2[5], delayed_off=bool(f2[6]),
        delayed_off_trigger=f2[7],
        delayed_off_ms=int.from_bytes(f3[1:5], "big"), last_seen=now,
    )


def decode_analogue(frames: dict[int, bytes], number: int, now: float) -> AnalogueTelemetry:
    if set(frames) != {0, 1} or any(len(f) != 8 for f in frames.values()):
        raise ValueError("Incomplete two-frame analogue response")
    a, b = frames[0], frames[1]
    return AnalogueTelemetry(number, kind=a[1], units=a[2], flags=a[3],
                             voltage_v=int.from_bytes(a[4:6], "big") / 1000,
                             value=int.from_bytes(b[1:5], "big", signed=True) / 1000,
                             last_seen=now)


def open_adapter(interface: str, channel: str, bitrate: int = 500000, *, dash_source: str = ""):
    """Open a python-can adapter. For ECUMaster, reuse the R2 Racing Dash
    32-bit EUSB2CAN bridge (no duplicated proprietary DLL in this repository).

    dash_source points to racing_dash/configurator_qt/src; alternatively set
    R2_DASH_CONFIGURATOR_SRC. Python 32-bit and EUSB2CAN.dll must already be
    installed as documented in racing_dash.
    """
    if bitrate not in (125000, 250000, 500000, 1000000):
        raise ValueError("Unsupported Synapse PDM CAN bitrate")
    import can
    if interface == "eusb2can":
        source = dash_source or os.getenv("R2_DASH_CONFIGURATOR_SRC", "")
        if not source or not os.path.isfile(os.path.join(source, "core", "can_eusb2can.py")):
            raise RuntimeError("ECUMaster needs R2_DASH_CONFIGURATOR_SRC pointing to racing_dash/configurator_qt/src")
        if source not in sys.path:
            sys.path.insert(0, source)
        from core.can_eusb2can import EUSB2CANBus
        return EUSB2CANBus(channel=channel, bitrate=bitrate)
    if interface not in {"pcan", "slcan", "socketcan", "kvaser", "vector", "virtual"}:
        raise ValueError(f"Unsupported CAN adapter: {interface}")
    kwargs = {"interface": interface, "channel": channel}
    if interface != "virtual":
        kwargs["bitrate"] = bitrate
    return can.Bus(**kwargs)


class PdmCanClient:
    """Polling and telemetry. Write methods require explicit arm AND live heartbeat.

    Responses for individual channels have no channel index. Requests are
    strictly serialized; unmatched/late frames cannot be uniquely attributed,
    so always treat status as diagnostic and verify after a config write.
    """
    def __init__(self, bus, ids: CanIds = CanIds(), now: Callable[[], float] = time.monotonic):
        self.bus = bus
        self.ids = ids
        self.now = now
        self.system = SystemTelemetry()
        self.channels: dict[int, ChannelTelemetry] = {}
        self.analogues: dict[int, AnalogueTelemetry] = {}
        self.digital_mask: Optional[int] = None
        self.digital_last_seen = 0.0
        self.last_config_readback: Optional[ConfigReadback] = None
        self._readback_channel: Optional[int] = None
        self._closed = False
        self.transport_errors = 0
        self.channel_cursor = 1
        self.analogue_cursor = 1
        self.pending_channel: Optional[int] = None
        self.pending_analogue: Optional[int] = None
        self._channel_frames: dict[int, bytes] = {}
        self._analogue_frames: dict[int, bytes] = {}
        self._channel_sent = 0.0
        self._analogue_sent = 0.0
        self._last_channel_request = -1e9
        self._last_analogue_request = -1e9
        self._last_digital_request = -1e9
        self.armed = False
        self.bad_frames = 0
        self.rx_frames = 0

    def online(self) -> bool:
        age = self.now() - self.system.last_seen
        return bool(not self._closed and self.system.last_seen and 0 <= age < 1.0)

    def _transport_failed(self) -> None:
        self.transport_errors += 1
        self.armed = False
        self.system.last_seen = 0.0
        self.pending_channel = self.pending_analogue = None
        self._channel_frames.clear()
        self._analogue_frames.clear()
        self._readback_channel = None
        if self.last_config_readback and self.last_config_readback.status == "pending":
            self.last_config_readback.status = "transport failed; unconfirmed"

    def _send(self, arbitration_id: int, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("CAN client is closed")
        if len(data) > 8 or not 0 <= arbitration_id <= 0x7FF:
            raise ValueError("Expected classic 11-bit CAN frame")
        import can
        try:
            self.bus.send(can.Message(arbitration_id=arbitration_id,
                                      is_extended_id=False, data=data), timeout=0.05)
        except Exception:
            self._transport_failed()
            raise

    def request_channel(self, number: int) -> None:
        if not 1 <= number <= 14:
            raise ValueError("Channel must be 1..14")
        if self.pending_channel is not None:
            raise RuntimeError("Another channel response is still pending")
        self.pending_channel = number
        self._channel_frames = {}
        self._channel_sent = self.now()
        self._send(self.ids.channels, bytes([number]) + bytes(7))

    def request_analogue(self, number: int) -> None:
        if not 1 <= number <= 8:
            raise ValueError("Analogue input must be 1..8")
        if self.pending_analogue is not None:
            raise RuntimeError("Another analogue response is still pending")
        self.pending_analogue = number
        self._analogue_frames = {}
        self._analogue_sent = self.now()
        self._send(self.ids.analogue, bytes([number]) + bytes(7))

    def _require_write_authorization(self) -> None:
        if not self.armed or not self.online():
            self.armed = False
            raise PermissionError("Writes require explicit arm and a recent valid system heartbeat")

    def set_output(self, number: int, duty: int) -> None:
        """Actuates an output: call ONLY after explicit user confirmation."""
        self._require_write_authorization()
        if not 1 <= number <= 14 or not 0 <= duty <= 100:
            raise ValueError("Channel 1..14 and control value 0..100 required")
        self._send(self.ids.config, bytes((number, duty)) + bytes(6))

    def set_system_current_limit(self, amps: int) -> None:
        """CAN system config F0. Does NOT guarantee enforcement of a global cutoff."""
        self._require_write_authorization()
        if self.last_config_readback and self.last_config_readback.status == "pending":
            raise RuntimeError("Previous configuration write still awaits diagnostic readback")
        if type(amps) is not int or not 0 <= amps <= 150:
            raise ValueError("System current limit 0..150 A")
        self._send(self.ids.system_config, bytes((amps, 0, 0, 0, 0, 0, 0, 1)))
        self.last_config_readback = ConfigReadback(0, {"current_limit_a": amps}, self.now())

    def configure_channel(self, number: int, *, channel_type: Optional[int] = None,
                          retries: Optional[int] = None,
                          low_current: Optional[int] = None,
                          high_current: Optional[int] = None) -> None:
        """CAN F1: firmware accepts INTEGER amp thresholds only (truncates 0.1A).
        No ACK exists for configuration writes; result must be read back.
        """
        self._require_write_authorization()
        if self.last_config_readback and self.last_config_readback.status == "pending":
            raise RuntimeError("Previous configuration write still awaits diagnostic readback")
        for value in (number, channel_type, retries, low_current, high_current):
            if value is not None and type(value) is not int:
                raise ValueError("Channel configuration requires integer values")
        if low_current is not None and high_current is not None and low_current > high_current:
            raise ValueError("Low-current threshold must not exceed high-current threshold")
        if not 1 <= number <= 14:
            raise ValueError("Channel 1..14 required")
        data = bytearray(8)
        data[0] = number
        mask = 0
        if channel_type is not None:
            if not 0 <= channel_type <= 6:
                raise ValueError("Channel type 0..6 required")
            data[1] = channel_type
            mask |= 1
        if low_current is not None:
            if not 0 <= low_current <= 17:
                raise ValueError("Integer low-current limit 0..17 A required")
            data[4] = low_current * 10
            mask |= 0x08
        if high_current is not None:
            if not 0 <= high_current <= 17:
                raise ValueError("Integer high-current limit 0..17 A required")
            data[5] = high_current * 10
            mask |= 0x10
        if retries is not None:
            if not 0 <= retries <= 255:
                raise ValueError("Retry count 0..255 required")
            data[6] = retries
            mask |= 0x20
        if not mask:
            raise ValueError("No configuration fields selected")
        data[7] = mask
        self._send(self.ids.config + 1, bytes(data))
        expected = {key: value for key, value in (
            ("kind", channel_type), ("retry_count", retries),
            ("current_low_a", low_current), ("current_high_a", high_current),
        ) if value is not None}
        self.last_config_readback = ConfigReadback(number, expected, self.now())
        self._readback_channel = number

    def _expire_requests(self, now: float) -> None:
        for kind in ("channel", "analogue"):
            if (getattr(self, "pending_" + kind) is not None and
                    not 0 <= now - getattr(self, "_" + kind + "_sent") < 0.35):
                setattr(self, "pending_" + kind, None)
                getattr(self, "_" + kind + "_frames").clear()
                self.bad_frames += 1
        readback = self.last_config_readback
        if readback and readback.status == "pending" and not 0 <= now - readback.sent_at < 1.5:
            readback.status = "timeout; unconfirmed"
            self._readback_channel = None

    def ingest(self, msg) -> None:
        if self._closed:
            return
        if (getattr(msg, "is_extended_id", True) or getattr(msg, "is_remote_frame", False)
                or getattr(msg, "is_error_frame", False) or getattr(msg, "is_fd", False)):
            return
        data = bytes(msg.data)
        if len(data) != 8 or getattr(msg, "dlc", len(data)) != 8:
            self.bad_frames += 1
            return
        cid = msg.arbitration_id
        if type(cid) is not int or not 0 <= cid <= 0x7ff:
            self.bad_frames += 1
            return
        now = self.now()
        self._expire_requests(now)  # Expire BEFORE accepting buffered late responses.
        if cid == self.ids.system:
            if not self.online():
                self.armed = False  # A new heartbeat cannot resurrect stale authorization.
            decode_system(data, self.system, now)
            readback = self.last_config_readback
            if readback and readback.number == 0 and readback.status == "pending":
                expected = readback.expected["current_limit_a"]
                observed = self.system.current_limit_a
                if observed != expected:
                    readback.discrepancies = {"current_limit_a": (expected, observed)}
                readback.status = ("discrepancy; unconfirmed" if readback.discrepancies
                                   else "diagnostic match; unconfirmed")
        elif cid == self.ids.system + 1:
            decode_system(data, self.system, now, second=True)
        elif cid == self.ids.digital + 1:
            self.digital_mask = data[0]
            self.digital_last_seen = now
        elif cid == self.ids.channels + 1 and self.pending_channel is not None:
            if data[0] in range(4) and data[0] not in self._channel_frames:
                self._channel_frames[data[0]] = data
                if len(self._channel_frames) == 4:
                    n = self.pending_channel
                    self.channels[n] = decode_channel(self._channel_frames, n, now)
                    readback = self.last_config_readback
                    if (readback and readback.status == "pending" and readback.number == n
                            and self._readback_channel is None
                            and self._channel_sent >= readback.sent_at):
                        readback.discrepancies = {
                            key: (expected, getattr(self.channels[n], key))
                            for key, expected in readback.expected.items()
                            if getattr(self.channels[n], key) != expected
                        }
                        readback.status = ("discrepancy; unconfirmed" if readback.discrepancies
                                           else "diagnostic match; unconfirmed")
                    self.pending_channel = None
                    self._channel_frames = {}
            else:
                self.bad_frames += 1
                self.pending_channel = None
                self._channel_frames.clear()
        elif cid == self.ids.analogue + 1 and self.pending_analogue is not None:
            if data[0] in (0, 1) and data[0] not in self._analogue_frames:
                self._analogue_frames[data[0]] = data
                if len(self._analogue_frames) == 2:
                    n = self.pending_analogue
                    self.analogues[n] = decode_analogue(self._analogue_frames, n, now)
                    self.pending_analogue = None
                    self._analogue_frames = {}
            else:
                self.bad_frames += 1
                self.pending_analogue = None
                self._analogue_frames.clear()
        self.rx_frames += 1

    def tick(self, *, receive_budget: int = 64) -> None:
        """Call from a GUI timer (e.g. every 25ms). recv is nonblocking."""
        if self._closed:
            return
        if type(receive_budget) is not int or not 0 <= receive_budget <= 256:
            raise ValueError("Receive budget must be an integer from 0 to 256")
        for _ in range(receive_budget):
            try:
                msg = self.bus.recv(timeout=0.0)
            except Exception:
                self._transport_failed()
                raise
            if msg is None:
                break
            self.ingest(msg)
        now = self.now()
        self._expire_requests(now)
        if not self.online():
            self.armed = False
            return
        if self.pending_channel is None and now - self._last_channel_request >= 0.2:
            self._last_channel_request = now
            if self._readback_channel is not None:
                number = self._readback_channel
                self._readback_channel = None
                self.request_channel(number)
            else:
                self.request_channel(self.channel_cursor)
                self.channel_cursor = self.channel_cursor % 14 + 1
        if self.pending_analogue is None and now - self._last_analogue_request >= 0.35:
            self._last_analogue_request = now
            self.request_analogue(self.analogue_cursor)
            self.analogue_cursor = self.analogue_cursor % 8 + 1
        if now - self._last_digital_request >= 0.8:
            self._last_digital_request = now
            self._send(self.ids.digital, bytes(8))

    def close(self) -> None:
        self.armed = False
        if self._closed:
            return
        self._closed = True
        self.pending_channel = self.pending_analogue = None
        self._channel_frames.clear()
        self._analogue_frames.clear()
        if self.last_config_readback and self.last_config_readback.status == "pending":
            self.last_config_readback.status = "disconnected; unconfirmed"
        self._readback_channel = None
        self.bus.shutdown()
