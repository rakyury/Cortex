"""Host fault-injection evidence; actual C policy, never hardware HIL approval.

START* commands are private stdin fixture controls, not CAN commands. In
particular STARTLINK explicitly supplies a fictional liveness input: dropping
frames at the Python receiver cannot establish device-side link supervision.
ADC diagnostics and scheduler feedback are deliberately independent fixtures.
"""
import struct
import unittest

from r2_pdm_configurator.integration import test_start_can as start_base
from r2_pdm_configurator.integration.test_hal_firmware import constant


class HilFaultTests(unittest.TestCase):
    setUpClass = classmethod(start_base.StartCanIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(start_base.StartCanIntegrationTests.tearDownClass.__func__)
    setUp = start_base.StartCanIntegrationTests.setUp
    pump = start_base.StartCanIntegrationTests.pump
    until = start_base.StartCanIntegrationTests.until
    configure = start_base.StartCanIntegrationTests.configure

    def snapshot_after(self, predicate=lambda s: True):
        """Wait for a capture after the injection, not an old frame in flight."""
        boundary = self.bus.now_ms
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.captured_ms > boundary
                   and predicate(self.client.start_snapshot))
        return self.client.start_snapshot

    def physical_off(self, writes=False):
        if not writes:
            self.assertEqual(self.bus.sent, [])
        else:
            self.assertTrue(all(m.arbitration_id in (0x590, 0x591, 0x592, 0x594)
                                for m in self.bus.sent))
        observed = set()
        for message in self.bus.history:
            if message.arbitration_id == 0x581:
                channel, quality, _, value = struct.unpack('<HBBi', message.data)
                if 200 <= channel < 214 and quality == 0:
                    observed.add(channel)
                    self.assertEqual(value, 0)
        self.assertEqual(observed, set(range(200, 214)))
        self.assertTrue(self.client.interlocked)

    def start_one(self, profile=24):
        self.configure(profile=profile)
        self.bus.command('STARTREQ 1')
        return self.snapshot_after(lambda s: s.available and s.channels[0].state == 'STARTING')

    def test_blackout_and_explicit_link_fault_never_replay_queue(self):
        """HIL-CAN-01: receiver blackout + explicit fixture link fault, 14/24 x 2/3/4."""
        for profile in (14, 24):
            for slots in (2, 3, 4):
                with self.subTest(profile=profile, slots=slots):
                    self.configure(profile, slots)
                    self.bus.command('STARTALL')
                    self.snapshot_after(lambda s: s.available and s.starts_reserved == slots)
                    self.bus.inbound = lambda m: None
                    self.bus.command('STARTLINK 0')
                    self.pump(80)
                    self.assertFalse(self.client.online())
                    self.assertFalse(self.client.start_snapshot_valid())
                    self.bus.inbound = lambda m: m
                    self.bus.command('STARTLINK 1')
                    s = self.snapshot_after(lambda s: s.available and not s.queue)
                    self.assertEqual(s.total_reserved_ma, 20000 + 30000 * slots)
                    self.assertTrue(all(c.state == 'FAULT' for c in s.channels[:slots]))
                    self.assertTrue(all(c.state == 'OFF' for c in s.channels[slots:]))
                    for channel in range(1, slots + 1):
                        self.bus.command(f'STARTOFF {channel}')
                    self.snapshot_after(lambda s: s.total_reserved_ma == 20000)
                    for channel in range(1, slots + 1):
                        self.bus.command(f'STARTCLEAR {channel}')
                    s = self.snapshot_after(lambda s: all(c.state == 'OFF' for c in s.channels))
                    self.assertEqual(s.queue, ())
                    self.assertEqual(s.starts_reserved, 0)
                    self.bus.command('STARTREQ 1')
                    self.snapshot_after(lambda s: s.channels[0].state == 'STARTING')
                    self.physical_off()

    def test_one_way_reply_loss_does_not_retry_upload_on_recovery(self):
        """HIL-CAN-02: device receives BEGIN; lost reverse replies never authorize DATA."""
        self.start_one()
        self.bus.inbound = lambda m: None if m.arbitration_id in (0x582, 0x583) else m
        self.client.armed = True
        self.client.begin_upload(constant(81), confirmed=True)
        self.pump(130)
        self.assertTrue(self.client.online())
        self.assertEqual(self.client.upload.state, 'failed')
        self.assertFalse(self.client.upload.verified)
        self.assertEqual([m.arbitration_id for m in self.bus.sent], [0x590])
        self.bus.inbound = lambda m: m
        self.pump(130)
        self.assertEqual([m.arbitration_id for m in self.bus.sent], [0x590])
        self.assertFalse(self.client.vm_active)
        self.assertFalse(self.client.armed)
        self.physical_off(writes=True)

    def test_reset_mid_upload_cannot_resume_old_transaction(self):
        """HIL-RESET-01: reset with pending DATA discards staging and client authorization."""
        self.start_one()
        held = []
        def hold(message):
            if message.arbitration_id == 0x591:
                held.append(message)
                return None
            return message
        self.bus.outbound = hold
        self.client.armed = True
        self.client.begin_upload(constant(82), confirmed=True)
        self.until(lambda: bool(held), ticks=20)
        self.bus.command('RESET')
        self.bus.now_ms = 0
        self.bus.outbound = lambda m: m
        sent = len(self.bus.sent)
        self.pump(60)
        # A delayed pre-reset DATA arrives at the new firmware session too.
        stale = held[0]
        self.bus.command(f'RX {self.bus.now_ms} 591 {bytes(stale.data).hex()}')
        self.pump(80)
        self.assertEqual(len(self.bus.sent), sent)
        self.assertFalse(self.client.armed)
        self.assertFalse(self.client.upload.verified)
        self.assertFalse(self.client.vm_active)
        self.assertFalse(self.client.sample_valid(300))
        s = self.snapshot_after()
        self.assertFalse(s.available)
        self.assertEqual(s.profile, 14)
        self.physical_off(writes=True)

    def test_delayed_old_generation_cannot_mix_with_new_profile(self):
        """HIL-CAN-03: old-generation DATA interleaved into a changed 24-channel fixture."""
        self.configure(profile=14, slots=2)
        self.snapshot_after(lambda s: s.available)
        old = next(m for m in self.bus.history if m.arbitration_id == 0x586 and m.data[2] == 3)
        injected = []
        def mix(message):
            if message.arbitration_id == 0x586 and message.data[2] == 3:
                injected.append(message)
                return old
            return message
        previous = self.client.start_snapshot.sequence
        self.bus.inbound = mix
        self.configure(profile=24, slots=4)
        self.bus.command('STARTALL')
        self.pump(280)
        self.assertTrue(injected)
        self.assertFalse(self.client.start_snapshot_valid())
        self.assertTrue(self.client.start_snapshot is None or self.client.start_snapshot.sequence == previous)
        self.assertGreater(self.client.start_bad_frames, 0)
        self.bus.inbound = lambda m: m
        s = self.snapshot_after(lambda s: s.available and s.profile == 24)
        self.assertEqual(len(s.channels), 24)
        self.assertEqual(s.slots, 4)
        self.physical_off()

    def test_frozen_measurement_fault_stays_latched_after_sensor_recovery(self):
        """HIL-SENSOR-01: frozen timestamps become Unknown, retain reserve, require explicit recovery."""
        self.start_one()
        self.bus.command('STARTFREEZE 1')
        s = self.snapshot_after(lambda s: s.total_measured_ma is None)
        self.assertTrue(s.inhibited)
        self.assertEqual(s.channels[0].state, 'FAULT')
        self.assertEqual(s.channels[0].reserve_ma, 30000)
        self.bus.command('STARTFREEZE 0')
        s = self.snapshot_after(lambda s: s.total_measured_ma == 50000)
        self.assertEqual(s.channels[0].state, 'FAULT')
        self.assertEqual(s.queue, ())
        self.bus.command('STARTCLEAR 1')
        s = self.snapshot_after()
        self.assertEqual(s.channels[0].state, 'FAULT')
        self.bus.command('STARTOFF 1')
        self.snapshot_after(lambda s: s.channels[0].reserve_ma == 0)
        self.bus.command('STARTCLEAR 1')
        self.snapshot_after(lambda s: s.channels[0].state == 'OFF')
        self.bus.command('STARTREQ 1')
        self.snapshot_after(lambda s: s.channels[0].state == 'STARTING')
        self.physical_off()

    def test_excess_current_and_contradictory_off_never_release_reservation(self):
        """HIL-SENSOR-02: over-envelope current and OFF+nonzero current cannot release a fault."""
        for profile in (14, 24):
            with self.subTest(profile=profile):
                self.start_one(profile)
                self.bus.command('STARTCURRENT 1 30001')
                s = self.snapshot_after(lambda s: s.channels[0].state == 'FAULT')
                self.assertEqual(s.total_measured_ma, 50001)
                self.assertLess(s.total_measured_ma, s.total_budget_ma)
                self.assertEqual(s.channels[0].reserve_ma, 30000)
                self.bus.command('STARTOFF 1')
                self.bus.command('STARTCURRENT 1 1')
                self.pump()
                self.bus.command('STARTCLEAR 1')
                s = self.snapshot_after()
                self.assertEqual(s.channels[0].state, 'FAULT')
                self.assertEqual(s.channels[0].reserve_ma, 30000)
                self.bus.command('STARTCURRENT 1 0')
                self.snapshot_after(lambda s: s.channels[0].reserve_ma == 0)
                self.bus.command('STARTCLEAR 1')
                self.snapshot_after(lambda s: s.channels[0].state == 'OFF')
                self.physical_off()

    def test_independent_total_above_reserve_blocks_admission_below_budget(self):
        """HIL-SENSOR-03: unexplained measured load below budget still vetoes new admission."""
        self.start_one()
        self.bus.command('STARTTOTAL 50001')
        self.bus.command('STARTREQ 2')
        s = self.snapshot_after(lambda s: s.channels[0].state == 'FAULT')
        self.assertEqual(s.total_reserved_ma, 50000)
        self.assertEqual(s.total_measured_ma, 50001)
        self.assertEqual(s.queue, (2,))
        self.assertEqual(s.channels[1].reason, 'UNHEALTHY')
        self.assertEqual(s.channels[1].reserve_ma, 0)
        # Explicitly cancel queued demand before recovery; health recovery alone
        # is not specified to discard otherwise-live queued requests.
        self.bus.command('STARTCANCEL 2')
        self.bus.command('STARTAUTO')
        s = self.snapshot_after()
        self.assertEqual(s.queue, ())
        self.assertEqual(s.channels[0].state, 'FAULT')
        self.assertEqual(s.channels[0].reserve_ma, 30000)
        self.physical_off()

    def test_invalid_channel_and_group_health_cannot_be_cleared_early(self):
        """HIL-SENSOR-04: sensor validity/group health gate explicit fault clear."""
        for injection, recovery in (('STARTSENSOR 1 0', 'STARTSENSOR 1 1'),
                                    ('STARTGROUP 1 0', 'STARTGROUP 1 1')):
            with self.subTest(injection=injection):
                self.start_one()
                self.bus.command(injection)
                s = self.snapshot_after(lambda s: s.channels[0].state == 'FAULT')
                self.assertEqual(s.channels[0].reserve_ma, 30000)
                self.bus.command('STARTOFF 1')
                self.pump(2)
                self.bus.command('STARTCLEAR 1')
                s = self.snapshot_after()
                self.assertEqual(s.channels[0].state, 'FAULT')
                self.bus.command(recovery)
                self.snapshot_after(lambda s: s.channels[0].reserve_ma == 0)
                self.bus.command('STARTCLEAR 1')
                self.snapshot_after(lambda s: s.channels[0].state == 'OFF')
                self.physical_off()

    def test_adc_saturation_invalid_and_stale_are_separate_from_scheduler_fixture(self):
        """HIL-ADC-01: ADC quality faults do not masquerade as commissioned scheduler feedback."""
        self.start_one()
        def adc(first=100, reference=1500):
            raw = [first] + [100] * 13 + [3000, reference, 1000]
            self.bus.command('ADC ' + str(self.bus.now_ms) + ' ' + ' '.join(map(str, raw)))
            self.pump(35)
        adc()
        self.assertEqual(self.client.samples[110].quality, 2)
        adc(first=4095)
        self.assertEqual(self.client.samples[110].quality, 3)
        self.assertEqual(self.client.samples[101].quality, 3)
        self.assertFalse(self.client.sample_valid(110))
        adc(reference=0)
        self.assertEqual(self.client.samples[100].quality, 3)
        adc()
        self.assertEqual(self.client.samples[110].quality, 2)
        self.pump(70)
        self.assertEqual(self.client.samples[110].quality, 4)
        self.assertTrue(self.client.online())
        s = self.snapshot_after(lambda s: s.available and s.total_measured_ma == 50000)
        self.assertTrue(s.simulation_only)
        self.assertEqual(s.channels[0].state, 'STARTING')
        self.physical_off()


if __name__ == '__main__':
    unittest.main()
