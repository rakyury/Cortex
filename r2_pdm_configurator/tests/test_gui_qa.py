"""Offscreen lifecycle regressions; adapter construction is always intercepted."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication
import app as pdm_app


class OfflineBus:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.fail_recv = False

    def send(self, msg):
        self.sent.append(msg)

    def recv(self, timeout=0):
        if self.fail_recv:
            raise RuntimeError("simulated adapter disconnect")
        return None

    def shutdown(self):
        self.closed = True


class GuiLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = pdm_app.MainWindow()
        self.bus = OfflineBus()
        self.adapter = patch.object(pdm_app, "open_adapter", return_value=self.bus)
        self.open_mock = self.adapter.start()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.adapter.stop()

    def connect(self):
        self.window._toggle_connection()
        self.window.timer.stop()  # All ticks below are explicit and deterministic.

    def test_window_and_connection_never_send_without_heartbeat(self):
        self.assertEqual(self.window.table.model().rowCount(), 35)
        self.open_mock.assert_not_called()
        self.connect()
        self.window._tick()
        self.assertEqual(self.bus.sent, [])
        self.window._disconnect()
        self.assertTrue(self.bus.closed)
        self.assertIsNone(self.window.client)
        self.assertIsNone(self.window.model.client)
        self.assertFalse(self.window.arm.isChecked())

    def test_adapter_failure_disconnects_and_clears_model(self):
        self.connect()
        self.bus.fail_recv = True
        with patch.object(pdm_app.QMessageBox, "critical") as error:
            self.window._tick()
        error.assert_called_once()
        self.assertTrue(self.bus.closed)
        self.assertIsNone(self.window.client)
        self.assertFalse(self.window.timer.isActive())
        self.assertEqual(self.bus.sent, [])


if __name__ == "__main__":
    unittest.main()
