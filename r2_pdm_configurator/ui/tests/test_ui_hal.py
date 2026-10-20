"""HAL mode has its own transport and explicit diagnostic upload authorization."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import can
from PyQt6.QtWidgets import QApplication, QMessageBox
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.hal_can import HalCanClient, HalUploadStatus
from r2_pdm_configurator.models.computed import Definition


class Bus:
    def __init__(self):
        self.sent = []
        self.closed = False
    def send(self, msg, **kwargs):
        self.sent.append(msg)
    def recv(self, timeout=0):
        return None
    def shutdown(self):
        self.closed = True


class HalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 100.0
        self.bus = Bus()
        self.window = MainWindow()
        self.window.protocol.setCurrentIndex(1)
        with patch('r2_pdm_configurator.app.open_adapter', return_value=self.bus), patch(
                'r2_pdm_configurator.app.HalCanClient', side_effect=lambda bus: HalCanClient(bus, now=lambda: self.now)):
            self.window._toggle_connection()
        self.window.timer.stop()
        self.client = self.window.client
        self.editor = self.window.hal_editor
        self.sequence = 0

    def tearDown(self):
        self.window.close()

    def heartbeat(self, flags=5):
        self.sequence += 1
        self.client.ingest(can.Message(arbitration_id=0x580, is_extended_id=False,
            data=struct.pack('<BBHI', 1, flags, self.sequence, int(self.now * 1000))))
        self.window._refresh_telemetry()

    def sample(self, channel, value, quality=0, sequence=1):
        self.client.ingest(can.Message(arbitration_id=0x581, is_extended_id=False,
            data=struct.pack('<HBBi', channel, quality, sequence, value)))
        self.window._refresh_telemetry()

    def row(self, channel):
        return next(row for row in self.window.model.samples if row.channel_id == f'HAL:{channel}')

    def test_connect_poll_and_legacy_controls_cannot_send_legacy_messages(self):
        self.assertEqual(self.bus.sent, [])
        self.heartbeat()
        self.window._tick()
        self.window.arm.setChecked(True)
        self.window._write_config()
        self.assertEqual(self.bus.sent, [])
        self.assertFalse(self.window.legacy_config_panel.isEnabled())
        self.assertTrue(self.window.legacy_config_panel.isHidden())
        self.assertFalse(self.window.send.isEnabled())
        self.assertFalse(self.window.computed_editor.isEnabled())
        self.assertFalse(self.window.protocol.isEnabled())
        self.assertEqual(self.window.model.rowCount(), 21)
        self.assertIn('unavailable', self.window.diagnostic_text.text())

    def test_actual_diagnostics_render_and_graph_without_fake_adc(self):
        self.heartbeat(flags=7)
        self.sample(200, 0)
        self.sample(230, 1)
        self.sample(300, 42)
        self.assertEqual(self.row(200).numeric, 0)
        self.assertEqual(self.row(230).numeric, 1)
        self.assertEqual(self.row(300).numeric, 42)
        self.window.table.selectRow(20)
        self.assertEqual(self.window.trend.history[-1], 42)
        self.assertEqual(self.window.tree.topLevelItem(0).childCount(), 21)
        self.assertFalse(any(row.unit in ('A', 'V', '°C') for row in self.window.model.samples))
        self.sample(300, 0, quality=1, sequence=2)
        self.assertIsNone(self.row(300).numeric)
        self.assertIsNone(self.window.trend.history[-1])

    def test_stale_heartbeat_disarms_and_never_sends(self):
        self.heartbeat()
        self.sample(200, 0)
        self.editor.arm.setChecked(True)
        self.assertTrue(self.editor.upload_btn.isEnabled())
        self.now += .5
        self.window._tick()
        self.window._refresh_telemetry()
        self.assertFalse(self.editor.arm.isChecked())
        self.assertFalse(self.editor.upload_btn.isEnabled())
        self.assertIsNone(self.row(200).numeric)
        self.assertEqual(self.bus.sent, [])

    def test_upload_cancel_confirmation_disarms_without_tx(self):
        self.heartbeat()
        self.editor.arm.setChecked(True)
        with patch('r2_pdm_configurator.ui.hal_vm.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            self.editor.upload_btn.click()
        self.assertEqual(self.bus.sent, [])
        self.assertFalse(self.client.armed)
        self.assertFalse(self.editor.arm.isChecked())

    def test_confirmation_rechecks_freshness_before_upload(self):
        self.heartbeat()
        self.editor.arm.setChecked(True)
        def delay(*args):
            self.now += .6
            return QMessageBox.StandardButton.Yes
        with patch('r2_pdm_configurator.ui.hal_vm.QMessageBox.question', side_effect=delay):
            self.editor.upload_btn.click()
        self.assertEqual(self.bus.sent, [])
        self.assertIn('rejected', self.editor.status.text())

    def test_confirmed_builder_upload_starts_only_new_protocol_and_not_verified(self):
        self.heartbeat()
        self.editor.arm.setChecked(True)
        with patch('r2_pdm_configurator.ui.hal_vm.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.editor.upload_btn.click()
        self.assertEqual([m.arbitration_id for m in self.bus.sent], [0x590])
        self.assertEqual(self.client.upload.state, 'begin')
        self.assertIn('Not verified', self.editor.status.text())
        self.assertFalse(self.client.armed)

    def test_status_truthfully_distinguishes_unknown_readback_and_verified(self):
        self.heartbeat()
        for state in ('unknown', 'readback', 'failed', 'verified'):
            self.client.upload = HalUploadStatus(state=state, active_crc=0x1234, expected_crc=0x1234)
            self.editor.refresh()
            self.assertEqual('ACK + CRC verified' in self.editor.status.text(), state == 'verified')
        self.assertEqual(self.bus.sent, [])

    def test_transport_send_exception_stays_in_ui_and_disarms(self):
        self.heartbeat()
        self.editor.arm.setChecked(True)
        with patch.object(self.bus, 'send', side_effect=can.CanError('Adapter disconnected')), patch(
                'r2_pdm_configurator.ui.hal_vm.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.editor.upload_btn.click()
        self.assertFalse(self.client.armed)
        self.assertFalse(self.editor.arm.isChecked())
        self.assertIn('Adapter disconnected', self.editor.status.text())
        self.assertEqual(self.bus.sent, [])

    def test_binary_load_rejects_store_feedback_and_oversize_without_tx(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'program.bin')
            good = b'\x02\xe6\x00\xff'
            with patch('r2_pdm_configurator.ui.hal_vm.QFileDialog.getOpenFileName', return_value=(path, '')):
                Path(path).write_bytes(good)
                self.editor.load_program()
                self.assertEqual(self.editor.program(), good)
                for bad in (b'x' * 129, b'\x02\x2c\x01\xff', b'\x01\x01\x00\x00\x00\x90\xc8\x00\xff'):
                    Path(path).write_bytes(bad)
                    self.editor.load_program()
                    self.assertIn('rejected', self.editor.file_label.text())
                    self.assertIsNone(self.editor.loaded_program)
                    with self.assertRaises(ValueError):
                        self.editor.program()
                    self.heartbeat()
                    self.editor.arm.setChecked(True)
                    self.editor.upload_program()
                    self.assertFalse(self.client.armed)
        self.assertEqual(self.bus.sent, [])

    def test_switch_back_preserves_local_computed_definitions(self):
        self.window.model.computed.add(Definition('Battery', 'SYS:Voltage'))
        self.window.protocol.setCurrentIndex(0)
        self.assertTrue(self.bus.closed)
        self.assertIsNone(self.window.client)
        self.assertEqual(self.window.model.rowCount(), 36)
        self.assertEqual(self.window.computed_tree.childCount(), 1)
        self.assertTrue(self.window.computed_editor.isEnabled())
        self.assertTrue(self.window.legacy_config_panel.isEnabled())
        self.assertEqual(self.bus.sent, [])


if __name__ == '__main__':
    unittest.main()
