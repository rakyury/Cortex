"""HAL CAN v1 host-only protocol and state-machine fault injection."""
import struct
import sys
import types
import unittest
import zlib
from unittest.mock import Mock, patch

from r2_pdm_configurator.hal_can import (
    HalCanClient, validate_program, HEARTBEAT, SAMPLE, ACK, READBACK,
    BEGIN, DATA, COMMIT, ABORT, QUERY, READABLE_CHANNELS,
    ADC_SAMPLE, ADC_CHANNELS, ADC_RANGES,
)
from r2_pdm_configurator.tests.test_can_protocol import FakeBus, FakeMessage

CODE = b"\x02\xe6\x00\xff"  # implemented channel230 interlock; END
ODD_CODE = b"\x02\xe6\x00\x42\xff"


class ProgramTests(unittest.TestCase):
    def test_allowed_sources_and_operations(self):
        for channel in READABLE_CHANNELS:
            self.assertEqual(validate_program(b"\x02" + struct.pack("<H", channel) + b"\xff")[-1], 255)
        for op in (0x10, 0x11, *range(0x30, 0x36), 0x40, 0x41, 0x50, 0x51):
            validate_program(CODE[:-1] * 2 + bytes((op, 255)))
        validate_program(ODD_CODE)
        validate_program(b"\x01\x00\x00\x00\x80\xff")

    def test_invalid_bytecode_and_sources_never_accepted(self):
        for code in (b"", bytearray(CODE), CODE + b"\0", b"\x01\x00\xff", b"\x10\xff",
                     b"\x02\xff", b"\x42\xff", CODE[:-1], CODE[:-1] * 2 + b"\xff",
                     CODE[:-1] + b"\x90\xc8\0\xff", b"\x00" * 129,
                     b"\x02\x2c\x01\xff", b"\x02\x64\0\xff", b"\x02\xff\xff\xff",
                     CODE[:-1] * 2 + b"\x12\xff"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                validate_program(code)


class HalClientTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.clock = 100.0
        self.client = HalCanClient(self.bus, now=lambda: self.clock)
        self.stub = patch.dict(sys.modules, {"can": types.SimpleNamespace(
            Message=lambda **kwargs: types.SimpleNamespace(**kwargs))})
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def feed(self, cid, payload):
        self.client.ingest(FakeMessage(cid, payload))

    def heartbeat(self, seq=1, uptime=1000, flags=5, version=1):
        self.feed(HEARTBEAT, struct.pack("<BBHI", version, flags, seq, uptime))

    def sample(self, channel=230, value=1, sequence=1, quality=0):
        self.feed(SAMPLE, struct.pack("<HBBi", channel, quality, sequence, value))

    def start(self, code=CODE):
        self.heartbeat()
        self.client.armed = True
        self.client.begin_upload(code, confirmed=True)
        self.tid = self.client.upload.tid

    def ack(self, op, offset, status=0, tid=None):
        self.feed(ACK, struct.pack("<IBBH", self.tid if tid is None else tid, op, status, offset))

    def readback(self, crc=None, tid=None):
        self.feed(READBACK, struct.pack("<II", self.tid if tid is None else tid,
                                       zlib.crc32(CODE) if crc is None else crc))

    def commit_pending(self):
        self.start()
        self.ack(0, 0)
        self.ack(1, 2)
        self.ack(1, 4)
        self.assertEqual(self.client.upload.state, "commit")

    def test_connect_and_heartbeat_never_transmit(self):
        self.client.tick()
        self.heartbeat()
        self.client.tick()
        self.assertEqual(self.bus.sent, [])

    def test_upload_requires_all_authorization_conditions(self):
        with self.assertRaises(PermissionError):
            self.client.begin_upload(CODE, confirmed=True)
        self.heartbeat()
        with self.assertRaises(PermissionError):
            self.client.begin_upload(CODE, confirmed=True)
        self.client.armed = True
        with self.assertRaises(PermissionError):
            self.client.begin_upload(CODE)
        self.heartbeat(seq=2, flags=1)
        self.client.armed = True
        with self.assertRaises(PermissionError):
            self.client.begin_upload(CODE, confirmed=True)
        self.assertEqual(self.bus.sent, [])

    def test_invalid_program_never_writes(self):
        self.heartbeat()
        self.client.armed = True
        with self.assertRaises(ValueError):
            self.client.begin_upload(b"\x90\xc8\0\xff", confirmed=True)
        self.assertEqual(self.bus.sent, [])

    def test_stop_wait_wire_layout_and_odd_padding(self):
        self.start(ODD_CODE)
        self.assertNotEqual(self.tid, 0)
        self.assertFalse(self.client.armed)
        self.assertEqual(self.bus.sent[0].arbitration_id, BEGIN)
        self.assertEqual(self.bus.sent[0].data, struct.pack("<IHBB", self.tid, 5, 1, 0))
        self.client.tick()
        self.assertEqual(len(self.bus.sent), 1)
        self.ack(0, 0)
        self.assertEqual(self.bus.sent[-1].data, struct.pack("<IH", self.tid, 0) + ODD_CODE[:2])
        self.ack(1, 2)
        self.ack(1, 4)
        self.assertEqual(self.bus.sent[-1].data, struct.pack("<IH", self.tid, 4) + b"\xff\0")
        self.ack(1, 5)
        self.assertEqual(self.bus.sent[-1].data, struct.pack("<II", self.tid, zlib.crc32(ODD_CODE)))
        self.assertEqual([m.arbitration_id for m in self.bus.sent], [BEGIN, DATA, DATA, DATA, COMMIT])

    def test_ack_and_readback_required_in_either_order(self):
        for readback_first in (False, True):
            with self.subTest(readback_first=readback_first):
                self.client = HalCanClient(self.bus, now=lambda: self.clock)
                self.commit_pending()
                if readback_first:
                    self.readback()
                else:
                    self.ack(2, 4)
                self.assertFalse(self.client.upload.verified)
                if readback_first:
                    self.ack(2, 4)
                else:
                    self.readback()
                self.assertTrue(self.client.upload.verified)

    def test_wrong_tid_operation_offset_and_duplicate_do_not_advance(self):
        self.start()
        self.ack(0, 0, tid=self.tid ^ 1)
        self.ack(1, 0)
        self.ack(0, 2)
        self.assertEqual(len(self.bus.sent), 1)
        self.ack(0, 0)
        self.ack(0, 0)
        self.ack(1, 0)
        self.assertEqual(len(self.bus.sent), 2)
        self.ack(1, 2)
        self.ack(1, 2)
        self.assertEqual(len(self.bus.sent), 3)

    def test_commit_timeout_unknown_no_retry_then_explicit_query(self):
        self.commit_pending()
        count = len(self.bus.sent)
        self.clock += .36
        self.client.tick()
        self.assertEqual(self.client.upload.state, "unknown")
        self.assertEqual(len(self.bus.sent), count)
        old_tid = self.tid
        self.client.query_readback()
        self.tid = self.client.upload.tid
        self.assertNotEqual(self.tid, old_tid)
        self.assertEqual(self.bus.sent[-1].arbitration_id, QUERY)
        self.readback(tid=old_tid)
        self.ack(4, 0)
        self.assertFalse(self.client.upload.verified)
        self.readback()
        self.assertTrue(self.client.upload.verified)

    def test_begin_timeout_discards_late_ack_without_retry(self):
        self.start()
        self.clock += .35
        self.ack(0, 0)
        self.assertEqual(self.client.upload.state, "failed")
        self.assertEqual(len(self.bus.sent), 1)

    def test_crc_mismatch_and_device_errors_never_verify(self):
        self.commit_pending()
        self.ack(2, 4)
        self.readback(crc=0)
        self.assertEqual(self.client.upload.state, "failed")
        self.client.query_readback()
        self.tid = self.client.upload.tid
        self.ack(4, 0, status=7)
        self.assertFalse(self.client.upload.verified)
        self.assertIn("status 7", self.client.upload.error)

    def test_query_without_expected_crc_reports_readback_only(self):
        self.heartbeat()
        self.client.query_readback()
        self.tid = self.client.upload.tid
        self.readback()
        self.ack(4, 0)
        self.assertEqual(self.client.upload.state, "readback")
        self.assertFalse(self.client.upload.verified)

    def test_cancel_staging_sends_only_matching_abort(self):
        self.start()
        self.client.cancel()
        self.assertFalse(self.client.armed)
        self.assertEqual(self.bus.sent[-1].arbitration_id, ABORT)
        self.assertEqual(self.bus.sent[-1].data, struct.pack("<II", self.tid, 0))
        self.ack(3, 0)
        self.assertEqual(self.client.upload.state, "cancelled")

    def test_cancel_after_commit_does_not_claim_active_code_removed(self):
        self.commit_pending()
        count = len(self.bus.sent)
        self.client.cancel()
        self.assertEqual(self.client.upload.state, "unknown")
        self.assertEqual(len(self.bus.sent), count)

    def test_reboot_and_heartbeat_expiry_clear_state_and_disarm(self):
        self.start()
        self.sample()
        self.heartbeat(seq=0, uptime=0)
        self.assertFalse(self.client.armed)
        self.assertEqual(self.client.samples, {})
        self.assertEqual(self.client.upload.state, "idle")
        self.ack(0, 0)
        self.assertEqual(len(self.bus.sent), 1)
        self.sample()
        self.client.armed = True
        self.clock += .5
        self.client.tick()
        self.assertFalse(self.client.online())
        self.assertFalse(self.client.armed)
        self.assertEqual(self.client.samples, {})

    def test_heartbeat_and_sample_rollover_and_duplicate_rejection(self):
        self.heartbeat(seq=65535, uptime=0xfffffff0)
        self.sample(sequence=255)
        self.clock += .1
        self.heartbeat(seq=0, uptime=84)
        self.sample(sequence=0)
        self.assertTrue(self.client.sample_valid(230))
        seen = self.client.samples[230].last_seen
        self.clock += .1
        self.sample(sequence=0)
        self.sample(sequence=255)
        self.assertEqual(self.client.samples[230].last_seen, seen)
        self.heartbeat(seq=0, uptime=84)
        self.assertEqual(self.client.heartbeat_last_seen, 100.1)

    def test_sample_quality_whitelist_and_vm_active_gate(self):
        self.heartbeat()
        self.sample(channel=1)
        self.sample(channel=300, value=42)
        self.assertEqual(self.client.samples, {})
        self.sample(quality=1)
        self.assertFalse(self.client.sample_valid(230))
        self.heartbeat(seq=2, flags=7)
        self.sample(channel=300, value=-42)
        self.assertTrue(self.client.sample_valid(300))
        self.heartbeat(seq=3, flags=5)
        self.assertFalse(self.client.sample_valid(300))

    def test_stale_sample_can_reestablish_sequence_after_long_gap(self):
        self.heartbeat()
        self.sample(sequence=1)
        for sequence in range(2, 8):
            self.clock += .2
            self.heartbeat(seq=sequence, uptime=1000 + sequence * 200)
        self.assertFalse(self.client.sample_valid(230))
        self.sample(sequence=200)
        self.assertTrue(self.client.sample_valid(230))
        self.assertEqual(self.client.samples[230].sequence, 200)

    def test_interlock_or_capability_loss_stops_upload(self):
        self.start()
        self.heartbeat(seq=2, flags=1)
        self.ack(0, 0)
        self.assertEqual(len(self.bus.sent), 1)
        self.assertFalse(self.client.armed)
        self.sample(channel=200, value=1)
        self.assertFalse(self.client.online())

    def test_frame_shapes_cannot_refresh_or_advance(self):
        for attribute in ("is_extended_id", "is_remote_frame", "is_error_frame", "is_fd"):
            msg = FakeMessage(HEARTBEAT, struct.pack("<BBHI", 1, 5, 1, 1000))
            setattr(msg, attribute, True)
            self.client.ingest(msg)
        self.feed(HEARTBEAT, bytes(7))
        self.assertFalse(self.client.online())
        self.heartbeat(flags=8)
        self.assertFalse(self.client.online())

    def test_transport_errors_and_close_clear_authorization(self):
        self.start()
        self.bus.recv = Mock(side_effect=OSError("removed"))
        with self.assertRaises(OSError):
            self.client.tick()
        self.assertFalse(self.client.online())
        self.assertFalse(self.client.armed)
        self.assertEqual(self.client.upload.state, "idle")
        self.client.close()
        self.client.close()
        self.assertTrue(self.bus.closed)

    def test_receive_budget_and_pending_transaction_bounds(self):
        self.bus.received = [FakeMessage(0x100, bytes(8)) for _ in range(65)]
        self.client.tick()
        self.assertEqual(len(self.bus.received), 1)
        with self.assertRaises(ValueError):
            self.client.tick(receive_budget=257)
        self.start()
        with self.assertRaises(RuntimeError):
            self.client.begin_upload(CODE, confirmed=True)
        with self.assertRaises(RuntimeError):
            self.client.query_readback()


class ADCClientTests(unittest.TestCase):
    setUp = HalClientTests.setUp
    feed = HalClientTests.feed
    heartbeat = HalClientTests.heartbeat
    sample = HalClientTests.sample

    def adc(self, channel=100, value=12500, quality=2, sequence=1, cid=ADC_SAMPLE):
        self.feed(cid, struct.pack("<HBBi", channel, quality, sequence, value))

    def test_seventeen_adc_channels_and_only_numeric_qualities(self):
        self.heartbeat()
        self.assertEqual(len(ADC_CHANNELS), 17)
        for channel in ADC_CHANNELS:
            for sequence, quality in enumerate((0, 1, 2, 3, 4), 1):
                self.adc(channel, 100, quality, sequence)
                self.assertEqual(self.client.sample_valid(channel), quality in (0, 2))
                self.assertEqual(self.client.samples[channel].value, 100 if quality in (0, 2) else 0)

    def test_strict_ranges_replace_last_numeric_with_invalid_gap(self):
        self.heartbeat()
        for channel, (low, high) in ADC_RANGES.items():
            for sequence, value in enumerate((low, high, low - 1, high + 1), 1):
                self.adc(channel, value, 2, sequence)
                if sequence <= 2:
                    self.assertTrue(self.client.sample_valid(channel), (channel, value))
                else:
                    self.assertFalse(self.client.sample_valid(channel), (channel, value))
                    self.assertEqual(self.client.samples[channel].quality, 3)
        self.assertEqual(self.client.adc_bad_frames, len(ADC_CHANNELS) * 2)

    def test_adc_frame_has_disjoint_ids_and_quality_semantics(self):
        self.heartbeat()
        self.adc(channel=230, value=1)  # diagnostic ID on ADC frame
        self.adc(cid=SAMPLE)  # ADC ID on diagnostic frame
        self.sample(quality=2)  # new nominal quality forbidden on old frame
        self.adc(quality=5)
        self.assertEqual(self.client.samples, {})
        self.assertEqual(self.client.adc_bad_frames, 2)
        self.assertEqual(self.client.bad_frames, 4)

    def test_diagnostic_packets_never_refresh_adc_host_age(self):
        self.heartbeat()
        self.adc()
        for sequence in range(2, 8):
            self.clock += .2
            self.heartbeat(seq=sequence, uptime=sequence * 200 + 1000)
            self.sample(sequence=sequence)
        self.assertTrue(self.client.online())
        self.assertTrue(self.client.sample_valid(230))
        self.assertFalse(self.client.sample_valid(100))
        self.assertEqual(self.client.adc_rx_frames, 1)
        self.assertEqual(self.client.adc_bad_frames, 0)

    def test_adc_sequence_rollover_independent_of_diagnostics(self):
        self.heartbeat()
        self.adc(sequence=255)
        self.adc(value=13000, sequence=0)
        self.adc(value=14000, sequence=255)
        self.adc(value=15000, sequence=0)
        self.assertEqual(self.client.samples[100].value, 13000)
        self.assertEqual(self.client.adc_bad_frames, 2)
        self.sample(sequence=255)
        self.sample(sequence=0)
        self.assertTrue(self.client.sample_valid(230))

    def test_missing_or_malformed_adc_does_not_mark_diagnostics_offline(self):
        self.heartbeat()
        self.sample()
        for attribute in ("is_extended_id", "is_remote_frame", "is_fd", "is_error_frame"):
            msg = FakeMessage(ADC_SAMPLE, struct.pack("<HBBi", 100, 2, 1, 12500))
            setattr(msg, attribute, True)
            self.client.ingest(msg)
        self.feed(ADC_SAMPLE, bytes(7))
        mismatch = FakeMessage(ADC_SAMPLE, bytes(8))
        mismatch.dlc = 7
        self.client.ingest(mismatch)
        self.assertEqual(self.client.adc_bad_frames, 6)
        self.assertTrue(self.client.online())
        self.assertTrue(self.client.sample_valid(230))
        self.assertFalse(self.client.sample_valid(100))

    def test_nominal_current_is_not_forced_zero_when_gates_off(self):
        self.heartbeat()
        self.sample(channel=200, value=0)
        self.adc(channel=110, value=321)
        self.assertEqual(self.client.samples[110].value, 321)
        self.assertTrue(self.client.sample_valid(110))
        self.adc(channel=110, value=0, sequence=2)
        self.assertTrue(self.client.sample_valid(110))

    def test_adc_requires_heartbeat_and_restart_clears_samples(self):
        self.adc()
        self.assertEqual(self.client.samples, {})
        self.heartbeat()
        self.adc()
        self.heartbeat(seq=0, uptime=0)
        self.assertEqual(self.client.samples, {})
        self.adc()
        self.clock += .5
        self.client.tick()
        self.assertEqual(self.client.samples, {})

    def test_adc_sources_still_rejected_by_vm_validator(self):
        for channel in ADC_CHANNELS:
            with self.assertRaises(ValueError):
                validate_program(b"\x02" + struct.pack("<H", channel) + b"\xff")


if __name__ == "__main__":
    unittest.main()
