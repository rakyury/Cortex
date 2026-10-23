"""Actual HAL C transmitter -> simulated Classic CAN -> Cortex diagnostics."""
import os
from pathlib import Path
import struct
import tempfile
import unittest

import can
from r2_pdm_configurator.hal_can import HalCanClient
from r2_pdm_configurator.integration.c_bus import CSimulatorBus, compile_simulator

START_IDS = (0x585, 0x586, 0x587)


class StartCanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / ('start_can.exe' if os.name == 'nt' else 'start_can')
        try:
            compile_simulator(cls.binary)
        except Exception:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.clock = 10.0
        self.bus = CSimulatorBus(self.binary)
        self.addCleanup(self.bus.shutdown)
        self.client = HalCanClient(self.bus, now=lambda: self.clock)
        self.addCleanup(self.client.close)

    def pump(self, ticks=1):
        for _ in range(ticks):
            self.clock += .01
            self.bus.advance(10)
            self.client.tick()

    def until(self, predicate, ticks=400):
        for _ in range(ticks):
            self.pump()
            if predicate():
                return
        self.fail('Snapshot condition not reached: ' + str(self.client.start_snapshot_error))

    def assert_off(self):
        self.assertEqual(self.bus.sent, [])
        for msg in self.bus.history:
            if msg.arbitration_id == 0x581:
                channel, quality, _, value = struct.unpack('<HBBi', msg.data)
                if 200 <= channel < 214 and quality == 0:
                    self.assertEqual(value, 0)
        self.assertTrue(self.client.interlocked)

    def test_hardware_metadata_is_uncommissioned_not_zero_budget(self):
        self.until(lambda: self.client.start_snapshot_valid())
        snapshot = self.client.start_snapshot
        self.assertEqual(snapshot.profile, 14)
        self.assertFalse(snapshot.available)
        self.assertFalse(snapshot.simulation_only)
        self.assertTrue(snapshot.inhibited)
        from r2_pdm_configurator.models.start_diagnostics import start_diagnostics_view
        view = start_diagnostics_view(self.client)
        self.assertEqual(view.rows, ())
        self.assertIn('Unknown', view.summary)
        self.assert_off()

    def test_heartbeat_alone_does_not_refresh_scheduler(self):
        self.until(lambda: self.client.start_snapshot_valid())
        self.bus.inbound = lambda msg: None if msg.arbitration_id in START_IDS else msg
        self.pump(350)
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.start_snapshot_valid())
        from r2_pdm_configurator.models.start_diagnostics import start_diagnostics_view
        self.assertEqual(start_diagnostics_view(self.client).rows, ())
        self.assert_off()

    def test_corrupted_and_missing_frames_never_publish_partial(self):
        def corrupt(msg):
            if msg.arbitration_id == 0x586 and msg.data[2] == 2:
                data = bytearray(msg.data)
                data[4] ^= 0x40
                return can.Message(arbitration_id=msg.arbitration_id, data=data, is_extended_id=False)
            return msg
        self.bus.inbound = corrupt
        self.pump(240)
        self.assertIsNone(self.client.start_snapshot)
        self.assertGreater(self.client.start_bad_frames, 0)
        self.bus.inbound = lambda msg: None if msg.arbitration_id == 0x586 and msg.data[2] == 2 else msg
        self.pump(240)
        self.assertIsNone(self.client.start_snapshot)
        self.bus.inbound = lambda msg: msg
        self.until(lambda: self.client.start_snapshot_valid())
        self.assert_off()

    def test_replayed_complete_transfer_cannot_refresh_capture(self):
        self.until(lambda: self.client.start_snapshot_valid())
        snapshot = self.client.start_snapshot
        messages = [m for m in self.bus.history if m.arbitration_id in START_IDS]
        self.bus.inbound = lambda msg: None if msg.arbitration_id in START_IDS else msg
        self.pump(350)
        for message in messages:
            self.client.ingest(message)
        self.assertFalse(self.client.start_snapshot_valid())
        self.assertTrue(self.client.start_snapshot is None or self.client.start_snapshot.last_seen == snapshot.last_seen)
        self.assert_off()

    def test_duplicate_frames_do_not_corrupt_coherent_transfer(self):
        def duplicate(msg):
            if msg.arbitration_id == 0x586:
                self.bus.received.append(msg)
            return msg
        self.bus.inbound = duplicate
        self.until(lambda: self.client.start_snapshot_valid())
        self.assertEqual(self.client.start_snapshot.profile, 14)
        self.assert_off()

    def configure(self, profile=24, slots=4, total=1000000, group=1000000):
        self.bus.command(f'STARTCFG {profile} {slots} {total} {group}')

    def test_profiles_and_slot_limits_show_real_queue_and_reservations(self):
        for profile in (14, 24):
            for slots in (2, 3, 4):
                with self.subTest(profile=profile, slots=slots):
                    previous = self.client.start_snapshot.sequence if self.client.start_snapshot else 0
                    self.configure(profile, slots)
                    self.bus.command('STARTALL')
                    self.until(lambda: self.client.start_snapshot_valid()
                               and self.client.start_snapshot.sequence > previous
                               and self.client.start_snapshot.available
                               and self.client.start_snapshot.profile == profile
                               and self.client.start_snapshot.slots == slots
                               and self.client.start_snapshot.starts_reserved == slots)
                    snapshot = self.client.start_snapshot
                    self.assertTrue(snapshot.simulation_only)
                    self.assertTrue(snapshot.physical_outputs_off)
                    self.assertEqual(snapshot.queue, tuple(range(slots + 1, profile + 1)))
                    self.assertEqual(snapshot.total_reserved_ma, 20000 + slots * 30000)
                    self.assertEqual(snapshot.channels[slots].reason, 'SLOTS')
                    self.assert_off()

    def test_total_and_group_budget_reasons_cross_can(self):
        for total, group, requests, reason, reserved in (
                (120000, 1000000, (1, 2, 3, 4), 'TOTAL_BUDGET', 110000),
                (1000000, 40000, (1, 3, 2), 'GROUP_BUDGET', 50000)):
            with self.subTest(reason=reason):
                previous = self.client.start_snapshot.sequence if self.client.start_snapshot else 0
                self.configure(total=total, group=group)
                for channel in requests:
                    self.bus.command(f'STARTREQ {channel}')
                self.until(lambda: self.client.start_snapshot_valid()
                           and self.client.start_snapshot.sequence > previous
                           and any(c.reason == reason for c in self.client.start_snapshot.channels))
                snapshot = self.client.start_snapshot
                self.assertEqual(snapshot.total_reserved_ma, reserved)
                self.assertTrue(snapshot.queue)
                self.assertEqual(snapshot.channels[snapshot.queue[0] - 1].reason, reason)
                self.assert_off()

    def test_fault_holds_reserve_until_off_proof_without_retry(self):
        self.configure()
        self.bus.command('STARTREQ 1')
        self.until(lambda: self.client.start_snapshot_valid() and self.client.start_snapshot.available
                   and self.client.start_snapshot.channels[0].state == 'STARTING')
        self.bus.command('STARTFAULT 1')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.channels[0].state == 'FAULT')
        self.assertEqual(self.client.start_snapshot.channels[0].reserve_ma, 30000)
        self.assertEqual(self.client.start_snapshot.starts_reserved, 1)
        self.bus.command('STARTOFF 1')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.channels[0].reserve_ma == 0)
        self.assertEqual(self.client.start_snapshot.channels[0].state, 'FAULT')
        self.bus.command('STARTCLEAR 1')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.channels[0].state == 'OFF')
        self.assertEqual(self.client.start_snapshot.queue, ())
        self.assert_off()

    def test_vm_upload_coexists_with_three_frame_tx_limit(self):
        self.bus.command('TXLIMIT 3')
        self.configure()
        self.bus.command('STARTALL')
        self.until(lambda: self.client.can_upload())
        self.client.armed = True
        self.client.begin_upload(b'\x01\x2a\x00\x00\x00\xff', confirmed=True)
        self.until(lambda: self.client.upload.verified, ticks=100)
        self.until(lambda: self.client.start_snapshot_valid() and self.client.start_snapshot.available)
        self.until(lambda: self.client.sample_valid(300))
        self.assertEqual(self.client.samples[300].value, 42)
        self.assertEqual({m.arbitration_id for m in self.bus.sent}, {0x590, 0x591, 0x592})
        self.assertTrue(self.client.interlocked)
        for msg in self.bus.history:
            if msg.arbitration_id == 0x581:
                channel, quality, _, value = struct.unpack('<HBBi', msg.data)
                if 200 <= channel < 214 and quality == 0:
                    self.assertEqual(value, 0)

    def test_tx_congestion_expires_old_snapshot_and_recovers(self):
        self.configure()
        self.bus.command('STARTALL')
        self.until(lambda: self.client.start_snapshot_valid())
        self.bus.command('TXLIMIT 1')  # Heartbeat/samples consume all foreground slots.
        self.pump(350)
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.start_snapshot_valid())
        self.bus.command('TXLIMIT 3')
        self.until(lambda: self.client.start_snapshot_valid())
        self.assert_off()

    def test_reset_clears_queue_but_keeps_active_fault_reserves(self):
        self.configure()
        self.bus.command('STARTALL')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.starts_reserved == 4)
        self.bus.command('STARTRESET')
        self.until(lambda: self.client.start_snapshot_valid() and not self.client.start_snapshot.queue)
        snapshot = self.client.start_snapshot
        self.assertEqual(snapshot.total_reserved_ma, 140000)
        self.assertEqual(snapshot.starts_reserved, 4)
        self.assertTrue(all(c.state == 'FAULT' for c in snapshot.channels[:4]))
        self.assert_off()

    def test_device_restart_replaces_fixture_with_uncommissioned_metadata(self):
        self.configure()
        self.bus.command('STARTALL')
        self.until(lambda: self.client.start_snapshot_valid() and self.client.start_snapshot.available)
        self.bus.command('RESET')
        self.bus.now_ms = 0
        self.pump()
        self.assertIsNone(self.client.start_snapshot)
        self.until(lambda: self.client.start_snapshot_valid())
        self.assertEqual(self.client.start_snapshot.profile, 14)
        self.assertFalse(self.client.start_snapshot.available)
        self.assert_off()

    def test_invalid_measurement_preserves_reserves_but_is_not_live_current(self):
        self.configure()
        self.bus.command('STARTREQ 1')
        self.until(lambda: self.client.start_snapshot_valid() and self.client.start_snapshot.available
                   and self.client.start_snapshot.channels[0].state == 'STARTING')
        self.bus.command('STARTINVALID')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.total_measured_ma is None)
        snapshot = self.client.start_snapshot
        self.assertTrue(snapshot.inhibited)
        self.assertEqual(snapshot.channels[0].state, 'FAULT')
        self.assertEqual(snapshot.channels[0].reserve_ma, 30000)
        self.assertEqual(snapshot.total_reserved_ma, 50000)
        from r2_pdm_configurator.models.start_diagnostics import start_diagnostics_view
        view = start_diagnostics_view(self.client)
        self.assertIn('Unknown', view.summary)
        self.assertEqual(len(view.rows), 24)
        self.assert_off()
