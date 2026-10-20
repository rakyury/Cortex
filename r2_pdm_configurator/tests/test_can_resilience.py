"""Host-only fault injection; no physical adapters or output actuation."""
import sys
import types
import unittest
from unittest.mock import Mock, patch
from r2_pdm_configurator.pdm_can import PdmCanClient, open_adapter
from r2_pdm_configurator.tests.test_can_protocol import FakeBus, FakeMessage, heartbeat, channel_frames


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.time = 100.0
        self.client = PdmCanClient(self.bus, now=lambda: self.time)
        self.stub = patch.dict(sys.modules, {"can": types.SimpleNamespace(
            Message=lambda **kwargs: types.SimpleNamespace(**kwargs))})
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def heartbeat(self):
        self.client.ingest(FakeMessage(0x720, heartbeat()))

    def fragments(self):
        for frame in channel_frames():
            self.client.ingest(FakeMessage(0x701, frame))

    def test_secondary_cannot_create_or_refresh_primary_heartbeat(self):
        self.client.ingest(FakeMessage(0x721, bytes(8)))
        self.assertFalse(self.client.online())
        self.heartbeat()
        self.time += 1.01
        self.client.ingest(FakeMessage(0x721, bytes(8)))
        self.assertFalse(self.client.online())
        self.assertEqual(self.client.system.secondary_last_seen, self.time)
        self.client.armed = True
        with self.assertRaises(PermissionError):
            self.client.set_output(1, 1)
        self.assertFalse(self.client.armed)

    def test_late_complete_response_rejected_before_tick(self):
        self.client.request_channel(1)
        self.time += .36
        self.fragments()
        self.assertEqual(self.client.channels, {})
        self.assertIsNone(self.client.pending_channel)
        self.assertEqual(self.client.bad_frames, 1)

    def test_new_heartbeat_does_not_restore_stale_arm(self):
        self.heartbeat()
        self.client.armed = True
        self.time += 2
        self.heartbeat()
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.armed)

    def test_system_write_readback_is_diagnostic_only(self):
        self.heartbeat()
        self.client.armed = True
        self.client.set_system_current_limit(150)
        self.assertEqual(self.client.last_config_readback.status, "pending")
        self.heartbeat()
        self.assertEqual(self.client.last_config_readback.status, "diagnostic match; unconfirmed")
        self.assertFalse(self.client.last_config_readback.confirmed)

    def test_late_analogue_response_rejected_before_tick(self):
        self.client.request_analogue(1)
        self.time += .36
        for i in range(2):
            self.client.ingest(FakeMessage(0x713, bytes([i]) + bytes(7)))
        self.assertEqual(self.client.analogues, {})
        self.assertIsNone(self.client.pending_analogue)

    def test_duplicate_fragment_invalidates_response(self):
        self.client.request_channel(1)
        first = FakeMessage(0x701, channel_frames()[0])
        self.client.ingest(first)
        self.client.ingest(first)
        self.fragments()
        self.assertEqual(self.client.channels, {})

    def test_send_failure_disarms_and_clears_request(self):
        self.heartbeat()
        self.client.armed = True
        self.bus.send = Mock(side_effect=OSError("adapter removed"))
        with self.assertRaises(OSError):
            self.client.request_channel(1)
        self.assertIsNone(self.client.pending_channel)
        self.assertFalse(self.client.armed)
        self.assertFalse(self.client.online())
        self.assertEqual(self.client.transport_errors, 1)

    def test_receive_failure_disarms(self):
        self.heartbeat()
        self.client.armed = True
        self.bus.recv = Mock(side_effect=OSError("adapter removed"))
        with self.assertRaises(OSError):
            self.client.tick()
        self.assertFalse(self.client.armed)

    def test_closed_client_cannot_poll_or_regain_authorization(self):
        self.heartbeat()
        self.client.close()
        self.client.close()
        self.heartbeat()
        self.client.tick()
        self.assertFalse(self.client.online())
        self.assertEqual(self.bus.sent, [])
        with self.assertRaises(RuntimeError):
            self.client.request_channel(1)

    def test_can_fd_cannot_count_as_classic_heartbeat(self):
        msg = FakeMessage(0x720, heartbeat())
        msg.is_fd = True
        self.client.ingest(msg)
        self.assertFalse(self.client.online())

    def test_budget_bounded_and_digital_has_own_freshness(self):
        self.bus.received = [FakeMessage(0x711, bytes(8)) for _ in range(65)]
        self.client.tick()
        self.assertEqual(len(self.bus.received), 1)
        self.assertEqual(self.client.digital_last_seen, self.time)
        self.assertFalse(self.client.online())
        with self.assertRaises(ValueError):
            self.client.tick(receive_budget=257)

    def write(self, **values):
        self.heartbeat()
        self.client.armed = True
        self.client.configure_channel(1, **values)
        self.client.tick(receive_budget=0)
        self.assertEqual(self.client.pending_channel, 1)

    def test_matching_readback_never_confirms_legacy_write(self):
        self.write(high_current=12, retries=3)
        self.fragments()
        result = self.client.last_config_readback
        self.assertEqual(result.status, "diagnostic match; unconfirmed")
        self.assertFalse(result.confirmed)
        self.assertEqual(self.client.channels[1].attribution, "uncorrelated diagnostic")

    def test_discrepancy_readback_reports_expected_and_observed(self):
        self.write(high_current=10)
        self.fragments()
        result = self.client.last_config_readback
        self.assertEqual(result.status, "discrepancy; unconfirmed")
        self.assertEqual(result.discrepancies, {"current_high_a": (10, 12.0)})
        self.assertFalse(result.confirmed)

    def test_readback_timeout_does_not_report_success(self):
        self.write(high_current=12)
        self.time += 1.5
        self.fragments()
        self.assertEqual(self.client.last_config_readback.status, "timeout; unconfirmed")

    def test_pending_readback_cannot_be_overwritten(self):
        self.write(high_current=12)
        count = len(self.bus.sent)
        with self.assertRaises(RuntimeError):
            self.client.configure_channel(2, high_current=11)
        self.assertEqual(len(self.bus.sent), count)

    def test_existing_request_cannot_satisfy_post_write_readback(self):
        self.heartbeat()
        self.client.request_channel(1)
        self.client.armed = True
        self.client.configure_channel(1, high_current=12)
        self.fragments()
        self.assertEqual(self.client.last_config_readback.status, "pending")

    def test_invalid_numeric_config_never_sends(self):
        self.heartbeat()
        self.client.armed = True
        for values in ({"high_current": 1.5}, {"retries": True},
                       {"low_current": 12, "high_current": 10}):
            with self.assertRaises(ValueError):
                self.client.configure_channel(1, **values)
        self.assertEqual(self.bus.sent, [])


class AdapterTests(unittest.TestCase):
    def test_native_adapter_factory_does_not_send_discovery_writes(self):
        for interface in ("pcan", "slcan", "socketcan", "kvaser", "vector", "virtual"):
            with self.subTest(interface=interface):
                factory = Mock(return_value=FakeBus())
                with patch.dict(sys.modules, {"can": types.SimpleNamespace(Bus=factory)}):
                    bus = open_adapter(interface, "channel")
                self.assertEqual(bus.sent, [])
                expected = {"interface": interface, "channel": "channel"}
                if interface != "virtual":
                    expected["bitrate"] = 500000
                factory.assert_called_once_with(**expected)

    def test_ecumaster_reuses_existing_bridge_without_copying_vendor_files(self):
        factory = Mock(return_value=FakeBus())
        modules = {"can": types.SimpleNamespace(), "core": types.ModuleType("core"),
                   "core.can_eusb2can": types.SimpleNamespace(EUSB2CANBus=factory)}
        with patch.dict(sys.modules, modules), patch("os.path.isfile", return_value=True), patch.object(sys, "path", list(sys.path)):
            bus = open_adapter("eusb2can", "COM4", dash_source="/existing/dash/src")
        factory.assert_called_once_with(channel="COM4", bitrate=500000)
        self.assertEqual(bus.sent, [])


if __name__ == "__main__":
    unittest.main()
