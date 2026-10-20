"""Real Python client -> simulated CAN -> actual C HAL protocol/R2 VM."""
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch

import can

from r2_pdm_configurator.hal_can import HalCanClient
from r2_pdm_configurator.integration.c_bus import CSimulatorBus, compile_simulator


def constant(value):
    return b"\x01" + struct.pack("<i", value) + b"\xff"


class FirmwareIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / ("link_simulator.exe" if os.name == "nt" else "link_simulator")
        try:
            compile_simulator(cls.binary)
        except Exception:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.clock = 10.0
        self.bus = CSimulatorBus(self.binary)
        # Independent cleanup must surface a simulator/sanitizer failure even
        # when MainWindow catches a transport shutdown exception in closeEvent.
        self.addCleanup(self.bus.shutdown)
        self.client = HalCanClient(self.bus, now=lambda: self.clock)
        self.addCleanup(self.client.close)
        self.pump(60)
        self.assertTrue(self.client.can_upload())

    def pump(self, ticks=1):
        for _ in range(ticks):
            self.clock += .01
            self.bus.advance(10)
            self.client.tick()

    def upload(self, code):
        self.client.armed = True
        self.client.begin_upload(code, confirmed=True)
        self.pump(30)

    def assert_outputs_off(self):
        self.pump(40)
        for channel in range(200, 214):
            self.assertTrue(self.client.sample_valid(channel), channel)
            self.assertEqual(self.client.samples[channel].value, 0)
        self.assertEqual(self.client.samples[230].value, 1)
        # Every valid output observation, including before/after malformed writes.
        for message in self.bus.history:
            if message.arbitration_id == 0x581:
                channel, quality, _, value = struct.unpack("<HBBi", message.data)
                if 200 <= channel <= 213 and quality == 0:
                    self.assertEqual(value, 0)

    def raw(self, cid, payload):
        replies = self.bus.command(f"RX {self.bus.now_ms} {cid:x} {payload.hex()}")
        return [struct.unpack("<IBBH", message.data) for message in replies
                if message.arbitration_id == 0x582]

    def raw_program(self, code, tid=0x12345678, crc=None):
        self.raw(0x590, struct.pack("<IHBB", tid, len(code), 1, 0))
        for offset in range(0, len(code), 2):
            self.raw(0x591, struct.pack("<IH", tid, offset) + code[offset:offset + 2].ljust(2, b"\0"))
        return self.raw(0x592, struct.pack("<II", tid, zlib.crc32(code) if crc is None else crc))

    def test_passive_connect_and_real_diagnostics(self):
        self.assertEqual(self.bus.sent, [])
        self.assertFalse(self.client.sample_valid(300))
        self.assertNotIn(100, self.client.samples)  # No fabricated ADC voltage.
        self.assert_outputs_off()

    def test_actual_vm_upload_ack_readback_and_result(self):
        code = b"\x02\xe6\x00" + b"\x01\x28\x00\x00\x00\x10\xff"  # interlock + 40
        self.upload(code)
        self.assertTrue(self.client.upload.verified, self.client.upload)
        self.assertEqual(self.client.upload.active_crc, zlib.crc32(code))
        self.pump(50)
        self.assertTrue(self.client.sample_valid(300))
        self.assertEqual(self.client.samples[300].value, 41)
        self.assertEqual({m.arbitration_id for m in self.bus.sent}, {0x590, 0x591, 0x592})
        self.assert_outputs_off()

    def test_crc_corruption_retains_old_active_program(self):
        self.upload(constant(7))
        self.assertTrue(self.client.upload.verified)
        def corrupt(message):
            if message.arbitration_id == 0x592:
                payload = bytearray(message.data)
                payload[4] ^= 1
                return can.Message(arbitration_id=0x592, data=payload, is_extended_id=False)
            return message
        self.bus.outbound = corrupt
        self.upload(constant(9))
        self.assertEqual(self.client.upload.state, "failed")
        self.bus.outbound = lambda message: message
        self.client.query_readback(expected_crc=zlib.crc32(constant(7)))
        self.pump(50)
        self.assertTrue(self.client.upload.verified)
        self.assertEqual(self.client.samples[300].value, 7)
        self.assert_outputs_off()

    def test_lost_commit_ack_is_unknown_then_explicit_query_reconciles(self):
        self.bus.inbound = lambda m: None if m.arbitration_id == 0x582 and m.data[4] == 2 else m
        self.upload(constant(123))
        self.pump(20)
        self.assertEqual(self.client.upload.state, "unknown")
        self.assertEqual(sum(m.arbitration_id == 0x592 for m in self.bus.sent), 1)
        self.bus.inbound = lambda message: message
        self.client.query_readback()
        self.pump(40)
        self.assertTrue(self.client.upload.verified)
        self.assertEqual(self.client.samples[300].value, 123)
        self.assert_outputs_off()

    def test_wrong_expected_readback_crc_never_verifies(self):
        self.upload(constant(77))
        self.assertTrue(self.client.upload.verified)
        self.client.query_readback(expected_crc=zlib.crc32(constant(78)))
        self.pump(40)
        self.assertEqual(self.client.upload.state, "failed")
        self.assertFalse(self.client.upload.verified)
        self.assertEqual(self.client.samples[300].value, 77)
        self.assert_outputs_off()

    def test_sample_loss_expires_even_with_fresh_heartbeat(self):
        self.assertTrue(self.client.sample_valid(240))
        self.bus.inbound = lambda m: None if (m.arbitration_id == 0x581 and
            int.from_bytes(m.data[:2], "little") == 240) else m
        self.pump(110)
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.sample_valid(240))
        self.assertTrue(self.client.sample_valid(230))

    def test_missing_data_times_out_without_activation(self):
        self.bus.outbound = lambda m: None if m.arbitration_id == 0x591 else m
        self.upload(constant(9))
        self.pump(100)
        self.assertEqual(self.client.upload.state, "failed")
        self.assertFalse(self.client.vm_active)
        self.assertFalse(any(m.arbitration_id == 0x592 for m in self.bus.sent))
        self.bus.outbound = lambda message: message
        self.upload(constant(10))
        self.assertTrue(self.client.upload.verified)
        self.assert_outputs_off()

    def test_identical_duplicate_data_is_idempotent(self):
        def duplicate(message):
            if message.arbitration_id == 0x591:
                self.bus.command(f"RX {self.bus.now_ms} 591 {bytes(message.data).hex()}")
            return message
        self.bus.outbound = duplicate
        self.upload(constant(55))
        self.assertTrue(self.client.upload.verified, self.client.upload)
        self.assert_outputs_off()

    def test_out_of_order_data_rejected_and_staging_expires(self):
        tid = 0x22222222
        self.assertEqual(self.raw(0x590, struct.pack("<IHBB", tid, 6, 1, 0))[0][2], 0)
        self.assertEqual(self.raw(0x591, struct.pack("<IHBB", tid, 2, 0, 0))[0][2], 3)
        self.pump(110)
        self.assertEqual(self.raw(0x592, struct.pack("<II", tid, 0))[0][2], 6)
        self.assertFalse(self.client.vm_active)
        self.assert_outputs_off()

    def test_store_unknown_opcode_and_unknown_source_cannot_replace_program(self):
        self.upload(constant(8))
        invalid = [b"\x01\x01\x00\x00\x00\x90\xc8\x00\xff",
                   b"\x02\x64\x00\xff", b"\x02\x2c\x01\xff",
                   b"\xfe\xff", b"\xff", constant(2) + b"\xff"]
        for index, code in enumerate(invalid):
            replies = self.raw_program(code, tid=0x1000 + index)
            self.assertEqual(replies[-1][2], 5, (code, replies))
            # An invalid candidate may retain staging until timeout; explicit abort
            # only discards that candidate, never the already-active expression.
            self.raw(0x593, struct.pack("<II", 0x1000 + index, 0))
        self.client.query_readback(expected_crc=zlib.crc32(constant(8)))
        self.pump(50)
        self.assertTrue(self.client.upload.verified)
        self.assertEqual(self.client.samples[300].value, 8)
        self.assert_outputs_off()

    def test_firmware_reset_discards_ram_vm_and_client_authorization(self):
        self.upload(constant(23))
        self.assertTrue(self.client.upload.verified)
        self.client.armed = True
        self.bus.command("RESET")
        self.bus.now_ms = 0
        self.pump(60)
        self.assertTrue(self.client.online())
        self.assertFalse(self.client.armed)
        self.assertFalse(self.client.vm_active)
        self.assertFalse(self.client.upload.verified)
        self.assertFalse(self.client.sample_valid(300))
        self.assert_outputs_off()

    def test_heartbeat_loss_clears_cached_values_and_disarms(self):
        self.client.armed = True
        self.bus.inbound = lambda m: None if m.arbitration_id == 0x580 else m
        self.pump(60)
        self.assertFalse(self.client.online())
        self.assertFalse(self.client.armed)
        self.assertEqual(self.client.samples, {})
        with self.assertRaises(PermissionError):
            self.client.begin_upload(constant(1), confirmed=True)

    def test_gui_upload_and_graph_use_actual_c_firmware_result(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from r2_pdm_configurator.app import MainWindow
        self.__class__.qt_app = QApplication.instance() or QApplication([])
        app = self.__class__.qt_app
        window = MainWindow()
        self.addCleanup(window.close)
        window.protocol.setCurrentIndex(1)
        with patch("r2_pdm_configurator.app.open_adapter", return_value=self.bus), patch(
                "r2_pdm_configurator.app.HalCanClient", return_value=self.client):
            window._toggle_connection()
        window.timer.stop()
        editor = window.hal_editor
        editor.source.setCurrentIndex(editor.source.findData(230))
        editor.operation.setCurrentIndex(editor.operation.findData(0x10))
        editor.constant.setValue(41)
        editor.arm.setChecked(True)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            editor.upload_program()
        self.pump(70)
        window._refresh_telemetry()
        window.table.selectRow(20)
        app.processEvents()
        self.assertTrue(self.client.upload.verified)
        self.assertIn("ACK + CRC verified", editor.status.text())
        self.assertEqual(window.model.samples[20].numeric, 42)
        self.assertEqual(window.trend.history[-1], 42)
        self.assertFalse(window.legacy_config_panel.isEnabled())
        self.assert_outputs_off()

    def test_simulator_exit_failure_cannot_be_swallowed_by_prior_cleanup(self):
        bus = CSimulatorBus(self.binary)
        bus._process.kill()
        bus._process.wait(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "Firmware simulator failed"):
            bus.shutdown()
        # Qt closeEvent may catch the first error; the independent gate still fails.
        with self.assertRaisesRegex(RuntimeError, "Firmware simulator failed"):
            bus.shutdown()

    def test_simulator_startup_protocol_failure_cleans_up_process(self):
        import subprocess
        spawned = []
        real_popen = subprocess.Popen
        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process
        with patch("r2_pdm_configurator.integration.c_bus.subprocess.Popen", side_effect=capture), patch.object(
                CSimulatorBus, "command", side_effect=RuntimeError("bad startup")):
            with self.assertRaisesRegex(RuntimeError, "bad startup"):
                CSimulatorBus(self.binary)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())


if __name__ == "__main__":
    unittest.main()
