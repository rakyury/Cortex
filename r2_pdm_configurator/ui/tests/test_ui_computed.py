"""Computed channel GUI actions must use the VM without sending CAN traffic."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.tests import test_ui_model as model_tests


class ComputedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        model_tests.TelemetryTests.setUp(self)
        self.window = MainWindow()
        self.window.client = self.client
        self.window.model.client = self.client
        self.editor = self.window.computed_editor

    def tearDown(self):
        self.window.close()

    def add(self, name='Low battery', source='SYS:Voltage', operation='lt', constant=11500):
        self.editor.name.setText(name)
        self.editor.source.setCurrentIndex(self.editor.source.findData(source))
        self.editor.operation.setCurrentIndex(self.editor.operation.findData(operation))
        self.editor.other_source.setCurrentIndex(0)
        self.editor.constant.setValue(constant)
        self.editor.add_btn.click()

    def test_editor_executes_vm_and_populates_table_tree_and_graph_without_writes(self):
        self.add()
        self.assertEqual(self.window.model.rowCount(), 36)
        self.assertEqual(self.window.model.samples[-1].numeric, 0)
        self.assertIn('Local preview', self.window.model.samples[-1].status)
        item = self.window.computed_tree.child(0)
        self.window._select_tree_channel(item, 0)
        self.assertEqual(self.window.table.currentIndex().row(), 35)
        self.assertEqual(self.window.trend.history[-1], 0)
        self.client.system.battery_v = 11.0
        self.window._refresh_telemetry()
        self.assertEqual(self.window.trend.history[-1], 1)
        self.assertEqual(self.bus.sent, [])
        self.assertFalse(self.client.armed)

    def test_stale_disconnect_and_missing_sources_never_become_zero(self):
        self.add('Output current', 'OUT:01', 'identity')
        self.assertEqual(self.window.model.samples[-1].numeric, 7200)
        self.window.table.selectRow(35)
        self.now += 7
        self.client.system.last_seen = self.now
        self.window._refresh_telemetry()
        self.assertIsNone(self.window.model.samples[-1].numeric)
        self.assertIsNone(self.window.trend.history[-1])
        self.window._disconnect()
        self.assertEqual(self.window.model.rowCount(), 36)
        self.assertIsNone(self.window.model.samples[-1].numeric)
        self.assertEqual(self.bus.sent, [])
        self.assertFalse(self.window.send.isEnabled())

    def test_update_remove_and_rejected_edit_preserve_model_consistency(self):
        self.add()
        self.editor.constant.setValue(15000)
        self.editor.update_btn.click()
        self.assertEqual(self.window.model.samples[-1].numeric, 1)
        self.editor.name.setText(' ')
        self.editor.update_btn.click()
        self.assertEqual(self.window.model.rowCount(), 36)
        self.assertEqual(self.window.model.computed.definitions[0].name, 'Low battery')
        self.editor.remove_btn.click()
        self.assertEqual(self.window.model.rowCount(), 35)
        self.assertEqual(self.window.computed_tree.childCount(), 0)
        self.assertEqual(self.bus.sent, [])

    def test_local_file_roundtrip_and_malformed_load_are_atomic(self):
        self.add()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'computed.json')
            with patch('r2_pdm_configurator.ui.computed.QFileDialog.getSaveFileName', return_value=(path, '')):
                self.editor.save_btn.click()
            self.editor.remove_btn.click()
            with patch('r2_pdm_configurator.ui.computed.QFileDialog.getOpenFileName', return_value=(path, '')):
                self.editor.load_btn.click()
                self.assertEqual(self.window.model.samples[-1].numeric, 0)
                Path(path).write_text('{"version":999}', encoding='utf-8')
                self.editor.load_btn.click()
                self.assertEqual(len(self.window.model.computed.definitions), 1)
                Path(path).write_bytes(b' ' * 32769)
                self.editor.load_btn.click()
                self.assertEqual(len(self.window.model.computed.definitions), 1)
        self.assertEqual(self.bus.sent, [])

    def test_invalid_unit_combination_is_visible_and_not_plotted(self):
        self.add('Invalid sum', operation='add', constant=0)
        self.editor.other_source.setCurrentIndex(self.editor.other_source.findData('OUT:01'))
        self.editor.update_btn.click()
        self.assertEqual(self.window.model.samples[-1].quality, 'invalid')
        self.window.table.selectRow(35)
        self.assertIsNone(self.window.trend.history[-1])
        self.assertEqual(self.bus.sent, [])

    def test_disconnect_clears_previous_connection_readback(self):
        self.window.operation_status.setText('OUT 1: pending')
        self.window._disconnect()
        self.assertIn('Disconnected', self.window.operation_status.text())
        self.assertNotIn('pending', self.window.operation_status.text())


if __name__ == '__main__':
    unittest.main()
