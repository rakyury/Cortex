"""Headless telemetry model and offscreen UI regression coverage."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import math
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from r2_pdm_configurator.app import MainWindow, TelemetryModel
from r2_pdm_configurator.models.telemetry import snapshot, diagnostics
from r2_pdm_configurator.pdm_can import PdmCanClient, ChannelTelemetry, AnalogueTelemetry


class Bus:
    def __init__(self):
        self.sent = []
        self.closed = False
    def recv(self, timeout=0):
        return None
    def send(self, message):
        self.sent.append(message)
    def shutdown(self):
        self.closed = True


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.bus = Bus()
        self.client = PdmCanClient(self.bus, now=lambda: self.now)
        self.client.system.last_seen = self.now
        self.client.system.battery_v = 13.8
        self.client.channels[1] = ChannelTelemetry(1, current_a=7.2, enabled=True, last_seen=self.now)
        self.client.analogues[1] = AnalogueTelemetry(1, value=12.5, voltage_v=2.0, last_seen=self.now)
    def row(self, cid):
        return next(row for row in snapshot(self.client) if row.channel_id == cid)
    def test_all_channels_exist_without_telemetry(self):
        rows = snapshot(None)
        self.assertEqual(len(rows), 35)
        self.assertEqual(sum(r.channel_id.startswith('OUT:') for r in rows), 14)
        self.assertTrue(all(r.value == '—' for r in rows))
    def test_fresh_value_and_immutable_snapshot(self):
        row = self.row('OUT:01')
        self.assertEqual((row.value, row.unit, row.status, row.numeric), ('7.2', 'A', 'ON', 7.2))
        with self.assertRaises(FrozenInstanceError):
            row.value = 'bad'
        self.assertEqual(self.bus.sent, [])
    def test_live_heartbeat_does_not_freshen_old_output(self):
        self.now += 7
        self.client.system.last_seen = self.now
        row = self.row('OUT:01')
        self.assertEqual((row.value, row.status, row.numeric), ('—', 'Stale', None))
        self.assertIn('7.2 A', row.detail)
        self.assertIn('7.0 s', row.age)
    def test_lost_heartbeat_degrades_other_fresh_samples(self):
        self.now += 1.1
        self.assertEqual(self.row('OUT:01').quality, 'degraded')
        self.assertIsNone(self.row('OUT:01').numeric)
    def test_nonfinite_and_future_samples_are_invalid(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            self.client.channels[1].current_a = value
            self.assertEqual(self.row('OUT:01').status, 'Invalid')
            self.assertEqual(self.row('OUT:01').value, '—')
        self.client.analogues[1].last_seen = self.now + 1
        self.assertEqual(self.row('AI:01').status, 'Invalid')
    def test_analogue_voltage_must_also_be_finite(self):
        self.client.analogues[1].voltage_v = float('nan')
        self.assertEqual(self.row('AI:01').status, 'Invalid')
    def test_digital_requires_own_timestamp(self):
        self.client.digital_mask = 1
        self.assertEqual(self.row('DI:01').value, '—')
        self.client.digital_last_seen = self.now
        self.assertEqual(self.row('DI:01').value, '1')
        self.now += 2
        self.client.system.last_seen = self.now
        self.assertEqual(self.row('DI:01').status, 'Stale')
    def test_diagnostics_preserve_fault_and_protocol_warning(self):
        self.client.system.error_flags = 4
        self.client.bad_frames = 3
        rows = snapshot(self.client)
        self.assertEqual(rows[0].quality, 'fault')
        text = diagnostics(self.client, rows)
        self.assertIn('malformed / timeouts 3', text)
        self.assertIn('no transaction ID', text)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        TelemetryTests.setUp(self)
        self.window = MainWindow()
        self.window.client = self.client
        self.window.model.client = self.client
    def tearDown(self):
        self.window.close()
    def test_window_is_read_only_until_explicit_arm(self):
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.send.isEnabled())
        self.assertFalse(self.window.arm.isChecked())
        self.assertEqual(self.bus.sent, [])
        self.assertEqual(self.window.tree.topLevelItem(1).childCount(), 14)
        self.assertFalse(self.window.grab().isNull())
    def test_refresh_and_tree_selection_keep_stale_out_of_graph(self):
        self.window._refresh_telemetry()
        item = self.window.tree.topLevelItem(1).child(0)
        self.window._select_tree_channel(item, 0)
        self.assertEqual(self.window.table.currentIndex().row(), 5)
        self.assertEqual(self.window.trend.history[-1], 7.2)
        self.now += 7.0
        self.client.system.last_seen = self.now
        self.window._refresh_telemetry()
        self.assertEqual(self.window.model.data(self.window.model.index(5, 4)), 'Stale')
        self.assertIsNone(self.window.trend.history[-1])
        self.assertIn('7.2 A', self.window.model.data(self.window.model.index(5, 2), Qt.ItemDataRole.ToolTipRole))
    def test_offline_disarms_visible_checkbox_and_send(self):
        self.window.arm.setChecked(True)
        self.assertTrue(self.window.send.isEnabled())
        self.now += 2
        self.client.armed = False
        self.window._refresh_telemetry()
        self.assertFalse(self.window.arm.isChecked())
        self.assertFalse(self.window.send.isEnabled())
        self.assertEqual(self.bus.sent, [])
    def test_readback_discrepancy_is_persistent_and_unconfirmed(self):
        self.client.last_config_readback = SimpleNamespace(number=1, status='discrepancy; unconfirmed',
            discrepancies={'high_current': (10, 12)})
        self.window._refresh_telemetry()
        self.assertIn('expected 10, observed 12', self.window.operation_status.text())
        self.assertIn('unconfirmed', self.window.operation_status.text())
    def test_disconnect_clears_values_trend_and_write_controls(self):
        self.window._refresh_telemetry()
        self.window.table.selectRow(0)
        self.window._disconnect()
        self.assertEqual(self.window.model.data(self.window.model.index(0, 2)), '—')
        self.assertEqual(list(self.window.trend.history), [])
        self.assertFalse(self.window.send.isEnabled())
        self.assertTrue(self.bus.closed)
    def test_trend_history_is_bounded(self):
        self.window._refresh_telemetry()
        row = self.window.model.samples[0]
        for _ in range(300):
            self.window.trend.append(row)
        self.assertEqual(len(self.window.trend.history), 180)


if __name__ == '__main__':
    unittest.main()
