"""Actual C scheduler snapshot -> Classic CAN decoder -> passive Qt dock."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication, QPushButton
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.integration import test_start_can as base


class StartCanUiIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(base.StartCanIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(base.StartCanIntegrationTests.tearDownClass.__func__)
    pump = base.StartCanIntegrationTests.pump
    until = base.StartCanIntegrationTests.until
    configure = base.StartCanIntegrationTests.configure
    assert_off = base.StartCanIntegrationTests.assert_off

    def setUp(self):
        base.StartCanIntegrationTests.setUp(self)
        self.__class__.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.window.protocol.setCurrentIndex(1)
        with patch('r2_pdm_configurator.app.open_adapter', return_value=self.bus), patch(
                'r2_pdm_configurator.app.HalCanClient', return_value=self.client):
            self.window._toggle_connection()
        self.window.timer.stop()
        self.panel = self.window.start_diagnostics

    def test_actual_24_channel_can_snapshot_renders_budget_then_stale_disconnect_clear(self):
        self.configure(profile=24, slots=4, total=120000, group=90000)
        self.bus.command('STARTALL')  # Host fixture input, never an outgoing CAN frame.
        self.until(lambda: self.client.start_snapshot_valid()
                   and self.client.start_snapshot.available
                   and self.client.start_snapshot.total_reserved_ma == 110000)
        self.window._refresh_telemetry()
        self.assertEqual(self.panel.table.rowCount(), 24)
        self.assertIn('110.000 A reserved / 120.000 A', self.panel.summary.text())
        self.assertIn('Start slots 3 / 4', self.panel.summary.text())
        self.assertIn('SIMULATION FIXTURES', self.panel.status.text())
        self.assertEqual(self.panel.table.item(3, 3).text(), 'total budget')
        self.assertEqual(self.panel.table.item(4, 3).text(), 'Waiting behind queue head')
        self.assertEqual(self.window.model.rowCount(), 38)
        self.assertEqual(self.panel.findChildren(QPushButton), [])
        self.assert_off()
        self.bus.inbound = lambda msg: None if msg.arbitration_id in base.START_IDS else msg
        self.pump(350)
        self.window._refresh_telemetry()
        self.assertTrue(self.client.online())
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertNotIn('110.000', self.panel.summary.text())
        self.window._disconnect()
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertIn('disconnected', self.panel.status.text())
        self.assertEqual(self.bus.sent, [])

    def test_actual_uncommissioned_14_metadata_does_not_invent_budgets(self):
        self.until(lambda: self.client.start_snapshot_valid())
        self.window._refresh_telemetry()
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertIn('UNCOMMISSIONED', self.panel.status.text())
        self.assertIn('14 advertised channels', self.panel.summary.text())
        self.assertIn('Unknown', self.panel.summary.text())
        self.assertNotIn('0.000 A', self.panel.summary.text())
        self.assertTrue(self.window.start_simulation_dock.isHidden())
        self.assert_off()


if __name__ == '__main__':
    unittest.main()
