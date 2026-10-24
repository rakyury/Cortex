"""HIL-UI software evidence: actual C fixtures through CAN and passive Qt views.

No physical HIL, sensor hardware or electrical output is exercised here.
"""
import unittest

import can
from r2_pdm_configurator.integration import test_start_can_ui as ui_base


class HilFaultUiTests(unittest.TestCase):
    setUpClass = classmethod(ui_base.StartCanUiIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(ui_base.StartCanUiIntegrationTests.tearDownClass.__func__)
    setUp = ui_base.StartCanUiIntegrationTests.setUp
    pump = ui_base.StartCanUiIntegrationTests.pump
    until = ui_base.StartCanUiIntegrationTests.until
    configure = ui_base.StartCanUiIntegrationTests.configure
    assert_off = ui_base.StartCanUiIntegrationTests.assert_off

    def adc(self, first=100):
        raw = [first] + [100] * 13 + [3000, 1500, 1000]
        self.bus.command(f'ADC {self.bus.now_ms} ' + ' '.join(map(str, raw)))

    def row(self, channel):
        return next(row for row in self.window.model.samples
                    if row.channel_id == f'HAL:{channel}')

    def test_current_saturation_gap_then_new_value_recovery(self):
        """HIL-UI-01: saturated current/total are gaps; fresh recovery keeps graph gap."""
        self.adc()
        self.pump(35)
        self.window._refresh_telemetry()
        index = next(i for i, row in enumerate(self.window.model.samples)
                     if row.channel_id == 'HAL:110')
        self.window.table.selectRow(index)
        self.app.processEvents()
        initial = self.row(110).numeric
        self.assertGreater(initial, 0)
        self.assertEqual(self.row(110).quality, 'nominal')
        self.assertEqual(self.window.trend.history[-1], initial)
        self.adc(4095)
        self.pump(35)
        self.window._refresh_telemetry()
        for channel in (110, 101):
            self.assertIsNone(self.row(channel).numeric)
            self.assertEqual(self.row(channel).quality, 'invalid')
        self.assertIsNotNone(self.row(100).numeric)
        self.assertIsNone(self.window.trend.history[-1])
        self.adc(200)
        self.pump(35)
        self.window._refresh_telemetry()
        self.assertGreater(self.row(110).numeric, initial)
        self.assertEqual(self.row(110).quality, 'nominal')
        self.assertIsNotNone(self.row(101).numeric)
        self.assertEqual(self.window.trend.history[-1], self.row(110).numeric)
        self.assertIn(None, self.window.trend.history)
        self.assertIn('Nominal', self.window.trend.caption)
        self.assert_off()

    def test_frozen_scheduler_current_is_unknown_while_adc_stays_fresh(self):
        """HIL-UI-02: stale scheduler current cannot hide held reserves or taint fresh ADC."""
        self.configure()
        self.bus.command('STARTREQ 1')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.available
                   and self.client.start_snapshot.channels[0].state == 'STARTING')
        self.bus.command('STARTFREEZE 1')
        for _ in range(15):
            self.adc()
            self.pump(20)
        self.window._refresh_telemetry()
        self.assertTrue(self.client.start_snapshot_valid())
        self.assertIsNone(self.client.start_snapshot.total_measured_ma)
        self.assertIn('Current Unknown', self.panel.summary.text())
        self.assertIn('50.000 A reserved', self.panel.summary.text())
        self.assertEqual(self.panel.table.rowCount(), 24)
        self.assertEqual(self.panel.table.item(0, 2).text(), 'FAULT')
        self.assertIsNotNone(self.row(110).numeric)
        self.assertEqual(self.row(110).quality, 'nominal')
        self.bus.command('STARTFREEZE 0')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.total_measured_ma is not None)
        self.window._refresh_telemetry()
        self.assertNotIn('Current Unknown', self.panel.summary.text())
        self.assertIn('50.000 A reserved', self.panel.summary.text())
        self.assertEqual(self.panel.table.item(0, 2).text(), 'FAULT')
        self.assertEqual(self.client.start_snapshot.queue, ())
        self.assert_off()

    def test_crc_fault_clears_old_rows_until_complete_recovery(self):
        """HIL-UI-03: rejected generation clears UI; partial transfer cannot restore it."""
        self.configure()
        self.bus.command('STARTREQ 1')
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.available)
        self.window._refresh_telemetry()
        self.assertEqual(self.panel.table.rowCount(), 24)

        def corrupt_end(message):
            if message.arbitration_id != 0x587:
                return message
            data = bytearray(message.data)
            data[4] ^= 1
            return can.Message(arbitration_id=0x587, data=data, is_extended_id=False)

        self.bus.inbound = corrupt_end
        self.until(lambda: self.client.start_bad_frames > 0)
        self.window._refresh_telemetry()
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertNotIn('50.000', self.panel.summary.text())
        self.assertIn('complete scheduler snapshot', self.panel.status.text())
        self.assertIn('CRC', self.client.start_snapshot_error)
        self.configure(profile=14, slots=2)
        self.bus.inbound = lambda msg: None if msg.arbitration_id == 0x587 else msg
        self.pump(100)
        self.window._refresh_telemetry()
        self.assertTrue(self.client.online())
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.bus.inbound = lambda msg: msg
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.available
                   and self.client.start_snapshot.profile == 14)
        self.window._refresh_telemetry()
        self.assertEqual(self.panel.table.rowCount(), 14)
        self.assertIn('SIMULATION FIXTURES', self.panel.status.text())
        self.assertEqual(self.window.model.rowCount(), 38)
        self.assert_off()


if __name__ == '__main__':
    unittest.main()
