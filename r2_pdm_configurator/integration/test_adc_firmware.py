"""Raw ADC fixtures -> actual C conversion/link -> Python CAN -> Cortex Qt.

Fixtures simulate ADC words and factory calibration, never physical hardware.
"""
import os
import struct
import unittest
from unittest.mock import patch

from r2_pdm_configurator.integration import test_hal_firmware as hal
from r2_pdm_configurator.models.hal_telemetry import hal_snapshot


def nearest(numerator, denominator):
    return (numerator + denominator // 2) // denominator


class AdcFirmwareIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(hal.FirmwareIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(hal.FirmwareIntegrationTests.tearDownClass.__func__)
    setUp = hal.FirmwareIntegrationTests.setUp
    pump = hal.FirmwareIntegrationTests.pump
    assert_outputs_off = hal.FirmwareIntegrationTests.assert_outputs_off
    upload = hal.FirmwareIntegrationTests.upload

    def inject(self, currents=None, battery=3000, reference=1500, temperature=1000):
        raw = list(currents if currents is not None else range(100, 1500, 100))
        raw += [battery, reference, temperature]
        self.bus.command(f'ADC {self.bus.now_ms} ' + ' '.join(map(str, raw)))
        self.pump(35)  # One full ADC round; acquisition still fresh.

    def test_raw_words_reach_cortex_with_independently_computed_units(self):
        self.inject()
        battery = nearest(3000 * 39787 * 3300, 10000 * 3294)
        currents = [nearest(raw * 3300 * 1840772, 4095 * 100000)
                    for raw in range(100, 1500, 100)]
        expected = {100: battery, 101: sum(currents), 102: 30000,
                    **dict(zip(range(110, 124), currents))}
        rows = {row.channel_id: row for row in hal_snapshot(self.client)}
        for channel, value in expected.items():
            self.assertTrue(self.client.sample_valid(channel), channel)
            self.assertEqual(self.client.samples[channel].value, value, channel)
            self.assertEqual(rows[f'HAL:{channel}'].numeric, value / 1000)
            self.assertEqual(self.client.samples[channel].quality, 0 if channel == 102 else 2)
        self.assertGreater(self.client.samples[110].value, 0)
        self.assertEqual(self.bus.sent, [])
        self.assert_outputs_off()

    def test_measured_reference_compensates_battery_current_and_temperature(self):
        self.inject(reference=1650, temperature=1100)
        self.assertEqual(self.client.samples[100].value, nearest(3000 * 39787 * 3000, 10000 * 3294))
        self.assertEqual(self.client.samples[110].value, nearest(100 * 3000 * 1840772, 4095 * 100000))
        self.assertEqual(self.client.samples[102].value, 30000)
        self.assert_outputs_off()

    def test_reference_fault_invalidates_every_sensor_without_fabricated_zero(self):
        for reference in (0, 4095, 1):
            self.inject(reference=reference)
            for channel in (100, 101, 102, *range(110, 124)):
                self.assertEqual(self.client.samples[channel].quality, 3)
                self.assertFalse(self.client.sample_valid(channel))
            self.assertTrue(all(row.numeric is None for row in hal_snapshot(self.client)[21:]))
        self.assert_outputs_off()

    def test_saturated_current_invalidates_total_only_and_zero_is_valid(self):
        self.inject(currents=[4095, 0] + [100] * 12)
        for channel in (101, 110):
            self.assertEqual(self.client.samples[channel].quality, 3)
            self.assertFalse(self.client.sample_valid(channel))
        self.assertTrue(self.client.sample_valid(111))
        self.assertEqual(self.client.samples[111].value, 0)
        self.assertTrue(self.client.sample_valid(112))
        self.assertTrue(self.client.sample_valid(100))
        self.assert_outputs_off()

    def test_temperature_calibration_fault_isolated_and_reference_cal_fault_global(self):
        self.bus.command('CAL 1500 1000 1000')
        self.inject()
        self.assertFalse(self.client.sample_valid(102))
        self.assertTrue(self.client.sample_valid(100))
        for calibration in (0, 65535):
            self.bus.command(f'CAL {calibration} 1000 1300')
            self.inject()
            self.assertTrue(all(not self.client.sample_valid(channel)
                for channel in (100, 101, 102, *range(110, 124))))
        self.assert_outputs_off()

    def test_temperature_outside_physical_range_and_battery_saturation_are_gaps(self):
        self.inject(battery=4095, temperature=3000)
        self.assertFalse(self.client.sample_valid(100))
        self.assertFalse(self.client.sample_valid(102))
        self.assertTrue(self.client.sample_valid(110))
        self.assert_outputs_off()

    def test_acquisition_stale_despite_live_can_then_recovers(self):
        self.inject()
        self.pump(85)
        self.assertTrue(self.client.online())
        for channel in (100, 101, 102, *range(110, 124)):
            self.assertEqual(self.client.samples[channel].quality, 4)
            self.assertFalse(self.client.sample_valid(channel))
        self.inject()
        self.assertTrue(self.client.sample_valid(100))
        self.assert_outputs_off()

    def test_adc_frames_lost_independently_of_heartbeat_and_output_diagnostics(self):
        self.inject()
        self.bus.inbound = lambda message: None if message.arbitration_id == 0x584 else message
        self.pump(110)
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.sample_valid(100))
        self.assertTrue(self.client.sample_valid(230))
        self.assert_outputs_off()

    def test_acquisition_failure_and_vm_upload_do_not_actuate(self):
        self.inject()
        self.bus.command('ADCFAIL 3')
        self.upload(hal.constant(123))
        self.pump(40)
        self.assertTrue(self.client.upload.verified)
        self.assertEqual(self.client.samples[300].value, 123)
        self.assertEqual(self.client.samples[100].quality, 3)
        self.assert_outputs_off()

    def test_qt_graph_uses_actual_adc_conversion_and_gaps_on_device_fault(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        from r2_pdm_configurator.app import MainWindow
        self.__class__.qt_app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.addCleanup(window.close)
        window.protocol.setCurrentIndex(1)
        with patch('r2_pdm_configurator.app.open_adapter', return_value=self.bus), patch(
                'r2_pdm_configurator.app.HalCanClient', return_value=self.client):
            window._toggle_connection()
        window.timer.stop()
        self.inject()
        window._refresh_telemetry()
        index = next(i for i, row in enumerate(window.model.samples) if row.channel_id == 'HAL:100')
        window.table.selectRow(index)
        self.qt_app.processEvents()
        expected = nearest(3000 * 39787 * 3300, 10000 * 3294) / 1000
        self.assertEqual(window.model.samples[index].numeric, expected)
        self.assertEqual(window.trend.history[-1], expected)
        self.bus.command('ADCFAIL 3')
        self.pump(35)
        window._refresh_telemetry()
        self.assertIsNone(window.model.samples[index].numeric)
        self.assertIsNone(window.trend.history[-1])
        self.assert_outputs_off()


if __name__ == '__main__':
    unittest.main()
