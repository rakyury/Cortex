"""Correlated HAL diagnostic CAN v1. RAM-only expressions, never output control.

Firmware uses normal CAN (including ACKs), while physical outputs stay interlocked.
This client never sends on connect and never retries COMMIT automatically.
"""
from dataclasses import dataclass, replace
import secrets
import struct
import time
import zlib

HEARTBEAT, SAMPLE, ACK, READBACK = 0x580, 0x581, 0x582, 0x583
BEGIN, DATA, COMMIT, ABORT, QUERY = range(0x590, 0x595)
READABLE_CHANNELS = frozenset((*range(200, 214), 230, *range(240, 245)))
SAMPLE_CHANNELS = READABLE_CHANNELS | {300}
HEARTBEAT_TIMEOUT = 0.5
SAMPLE_TIMEOUT = 1.0
REPLY_TIMEOUT = 0.35
MAX_PROGRAM_BYTES = 128


def validate_program(code: bytes) -> bytes:
    """Validate the deliberately narrow device profile before any CAN write."""
    if type(code) is not bytes or not 1 <= len(code) <= MAX_PROGRAM_BYTES:
        raise ValueError("HAL program must be immutable bytes, length 1..128")
    pc, depth = 0, 0
    while pc < len(code):
        op = code[pc]
        pc += 1
        if op == 0x01:
            if pc + 4 > len(code):
                raise ValueError("Truncated constant")
            pc += 4
            depth += 1
        elif op == 0x02:
            if pc + 2 > len(code):
                raise ValueError("Truncated channel")
            channel = int.from_bytes(code[pc:pc + 2], "little")
            if channel not in READABLE_CHANNELS:
                raise ValueError("HAL source must be an implemented diagnostic channel")
            pc += 2
            depth += 1
        elif op in {0x10, 0x11, *range(0x30, 0x36), 0x40, 0x41, 0x50, 0x51}:
            if depth < 2:
                raise ValueError("Stack underflow")
            depth -= 1
        elif op == 0x42:
            if depth < 1:
                raise ValueError("Stack underflow")
        elif op == 0xff:
            if pc != len(code) or depth != 1:
                raise ValueError("END must be final with exactly one value")
            return code
        else:
            raise ValueError(f"Unsupported HAL opcode 0x{op:02x}")
        if depth > 48:
            raise ValueError("HAL stack exceeds 48 values")
    raise ValueError("Missing END")


@dataclass(frozen=True)
class HalSample:
    channel: int
    value: int
    quality: int
    sequence: int
    last_seen: float


@dataclass(frozen=True)
class HalUploadStatus:
    state: str = "idle"
    tid: int = 0
    offset: int = 0
    length: int = 0
    expected_crc: int | None = None
    active_crc: int | None = None
    error: str = ""

    @property
    def verified(self) -> bool:
        return self.state == "verified"


class HalCanClient:
    def __init__(self, bus, now=time.monotonic):
        self.bus, self.now = bus, now
        self.armed = False
        self.samples: dict[int, HalSample] = {}
        self.upload = HalUploadStatus()
        self.heartbeat_last_seen = 0.0
        self.compatible = False
        self.interlocked = False
        self.upload_supported = False
        self.vm_active = False
        self.rx_frames = 0
        self.bad_frames = 0
        self.transport_errors = 0
        self._closed = False
        self._heartbeat_sequence = None
        self._uptime = None
        self._pending = None  # (op, expected offset, deadline)
        self._acked = False
        self._readback_crc = None
        self._code = b""

    def online(self) -> bool:
        return bool(not self._closed and self._heartbeat_sequence is not None
                    and self.compatible and 0 <= self.now() - self.heartbeat_last_seen < HEARTBEAT_TIMEOUT)

    def can_upload(self) -> bool:
        return self.online() and self.interlocked and self.upload_supported

    def sample_valid(self, channel: int) -> bool:
        sample = self.samples.get(channel)
        return bool(self.online() and sample and sample.quality == 0
                    and 0 <= self.now() - sample.last_seen < SAMPLE_TIMEOUT
                    and (channel != 300 or self.vm_active))

    def _reset_session(self, reason: str) -> None:
        self.armed = False
        self.samples.clear()
        self._pending = None
        self._acked = False
        self._readback_crc = None
        self._code = b""
        self.upload = HalUploadStatus(error=reason)
        self.compatible = self.interlocked = self.upload_supported = self.vm_active = False
        self._heartbeat_sequence = self._uptime = None
        self.heartbeat_last_seen = 0.0

    def _expire(self) -> None:
        now = self.now()
        if self._heartbeat_sequence is not None and not 0 <= now - self.heartbeat_last_seen < HEARTBEAT_TIMEOUT:
            self._reset_session("Heartbeat expired; session cleared")
        if self._pending is not None and now >= self._pending[2]:
            state = "unknown" if self._pending[0] == 2 else "failed"
            self.upload = replace(self.upload, state=state, error="Reply timeout; no automatic retry")
            self._pending = None
            self._acked = False
            self._readback_crc = None

    def _send(self, cid: int, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("HAL client is closed")
        import can
        try:
            self.bus.send(can.Message(arbitration_id=cid, is_extended_id=False,
                                      data=payload, check=True), timeout=0.05)
        except Exception:
            self.transport_errors += 1
            self._reset_session("Transport failed; device state unknown")
            raise

    def _new_tid(self) -> int:
        # Reuse is especially undesirable after a lost COMMIT reply.
        previous = self.upload.tid
        tid = secrets.randbelow(0xffffffff) + 1
        while tid == previous:
            tid = secrets.randbelow(0xffffffff) + 1
        return tid

    def _request(self, op: int, offset: int, payload: bytes) -> None:
        self._pending = (op, offset, self.now() + REPLY_TIMEOUT)
        self._acked = False
        self._readback_crc = None
        self._send(BEGIN + op, payload)

    def begin_upload(self, code: bytes, *, confirmed: bool = False) -> None:
        self._expire()
        validate_program(code)
        if self._pending is not None:
            raise RuntimeError("A HAL transaction is already pending")
        if not self.can_upload() or not self.armed or confirmed is not True:
            raise PermissionError("Upload requires fresh compatible interlocked HAL heartbeat, arm and explicit confirmation")
        tid = self._new_tid()
        self.armed = False  # One explicit authorization starts one transaction.
        self._code = code
        self.upload = HalUploadStatus("begin", tid, 0, len(code), zlib.crc32(code))
        self._request(0, 0, struct.pack("<IHBB", tid, len(code), 1, 0))

    def query_readback(self, expected_crc: int | None = None) -> None:
        """Explicit read-only reconciliation, including an unknown COMMIT."""
        self._expire()
        if not self.online():
            raise PermissionError("Query requires a fresh compatible HAL heartbeat")
        if self._pending is not None:
            raise RuntimeError("A HAL transaction is already pending")
        if expected_crc is None:
            expected_crc = self.upload.expected_crc
        if expected_crc is not None and (type(expected_crc) is not int or not 0 <= expected_crc <= 0xffffffff):
            raise ValueError("Expected CRC must be uint32")
        tid = self._new_tid()
        self.upload = HalUploadStatus("query", tid, expected_crc=expected_crc)
        self._request(4, 0, struct.pack("<II", tid, 0))

    def cancel(self) -> None:
        self._expire()
        self.armed = False
        if self._pending is None:
            return
        op = self._pending[0]
        if op in (0, 1) and self.online():
            self.upload = replace(self.upload, state="abort")
            self._request(3, 0, struct.pack("<II", self.upload.tid, 0))
        else:
            # ABORT cannot undo an already-sent COMMIT or remove active code.
            self.upload = replace(self.upload, state="unknown" if op == 2 else "cancelled",
                                  error="Cancelled locally; no active program was removed")
            self._pending = None
            self._acked = False
            self._readback_crc = None

    def _advance_upload(self) -> None:
        offset = self.upload.offset
        if offset < len(self._code):
            chunk = self._code[offset:offset + 2]
            self.upload = replace(self.upload, state="data")
            self._request(1, offset + len(chunk),
                          struct.pack("<IH", self.upload.tid, offset) + chunk.ljust(2, b"\0"))
        else:
            self.upload = replace(self.upload, state="commit")
            self._request(2, len(self._code), struct.pack("<II", self.upload.tid, self.upload.expected_crc))

    def _finish_readback(self) -> None:
        if not self._acked or self._readback_crc is None:
            return
        crc = self._readback_crc
        expected = self.upload.expected_crc
        state = "readback" if expected is None else "verified" if expected == crc else "failed"
        self.upload = replace(self.upload, state=state, active_crc=crc,
                              error="" if state != "failed" else "Active program CRC mismatch")
        self._pending = None

    def ingest(self, msg) -> None:
        if self._closed:
            return
        self._expire()
        if (getattr(msg, "is_extended_id", True) or getattr(msg, "is_remote_frame", False)
                or getattr(msg, "is_error_frame", False) or getattr(msg, "is_fd", False)):
            self.bad_frames += 1
            return
        cid = getattr(msg, "arbitration_id", None)
        if cid not in (HEARTBEAT, SAMPLE, ACK, READBACK):
            return
        payload = bytes(msg.data)
        if len(payload) != 8 or getattr(msg, "dlc", len(payload)) != 8:
            self.bad_frames += 1
            return
        self.rx_frames += 1
        if cid == HEARTBEAT:
            version, flags, sequence, uptime = struct.unpack("<BBHI", payload)
            if version != 1 or flags & ~7 or not flags & 1:
                self.bad_frames += 1
                self._reset_session("Incompatible or non-interlocked heartbeat")
                return
            if self._heartbeat_sequence is not None:
                uptime_delta = (uptime - self._uptime) & 0xffffffff
                if uptime_delta >= 0x80000000:
                    self._reset_session("Device restart; session cleared")
                elif not 0 < ((sequence - self._heartbeat_sequence) & 0xffff) < 0x8000:
                    self.bad_frames += 1
                    return
            self.compatible = self.interlocked = True
            self.upload_supported = bool(flags & 4)
            self.vm_active = bool(flags & 2)
            self.heartbeat_last_seen = self.now()
            self._heartbeat_sequence, self._uptime = sequence, uptime
            if not self.upload_supported:
                self.armed = False
                if self._pending is not None and self._pending[0] in (0, 1, 2):
                    self.upload = replace(self.upload, state="unknown" if self._pending[0] == 2 else "failed",
                                          error="Upload capability withdrawn")
                    self._pending = None
            return
        if not self.online():
            return
        if cid == SAMPLE:
            channel, quality, sequence, value = struct.unpack("<HBBi", payload)
            if channel not in SAMPLE_CHANNELS or quality not in (0, 1):
                self.bad_frames += 1
                return
            old = self.samples.get(channel)
            if (old is not None and 0 <= self.now() - old.last_seen < SAMPLE_TIMEOUT
                    and not 0 < ((sequence - old.sequence) & 255) < 128):
                self.bad_frames += 1
                return
            if quality == 0 and ((200 <= channel <= 213 and value != 0) or (channel == 230 and value != 1)):
                self.bad_frames += 1
                self._reset_session("Output interlock diagnostic violation")
                return
            if channel == 300 and quality == 0 and not self.vm_active:
                self.bad_frames += 1
                return
            self.samples[channel] = HalSample(channel, value, quality, sequence, self.now())
            return
        if self._pending is None:
            return
        tid = int.from_bytes(payload[:4], "little")
        if tid != self.upload.tid:
            self.bad_frames += 1
            return
        if cid == READBACK:
            if self._pending[0] not in (2, 4):
                self.bad_frames += 1
                return
            crc = int.from_bytes(payload[4:], "little")
            if self._readback_crc is not None and self._readback_crc != crc:
                self.bad_frames += 1
                self.upload = replace(self.upload, state="failed", error="Conflicting correlated readback")
                self._pending = None
                return
            self._readback_crc = crc
            self._finish_readback()
            return
        _, op, status, offset = struct.unpack("<IBBH", payload)
        if op != self._pending[0] or status > 7:
            self.bad_frames += 1
            return
        if status != 0:
            self.upload = replace(self.upload, state="failed", error=f"Device rejected operation {op}: status {status}")
            self._pending = None
            return
        if offset != self._pending[1]:
            self.bad_frames += 1
            return
        if op in (2, 4):
            self._acked = True
            self._finish_readback()
        elif op == 3:
            self.upload = replace(self.upload, state="cancelled", offset=0)
            self._pending = None
        else:
            self.upload = replace(self.upload, offset=offset)
            self._advance_upload()

    def tick(self, *, receive_budget: int = 64) -> None:
        if self._closed:
            return
        if type(receive_budget) is not int or not 0 <= receive_budget <= 256:
            raise ValueError("Receive budget must be 0..256")
        self._expire()
        for _ in range(receive_budget):
            try:
                msg = self.bus.recv(timeout=0.0)
            except Exception:
                self.transport_errors += 1
                self._reset_session("Transport failed; device state unknown")
                raise
            if msg is None:
                break
            self.ingest(msg)
        self._expire()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reset_session("Disconnected; session cleared")
        self.bus.shutdown()
