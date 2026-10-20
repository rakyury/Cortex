"""Read-only, bounded telemetry snapshots for offline VM previews."""
from types import MappingProxyType


def int32(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("channel values must be int32 integers")
    return value


class Snapshot:
    """No write, transport, GPIO, or device configuration capability."""

    def __init__(self, values=None):
        data = {0: 0, 1: 1, 10: 0, 11: 0, 20: 0, 21: 100, 30: 0, 31: 0}
        if values is not None:
            if len(values) > 128:
                raise ValueError("snapshot exceeds 128 channels")
            for key, value in values.items():
                if type(key) is not int or not 0 <= key <= 65535:
                    raise ValueError("channel IDs must be uint16 integers")
                # Peak IDs require history and are outside this snapshot profile.
                if 0xA000 <= key < 0xE000:
                    raise ValueError("peak channels are outside preview profile v1")
                data[key] = int32(value)
        if len(data) > 128:
            raise ValueError("snapshot exceeds 128 channels including system channels")
        self.values = MappingProxyType(data)

    def get(self, channel_id):
        if 0xA000 <= channel_id < 0xE000:
            raise ValueError("peak reads require history and are unsupported")
        return self.values.get(channel_id, 0)
