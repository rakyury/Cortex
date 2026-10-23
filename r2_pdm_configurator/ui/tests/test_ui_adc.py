"""ADC display consumes actual 0x584 frames, never synthesizes gate-based currents."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import struct
import unittest
import can
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from r2_pdm_configurator.hal_can import READABLE_CHANNELS
from r2_pdm_configurator.ui.tests import test_ui_hal as hal_tests


class AdcUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = hal_tests.HalUiTests.setUp
    tearDown = hal_tests.HalUiTests.tearDown
    heartbeat = hal_tests.HalUiTests.heartbeat
    sample = hal_tests.HalUiTests.sample
    row = hal_tests.HalUiTests.row

    def adc(self, channel, value, quality=2, sequence=1):
        self.client.ingest(can.Message(arbitration_id=0x584, is_extended_id=False,
            data=struct.pack('<HBBi', channel, quality, sequence, value)))
        self.window._refresh_telemetry()

    def select(self, channel):
        index = next(i for i, row in enumerate(self.window.model.samples) if row.channel_id == f'HAL:{channel}')
        self.window.table.selectRow(index)
        return index

    def test_old_firmware_has_17_unavailable_adc_rows_and_preserves_vm_index(self):
        self.heartbeat()
        self.sample(230, 1)
        self.assertEqual(self.window.model.rowCount(), 38)
        self.assertEqual(self.window.model.samples[20].channel_id, 'HAL:300')
        self.assertEqual(len(self.window.model.samples[21:]), 17)
        self.assertTrue(all(row.numeric is None and row.quality == 'unavailable'
                            for row in self.window.model.samples[21:]))
        self.assertEqual(self.window.tree.topLevelItem(0).childCount(), 38)
        self.assertEqual(self.bus.sent, [])

    def test_battery_and_mcu_die_temperature_scale_correctly(self):
        self.heartbeat()
        self.adc(100, 13875)
        self.adc(102, -12500, quality=0)
        self.assertEqual((self.row(100).value, self.row(100).unit, self.row(100).numeric), ('13.875', 'V', 13.875))
        self.assertEqual((self.row(102).value, self.row(102).unit, self.row(102).numeric), ('-12.500', '°C', -12.5))
        self.assertIn('MCU die', self.row(102).label)
        self.assertIn('not board/driver/coolant', self.row(102).detail)
        self.assertEqual(self.row(102).quality, 'live')

    def test_nonzero_is_current_survives_gate_off_and_is_orange_nominal(self):
        self.heartbeat()
        self.sample(200, 0)
        self.adc(110, 1234)
        self.adc(101, 17276)
        self.assertEqual(self.row(200).numeric, 0)
        self.assertEqual(self.row(110).numeric, 1.234)
        self.assertEqual(self.row(101).numeric, 17.276)
        self.assertEqual(self.row(110).quality, 'nominal')
        self.assertIn('not independently verified load current', self.row(110).detail)
        index = self.select(110)
        color = self.window.model.data(self.window.model.index(index, 2), Qt.ItemDataRole.ForegroundRole)
        self.assertEqual(color.name(), '#e7a649')
        self.assertIn('Nominal calibration', self.window.trend.caption)
        self.assertEqual(self.window.trend.history[-1], 1.234)
        self.assertEqual(self.bus.sent, [])

    def test_all_invalid_adc_qualities_create_graph_gaps_not_zero(self):
        self.heartbeat()
        self.adc(110, 1000)
        self.select(110)
        for sequence, quality in enumerate((1, 3, 4), 2):
            self.adc(110, 0, quality=quality, sequence=sequence)
            self.assertEqual(self.row(110).value, '—')
            self.assertIsNone(self.row(110).numeric)
            self.assertIsNone(self.window.trend.history[-1])
        self.assertEqual(self.row(110).quality, 'stale')

    def test_adc_stales_independently_of_live_heartbeat_and_gate_samples(self):
        self.heartbeat()
        self.adc(100, 12000)
        self.select(100)
        for i in range(3):
            self.now += .4
            self.heartbeat()
            self.sample(200, 0, sequence=i + 1)
        self.assertTrue(self.client.online())
        self.assertEqual(self.row(200).numeric, 0)
        self.assertEqual(self.row(100).quality, 'stale')
        self.assertIsNone(self.window.trend.history[-1])

    def test_zero_nominal_sample_is_real_zero_and_disconnect_clears_numeric(self):
        self.heartbeat()
        self.adc(110, 0)
        self.assertEqual(self.row(110).numeric, 0.0)
        self.assertEqual(self.row(110).quality, 'nominal')
        self.window._disconnect()
        self.assertIsNone(self.row(110).numeric)
        self.assertEqual(self.bus.sent, [])

    def test_adc_sources_remain_excluded_from_device_vm(self):
        offered = {self.editor.source.itemData(i) for i in range(self.editor.source.count())}
        self.assertEqual(offered, READABLE_CHANNELS)
        self.assertTrue({100, 101, 102, *range(110, 124)}.isdisjoint(offered))


if __name__ == '__main__':
    unittest.main()
