"""Offline tests for the Synapse PDM CAN protocol. No device required."""
import sys
import types
import unittest
from unittest.mock import patch
from r2_pdm_configurator.pdm_can import (
    CanIds, PdmCanClient, SystemTelemetry, decode_system,
    decode_channel, decode_analogue,
)

class FakeMessage:
    def __init__(self, arbitration_id, data, *, extended=False, remote=False):
        self.arbitration_id = arbitration_id
        self.data = bytes(data)
        self.dlc = len(data)
        self.is_extended_id = extended
        self.is_remote_frame = remote
        self.is_error_frame = False

class FakeBus:
    def __init__(self):
        self.sent = []
        self.received = []
        self.closed = False
    def send(self, msg):
        self.sent.append(msg)
    def recv(self, timeout=0):
        return self.received.pop(0) if self.received else None
    def shutdown(self):
        self.closed = True

def heartbeat():
    return bytes([14, 150, 48, 138, 0, 211, 0, 9])

def channel_frames():
    name = (5 << 10) | (0 << 5) | 13
    return [
        bytes([0, 4, 72, 1, name >> 8, name & 255, 0, 120]),
        bytes([1, 3, 0, 0, 1, 244, 1, 0]),
        bytes([2, 0, 0, 3, 232, 35, 0, 0]),
        bytes([3, 0, 0, 0, 0, 0, 0, 0]),
    ]

class CodecTests(unittest.TestCase):
    def test_system_big_endian(self):
        s = SystemTelemetry()
        decode_system(heartbeat(), s, 100)
        self.assertEqual((s.total_current_a, s.battery_v, s.error_flags), (21.1, 13.8, 9))
    def test_four_channel_frames(self):
        c = decode_channel(dict(enumerate(channel_frames())), 4, 100)
        self.assertEqual((c.name, c.current_a, c.inrush_ms, c.delayed_on_ms), ("FAN", 7.2, 500, 1000))
    def test_missing_channel_frame(self):
        with self.assertRaises(ValueError):
            decode_channel({0: channel_frames()[0]}, 1, 100)
    def test_signed_analogue(self):
        frames = {
            0: bytes([0, 2, 7, 3, 4, 210, 0, 0]),
            1: bytes([1, 255, 255, 248, 48, 0, 0, 0]),
        }
        a = decode_analogue(frames, 2, 100)
        self.assertEqual((a.voltage_v, a.value), (1.234, -2.0))
    def test_id_overlap_or_overflow(self):
        for settings in (dict(config=0x7F0), dict(config=0x720)):
            with self.assertRaises(ValueError):
                CanIds(**settings)

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.time = [100.0]
        self.client = PdmCanClient(self.bus, now=lambda: self.time[0])
        self.stub = patch.dict(sys.modules, {"can": types.SimpleNamespace(
            Message=lambda **kwargs: types.SimpleNamespace(**kwargs))})
        self.stub.start()
    def tearDown(self):
        self.stub.stop()
    def feed_heartbeat(self):
        self.client.ingest(FakeMessage(0x720, heartbeat()))
    def test_no_polling_without_heartbeat(self):
        self.client.tick()
        self.assertEqual(self.bus.sent, [])
    def test_polling_when_online(self):
        self.bus.received.append(FakeMessage(0x720, heartbeat()))
        self.client.tick()
        self.assertEqual({m.arbitration_id for m in self.bus.sent}, {0x700, 0x710, 0x712})
    def test_output_requires_arm(self):
        self.feed_heartbeat()
        with self.assertRaises(PermissionError):
            self.client.set_output(4, 40)
    def test_output_requires_fresh_heartbeat(self):
        self.feed_heartbeat()
        self.client.armed = True
        self.time[0] += 1.01
        with self.assertRaises(PermissionError):
            self.client.set_output(4, 40)
    def test_output_control_classic_can(self):
        self.feed_heartbeat()
        self.client.armed = True
        self.client.set_output(4, 40)
        f = self.bus.sent[-1]
        self.assertEqual((f.arbitration_id, f.is_extended_id, f.data),
                         (0x740, False, bytes([4, 40, 0, 0, 0, 0, 0, 0])))
    def test_config_mask(self):
        self.feed_heartbeat()
        self.client.armed = True
        self.client.configure_channel(2, channel_type=5, low_current=2, high_current=12, retries=3)
        f = self.bus.sent[-1]
        self.assertEqual((f.arbitration_id, f.data),
                         (0x741, bytes([2, 5, 0, 0, 20, 120, 3, 0x39])))
    def test_invalid_config_values(self):
        self.feed_heartbeat()
        self.client.armed = True
        for values in (dict(high_current=18), dict(low_current=-1),
                       dict(retries=256), dict(channel_type=7)):
            with self.assertRaises(ValueError):
                self.client.configure_channel(1, **values)
    def test_channel_multiframe(self):
        self.feed_heartbeat()
        self.client.request_channel(1)
        for f in reversed(channel_frames()):
            self.client.ingest(FakeMessage(0x701, f))
        self.assertEqual(self.client.channels[1].name, "FAN")
        self.assertIsNone(self.client.pending_channel)
    def test_malformed_can_rejected(self):
        self.client.ingest(FakeMessage(0x720, heartbeat(), extended=True))
        self.client.ingest(FakeMessage(0x720, heartbeat(), remote=True))
        self.client.ingest(FakeMessage(0x720, heartbeat()[:-1]))
        self.assertFalse(self.client.online())
        self.assertEqual(self.client.bad_frames, 1)
    def test_no_infinite_pending_request(self):
        self.feed_heartbeat()
        self.client.request_channel(1)
        self.time[0] += 0.36
        self.client.tick()
        self.assertGreaterEqual(self.client.bad_frames, 1)
    def test_disconnect(self):
        self.feed_heartbeat()
        self.client.armed = True
        self.client.close()
        self.assertFalse(self.client.armed)
        self.assertTrue(self.bus.closed)

if __name__ == "__main__":
    unittest.main()
