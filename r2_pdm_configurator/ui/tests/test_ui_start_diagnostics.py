"""Read-only scheduler UI: fresh coherent snapshots, never a control surface."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace as Record
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication, QPushButton, QSpinBox
from r2_pdm_configurator.ui.start_diagnostics import StartDiagnosticsPanel
from r2_pdm_configurator.models.start_diagnostics import start_diagnostics_view
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.ui.tests.test_ui_hal import Bus


def snapshot(count=24, available=True, simulation=True):
    channels = tuple(Record(channel=i, state='STARTING' if i < 3 else 'QUEUED',
        reason='SLOTS' if i == 3 else 'OK', reserve_ma=30000 if i < 3 else 0,
        group=i % 2, queue_position=None if i < 3 else i - 2, slot_reserved=i < 3)
        for i in range(1, count + 1)) if available else ()
    return Record(profile=count, count=count, slots=2 if available else None,
        available=available, simulation_only=simulation, inhibited=True,
        physical_outputs_off=True, sequence=3, captured_ms=100, last_seen=100.0,
        total_budget_ma=120000 if available else None,
        total_reserved_ma=80000 if available else None,
        total_measured_ma=20000 if available else None,
        starts_reserved=2 if available else None, queue=tuple(range(3, count + 1)) if available else (),
        groups=(Record(id=0, reserved_ma=40000, budget_ma=90000),
                Record(id=1, reserved_ma=40000, budget_ma=90000)) if available else (), channels=channels)


class Client:
    def __init__(self, value):
        self.start_snapshot = value
        self.start_snapshot_error = ''
        self.fresh = self.connected = True
        self.time = 100.1
    def now(self): return self.time
    def online(self): return self.connected
    def start_snapshot_valid(self): return self.fresh
    def send(self, *args): raise AssertionError('Read-only view must not send')


class StartDiagnosticsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = StartDiagnosticsPanel()

    def tearDown(self):
        self.panel.close()

    def test_fresh_profiles_expand_only_separate_diagnostic_rows(self):
        for count in (14, 24):
            self.panel.refresh(Client(snapshot(count)))
            self.assertEqual(self.panel.table.rowCount(), count)
            self.assertIn(f'Profile {count}', self.panel.summary.text())
            self.assertIn('SIMULATION FIXTURES', self.panel.status.text())
            self.assertIn('80.000 A reserved / 120.000 A', self.panel.summary.text())
            self.assertIn('Current 20.000 A', self.panel.summary.text())
            self.assertIn('Group 0: 40.000 A', self.panel.groups.text())
            self.assertEqual(self.panel.table.item(2, 1).text(), '1')
            self.assertEqual(self.panel.table.item(2, 3).text(), 'slots')
            self.assertEqual(self.panel.table.item(3, 3).text(), 'Waiting behind queue head')

    def test_no_controls_or_writes_and_immutable_presentation(self):
        value = snapshot()
        view = start_diagnostics_view(Client(value))
        self.panel.refresh(Client(value))
        self.assertEqual(self.panel.findChildren(QPushButton), [])
        self.assertEqual(self.panel.findChildren(QSpinBox), [])
        self.assertEqual(self.panel.table.editTriggers(), self.panel.table.EditTrigger.NoEditTriggers)
        self.assertIsInstance(view.rows, tuple)
        self.assertEqual(value.channels[0].state, 'STARTING')

    def test_uncommissioned_hardware_has_unknown_not_fabricated_zero(self):
        self.panel.refresh(Client(snapshot(14, available=False, simulation=False)))
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertIn('UNCOMMISSIONED', self.panel.status.text())
        self.assertIn('14 advertised channels', self.panel.summary.text())
        self.assertIn('total budget: Unknown', self.panel.summary.text())
        self.assertNotIn('0.000 A', self.panel.summary.text())
        self.assertEqual(self.panel.groups.text(), '')

    def test_scheduler_stale_while_heartbeat_live_clears_all_numeric_values(self):
        client = Client(snapshot())
        self.panel.refresh(client)
        client.fresh = False
        client.time += 2
        client.start_snapshot_error = 'Snapshot expired'
        self.panel.refresh(client)
        self.assertEqual(self.panel.table.rowCount(), 0)
        self.assertIn('stale or incomplete', self.panel.status.text())
        self.assertNotIn('80.000', self.panel.summary.text())
        self.assertEqual(self.panel.groups.text(), '')

    def test_disconnected_and_missing_old_firmware_snapshots_are_unavailable(self):
        client = Client(snapshot())
        self.panel.refresh(client)
        client.connected = False
        self.panel.refresh(client)
        self.assertIn('heartbeat', self.panel.status.text())
        self.assertEqual(self.panel.table.rowCount(), 0)
        client.connected = True
        client.start_snapshot = None
        self.panel.refresh(client)
        self.assertIn('Waiting for a complete', self.panel.status.text())
        self.panel.refresh(None)
        self.assertIn('disconnected', self.panel.status.text())

    def test_held_fault_slot_and_queued_pending_reason_are_distinct(self):
        value = snapshot()
        value.channels[0].state = 'FAULT'
        value.channels[0].reason = 'DEADLINE'
        value.channels[2].reason = 'OK'
        self.panel.refresh(Client(value))
        self.assertEqual(self.panel.table.item(0, 2).text(), 'FAULT')
        self.assertEqual(self.panel.table.item(0, 4).text(), '30.000 A')
        self.assertEqual(self.panel.table.item(0, 6).text(), 'Held')
        self.assertEqual(self.panel.table.item(2, 3).text(), 'Awaiting dispatch')

    def test_unknown_current_keeps_coherent_reservations_and_queue_visible(self):
        value = snapshot()
        value.total_measured_ma = None
        value.inhibited = True
        self.panel.refresh(Client(value))
        self.assertIn('Current Unknown', self.panel.summary.text())
        self.assertIn('80.000 A reserved / 120.000 A budget', self.panel.summary.text())
        self.assertIn('inhibited', self.panel.summary.text())
        self.assertEqual(self.panel.table.rowCount(), 24)
        self.assertEqual(self.panel.table.item(0, 4).text(), '30.000 A')
        self.assertEqual(self.panel.table.item(2, 1).text(), '1')

    def test_mainwindow_hal_dock_is_separate_and_connection_is_passive(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertTrue(window.start_diagnostics_dock.isHidden())
        window.protocol.setCurrentIndex(1)
        self.assertFalse(window.start_diagnostics_dock.isHidden())
        self.assertTrue(window.start_simulation_dock.isHidden())
        bus = Bus()
        with patch('r2_pdm_configurator.app.open_adapter', return_value=bus):
            window._toggle_connection()
        window.timer.stop()
        window._refresh_telemetry()
        self.assertEqual(bus.sent, [])
        self.assertEqual(window.model.rowCount(), 38)
        self.assertEqual(window.channel.maximum(), 14)
        window._disconnect()
        self.assertEqual(window.start_diagnostics.table.rowCount(), 0)
        window.protocol.setCurrentIndex(0)
        self.assertTrue(window.start_diagnostics_dock.isHidden())


if __name__ == '__main__':
    unittest.main()
