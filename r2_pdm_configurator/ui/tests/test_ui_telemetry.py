"""Offscreen GUI suite; run separately with PyQt6 installed."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.tests import test_ui_model as model_tests


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        model_tests.TelemetryTests.setUp(self)
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
