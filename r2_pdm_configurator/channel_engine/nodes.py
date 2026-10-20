"""Bounded R2 filter, timer, table and alarm previews; no device operations."""
from collections import deque
from . import int32
from ..vm.preview import cdiv, ratio_mul


def bounded(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"integer must be in {lo}..{hi}")
    return value


class MovingAverage:
    def __init__(self, window=4):
        self._samples = deque(maxlen=bounded(window, 1, 16))

    def update(self, value):
        self._samples.append(int32(value))
        return cdiv(sum(self._samples), len(self._samples))


class ExponentialAverage:
    def __init__(self, alpha):
        self._alpha = bounded(alpha, 0, 255)
        self._accumulator = None

    def update(self, value):
        scaled = int32(value) * 65536
        if self._accumulator is None:
            self._accumulator = scaled
        self._accumulator = cdiv(self._accumulator * (255 - self._alpha) + scaled * self._alpha, 255)
        return (self._accumulator + 32768) >> 16


class OnDelay:
    def __init__(self, preset_ms):
        self._preset = bounded(preset_ms, 0, 0x7FFFFFFF)
        self._start = None
        self._done = False

    def update(self, value, now_ms):
        value = int32(value)
        now_ms = bounded(now_ms, 0, 0xFFFFFFFF)
        if not value:
            self._start, self._done = None, False
        else:
            if self._start is None:
                self._start = now_ms
            if not self._done and ((now_ms - self._start) & 0xFFFFFFFF) >= self._preset:
                self._done = True
        return int(self._done)


class HysteresisAlarm:
    """Same inclusive thresholds as R2 FILTER_HYST, telemetry only."""
    def __init__(self, low, high):
        self._low, self._high = int32(low), int32(high)
        if low > high:
            raise ValueError("alarm low must not exceed high")
        self._state = 0

    def update(self, value):
        value = int32(value)
        if not self._state and value >= self._high:
            self._state = 1
        elif self._state and value <= self._low:
            self._state = 0
        return self._state


class LinearTable:
    def __init__(self, axis, values):
        if len(axis) != len(values) or not 1 <= len(axis) <= 16:
            raise ValueError("table requires 1..16 matching axis/value entries")
        self._axis = tuple(int32(x) for x in axis)
        self._values = tuple(int32(y) for y in values)
        if any(a >= b for a, b in zip(self._axis, self._axis[1:])):
            raise ValueError("table axis must be strictly increasing")

    def lookup(self, value):
        x = int32(value)
        if x <= self._axis[0]:
            return self._values[0]
        if x >= self._axis[-1]:
            return self._values[-1]
        for i, upper in enumerate(self._axis[1:]):
            if x <= upper:
                lo, y = self._axis[i], self._values[i]
                result = y + ratio_mul(x - lo, self._values[i + 1] - y, upper - lo)
                return min(0x7FFFFFFF, max(-0x80000000, result))
        raise AssertionError("validated axis did not contain input")
