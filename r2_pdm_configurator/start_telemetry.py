"""Readonly coherent CAN scheduler snapshots; no command or actuator API."""
from dataclasses import dataclass
import struct
import zlib

START_BEGIN, START_DATA, START_END = 0x585, 0x586, 0x587
START_IDS = (START_BEGIN, START_DATA, START_END)
ASSEMBLY_TIMEOUT = 1.5
SNAPSHOT_TIMEOUT = 2.5
MAX_PAYLOAD = 408
STATES = ('OFF', 'QUEUED', 'STARTING', 'ON', 'FAULT')
REASONS = ('OK', 'BAD_CONFIG', 'BAD_CHANNEL', 'OLD_REQUEST', 'EXPIRED', 'UNHEALTHY',
           'UNKNOWN_OFF', 'SLOTS', 'TOTAL_BUDGET', 'GROUP_BUDGET', 'COOLDOWN',
           'DEADLINE', 'FAULT_LATCHED', 'CANCELLED')


@dataclass(frozen=True)
class StartGroup:
    id: int
    budget_ma: int
    reserved_ma: int


@dataclass(frozen=True)
class StartChannel:
    channel: int
    state: str
    reason: str
    reserve_ma: int
    group: int
    queue_position: int | None
    slot_reserved: bool


@dataclass(frozen=True)
class StartSnapshot:
    profile: int
    count: int
    slots: int | None
    available: bool
    simulation_only: bool
    inhibited: bool
    physical_outputs_off: bool
    sequence: int
    captured_ms: int
    last_seen: float
    total_budget_ma: int | None
    total_reserved_ma: int | None
    total_measured_ma: int | None
    starts_reserved: int | None
    queue: tuple[int, ...]
    groups: tuple[StartGroup, ...]
    channels: tuple[StartChannel, ...]


def capture_age(uptime, captured):
    """Serial uint32 device time, with at most200ms future clock/cadence skew."""
    if uptime is None:
        return None
    age = (uptime - captured) & 0xffffffff
    if age >= 0x100000000 - 200:
        return 0
    return age if age < 0x80000000 else None


class StartTelemetryDecoder:
    def __init__(self):
        self.rx_frames = self.bad_frames = 0
        self.reset()

    def reset(self):
        self.snapshot = None
        self.error = 'No complete scheduler snapshot'
        self._highwater = None
        self._begin = None
        self._sequence = self._length = 0
        self._started = 0.0
        self._chunks = []

    def reject(self, reason):
        self.bad_frames += 1
        self.snapshot = None
        self._begin = None
        self._chunks = []
        self.error = reason

    def expire(self, now):
        if self._begin is not None and not 0 <= now - self._started < ASSEMBLY_TIMEOUT:
            self.reject('Scheduler snapshot assembly expired')

    def valid(self, now, uptime):
        sample = self.snapshot
        if sample is None:
            return False
        age = capture_age(uptime, sample.captured_ms)
        valid = 0 <= now - sample.last_seen < SNAPSHOT_TIMEOUT and age is not None and age < 2500
        if not valid:
            self.error = 'Scheduler snapshot stale'
        return valid

    def ingest(self, cid, payload, now, uptime):
        self.rx_frames += 1
        self.expire(now)
        if cid == START_BEGIN:
            version, reserved, length, sequence = struct.unpack('<BBHI', payload)
            if version != 1 or reserved != 0 or not 24 <= length <= MAX_PAYLOAD or length % 8:
                self.reject('Unsupported scheduler header')
                return
            if self._begin is not None and payload == self._begin:
                return  # Exact duplicate does not renew the assembly deadline.
            if self._highwater is not None and not 0 < ((sequence - self._highwater) & 0xffffffff) < 0x80000000:
                self.reject('Old or conflicting scheduler generation')
                return
            self._highwater = self._sequence = sequence
            self._length, self._begin, self._started, self._chunks = length, payload, now, []
            return
        if self._begin is None:
            self.reject('Scheduler fragment without active header')
            return
        if cid == START_DATA:
            short_sequence, index = struct.unpack('<HB', payload[:3])
            chunk = payload[3:]
            if short_sequence != (self._sequence & 0xffff) or index >= (self._length + 4) // 5:
                self.reject('Scheduler fragment generation/index mismatch')
            elif index < len(self._chunks):
                if self._chunks[index] != chunk:
                    self.reject('Conflicting scheduler duplicate')
            elif index != len(self._chunks):
                self.reject('Out-of-order scheduler fragment')
            else:
                remaining = self._length - index * 5
                if remaining < 5 and any(chunk[remaining:]):
                    self.reject('Nonzero scheduler padding')
                else:
                    self._chunks.append(chunk)
            return
        sequence, crc = struct.unpack('<II', payload)
        if sequence != self._sequence or len(self._chunks) != (self._length + 4) // 5:
            self.reject('Incomplete scheduler snapshot')
            return
        body = b''.join(self._chunks)[:self._length]
        if zlib.crc32(self._begin + body) != crc:
            self.reject('Scheduler snapshot CRC mismatch')
            return
        try:
            sample = self._decode(body, now, uptime)
        except ValueError as error:
            self.reject(str(error))
            return
        self.snapshot = sample
        self.error = ''
        self._begin, self._chunks = None, []

    def _decode(self, body, now, uptime):
        profile, count, flags, slots, starts, group_count, queued, reserved, captured, budget, total, measured = struct.unpack('<8B4I', body[:24])
        if (profile not in (14, 24) or count != profile or flags & ~31 or not flags & 8 or reserved or
                group_count > 24 or queued > count or len(body) != 24 + 8 * (group_count + count)):
            raise ValueError('Invalid scheduler profile/count/flags/length')
        age = capture_age(uptime, captured)
        if age is None or age > 2000:
            raise ValueError('Scheduler capture is stale or outside heartbeat time')
        available, simulation, inhibited = bool(flags & 1), bool(flags & 2), bool(flags & 4)
        measurement_unavailable = bool(flags & 16)
        if measurement_unavailable and (not available or not inhibited or measured != 0):
            raise ValueError('Invalid unavailable scheduler measurement flags/value')
        if not available:
            if flags != 12 or profile != 14 or slots or starts or group_count or queued or budget or total or measured:
                raise ValueError('Invalid unavailable scheduler metadata')
            if any(body[24+i*8:32+i*8] != bytes((0,1,0,255,0,0,0,0)) for i in range(count)):
                raise ValueError('Unavailable scheduler contains invented live data')
            return StartSnapshot(profile,count,None,False,False,True,True,self._sequence,captured,now,
                                 None,None,None,None,(),(),())
        if (not 2 <= slots <= 4 or starts > slots or not 1 <= group_count <= 24 or
                budget == 0 or total > budget or (profile == 24 and not simulation)):
            raise ValueError('Invalid scheduler capacity/budget')
        groups = tuple(StartGroup(i,*struct.unpack_from('<II',body,24+i*8)) for i in range(group_count))
        if any(g.budget_ma == 0 or g.reserved_ma > g.budget_ma for g in groups) or sum(g.reserved_ma for g in groups) > total:
            raise ValueError('Invalid scheduler group budget')
        channels, queue, reserves = [], {}, [0] * group_count
        for i in range(count):
            state_flags, reason, position, group, reserve = struct.unpack_from('<BBBBI',body,24+group_count*8+i*8)
            state, slot = state_flags & 7, bool(state_flags & 128)
            if (state_flags & 0x78 or state >= len(STATES) or reason >= len(REASONS) or group >= group_count or
                position > queued or (state == 1) != (position != 0) or
                (state in (0,1) and (reserve or slot)) or (state == 2 and (not slot or reserve == 0)) or
                (state == 3 and (slot or reserve == 0)) or (slot and reserve == 0)):
                raise ValueError('Invalid scheduler channel state')
            if position:
                if position in queue:
                    raise ValueError('Duplicate scheduler queue position')
                queue[position] = i+1
            reserves[group] += reserve
            channels.append(StartChannel(i+1,STATES[state],REASONS[reason],reserve,group,position or None,slot))
        if (set(queue) != set(range(1,queued+1)) or sum(c.slot_reserved for c in channels) != starts or
                any(reserves[g.id] > g.reserved_ma for g in groups)):
            raise ValueError('Inconsistent scheduler queue/reservation totals')
        return StartSnapshot(profile,count,slots,True,simulation,inhibited,True,self._sequence,captured,now,
            budget,total,None if measurement_unavailable else measured,starts,
            tuple(queue[i] for i in range(1,queued+1)),groups,tuple(channels))
