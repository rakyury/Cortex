"""Independent CAN scheduler wire fixtures and incomplete/corrupt transfer cases."""
import dataclasses
import struct
import unittest
import zlib

from r2_pdm_configurator.hal_can import HalCanClient, HEARTBEAT
from r2_pdm_configurator.start_telemetry import START_BEGIN, START_DATA, START_END
from r2_pdm_configurator.tests.test_can_protocol import FakeBus, FakeMessage


def body(profile=24, slots=2, captured=1000, available=True):
    if not available:
        return struct.pack('<8B4I',14,14,12,0,0,0,0,0,captured,0,0,0) + bytes((0,1,0,255,0,0,0,0))*14
    # Onegroup, explicit20A baseline, firstN STARTING30A, remainingqueued.
    total=20000+slots*30000
    header=struct.pack('<8B4I',profile,profile,11,slots,slots,1,profile-slots,0,captured,1000000,total,total)
    groups=struct.pack('<II',1000000,total)
    channels=b''.join(struct.pack('<BBBBI',130,0,0,0,30000) if i<slots else
                      struct.pack('<BBBBI',1,7 if i==slots else 0,i-slots+1,0,0) for i in range(profile))
    return header+groups+channels


def frames(payload=None, sequence=1):
    payload=body() if payload is None else payload
    begin=struct.pack('<BBHI',1,0,len(payload),sequence)
    result=[(START_BEGIN,begin)]
    result += [(START_DATA,struct.pack('<HB',sequence&65535,i//5)+payload[i:i+5].ljust(5,b'\0'))
               for i in range(0,len(payload),5)]
    return result+[(START_END,struct.pack('<II',sequence,zlib.crc32(begin+payload)))]


class StartTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.0
        self.bus=FakeBus()
        self.client=HalCanClient(self.bus,now=lambda:self.clock)
        self.hb_sequence=0
        self.heartbeat()

    def feed(self, cid, payload): self.client.ingest(FakeMessage(cid,payload))
    def heartbeat(self,uptime=None):
        self.hb_sequence+=1
        if uptime is None: uptime=1000+round((self.clock-100)*1000)
        self.feed(HEARTBEAT,struct.pack('<BBHI',1,5,self.hb_sequence,uptime&0xffffffff))
    def send(self, transfer):
        for frame in transfer: self.feed(*frame)

    def test_profiles_and_slots_decode_atomically_and_never_write(self):
        sequence=0
        for profile in (14,24):
            for slots in (2,3,4):
                sequence+=1
                transfer=frames(body(profile,slots),sequence)
                old=self.client.start_snapshot
                self.send(transfer[:-1]); self.assertIs(self.client.start_snapshot,old)
                self.send(transfer[-1:]); self.assertTrue(self.client.start_snapshot_valid())
                snapshot=self.client.start_snapshot
                self.assertEqual((snapshot.profile,snapshot.count,snapshot.slots),(profile,profile,slots))
                self.assertEqual(snapshot.queue,tuple(range(slots+1,profile+1)))
                self.assertEqual(snapshot.starts_reserved,slots)
                self.assertEqual(snapshot.total_reserved_ma,20000+slots*30000)
                self.assertTrue(snapshot.available and snapshot.simulation_only and snapshot.physical_outputs_off)
                with self.assertRaises(dataclasses.FrozenInstanceError): snapshot.count=99
        self.assertEqual(self.bus.sent,[])

    def test_hardware_uncommissioned_is_metadata_not_fabricated_zeros(self):
        self.send(frames(body(available=False)))
        snapshot=self.client.start_snapshot
        self.assertTrue(self.client.start_snapshot_valid())
        self.assertFalse(snapshot.available or snapshot.simulation_only)
        self.assertTrue(snapshot.inhibited)
        self.assertIsNone(snapshot.slots); self.assertIsNone(snapshot.total_budget_ma)
        self.assertIsNone(snapshot.total_measured_ma); self.assertEqual(snapshot.channels,())
        self.assertEqual(snapshot.groups,()); self.assertEqual(snapshot.queue,())

    def test_identical_duplicates_do_not_extend_deadline(self):
        transfer=frames()
        self.send(transfer[:2]); self.send(transfer[:2])
        self.send(transfer[2:]); self.assertTrue(self.client.start_snapshot_valid())
        self.send(frames(sequence=2)[:1])
        for i in range(5):
            self.clock+=.3; self.heartbeat(); self.send(frames(sequence=2)[:1])
        self.clock+=.01; self.heartbeat()
        self.send(frames(sequence=2)[1:])
        self.assertFalse(self.client.start_snapshot_valid())

    def test_missing_reordered_conflicting_chunks_or_end_clear_snapshot(self):
        transfer=frames()
        cases=[transfer[:2]+transfer[3:],transfer[:1]+[transfer[2],transfer[1]]+transfer[3:],
               transfer[:2]+[(START_DATA,transfer[1][1][:-1]+bytes([transfer[1][1][-1]^1]))]+transfer[2:],
               transfer[:-2]+transfer[-1:]]
        for broken in cases:
            self.setUp(); self.send(broken)
            self.assertFalse(self.client.start_snapshot_valid())
            self.assertGreater(self.client.start_bad_frames,0)

    def test_crc_corruption_and_nonzero_final_padding_rejected(self):
        transfer=frames()
        transfer[-1]=(START_END,transfer[-1][1][:-1]+bytes([transfer[-1][1][-1]^1]))
        self.send(transfer); self.assertIsNone(self.client.start_snapshot)
        transfer=frames(sequence=2)
        self.assertNotEqual(len(body())%5,0)
        transfer[-2]=(START_DATA,transfer[-2][1][:-1]+b'\x01')
        self.send(transfer); self.assertIsNone(self.client.start_snapshot)

    def test_schema_mutations_with_correct_crc_fail(self):
        mutations=[(0,99),(1,14),(2,3),(2,43),(3,1),(4,3),(5,25),(6,25),(7,1),
                   (32,0),(33,255),(34,1),(35,1),(32+8*2+2,2),(32+8*3+2,1)]
        for sequence,(offset,value) in enumerate(mutations,1):
            payload=bytearray(body()); payload[offset]=value
            self.send(frames(bytes(payload),sequence))
            self.assertIsNone(self.client.start_snapshot,(offset,value))
        payload=bytearray(body(available=False)); payload[24+4]=1
        self.send(frames(bytes(payload),100)); self.assertIsNone(self.client.start_snapshot)

    def test_unavailable_measurement_preserves_queue_and_reservations(self):
        payload=bytearray(body())
        payload[2]=31  # available, simulation, inhibited, physicalOFF, measurement unavailable
        struct.pack_into('<I',payload,20,0)
        self.send(frames(bytes(payload)))
        snapshot=self.client.start_snapshot
        self.assertTrue(self.client.start_snapshot_valid())
        self.assertTrue(snapshot.available and snapshot.inhibited)
        self.assertIsNone(snapshot.total_measured_ma)
        self.assertEqual(snapshot.total_reserved_ma,80000)
        self.assertEqual(snapshot.starts_reserved,2)
        self.assertEqual(snapshot.queue,tuple(range(3,25)))
        self.assertEqual(snapshot.channels[0].reserve_ma,30000)

    def test_unavailable_measurement_requires_inhibit_and_zero_sentinel(self):
        payload=bytearray(body())
        payload[2]=31  # Nonzero measured value cannot be silently hidden.
        self.send(frames(bytes(payload),1)); self.assertIsNone(self.client.start_snapshot)
        struct.pack_into('<I',payload,20,0)
        payload[2]=27  # Missing inhibited bit.
        self.send(frames(bytes(payload),2)); self.assertIsNone(self.client.start_snapshot)
        payload=bytearray(body(available=False)); payload[2]=28
        self.send(frames(bytes(payload),3)); self.assertIsNone(self.client.start_snapshot)
        # Available genuine measured zero without the quality flag stays numeric.
        payload=bytearray(body()); struct.pack_into('<I',payload,20,0)
        self.send(frames(bytes(payload),4)); self.assertEqual(self.client.start_snapshot.total_measured_ma,0)

    def test_invalid_header_version_reserved_length_and_stale_generation(self):
        for version,reserved,length in ((2,0,224),(1,1,224),(1,0,409),(1,0,23),(1,0,25)):
            self.feed(START_BEGIN,struct.pack('<BBHI',version,reserved,length,1))
            self.assertIsNone(self.client.start_snapshot)
        self.send(frames(sequence=10)); self.assertTrue(self.client.start_snapshot_valid())
        self.send(frames(sequence=9)); self.assertFalse(self.client.start_snapshot_valid())
        self.send(frames(sequence=10)); self.assertFalse(self.client.start_snapshot_valid())
        self.send(frames(sequence=11)); self.assertTrue(self.client.start_snapshot_valid())

    def test_sequence_and_uptime_wrap_are_supported(self):
        self.heartbeat(0xfffffff0)
        self.send(frames(body(captured=0xffffffe0),0xffffffff))
        self.assertTrue(self.client.start_snapshot_valid())
        self.clock+=.04; self.heartbeat(24)
        self.send(frames(body(captured=24),0))
        self.assertTrue(self.client.start_snapshot_valid())

    def test_capture_age_is_checked_on_publish_and_without_new_can(self):
        self.heartbeat(3001)
        self.send(frames(body(captured=1000)))
        self.assertFalse(self.client.start_snapshot_valid())
        self.send(frames(body(captured=1200),2)); self.assertTrue(self.client.start_snapshot_valid())
        for _ in range(3): self.clock+=.25; self.heartbeat(3001+round((self.clock-100)*1000))
        self.assertFalse(self.client.start_snapshot_valid())
        self.assertIn('stale',self.client.start_snapshot_error)

    def test_future_capture_and_time_rollback_do_not_look_fresh(self):
        self.send(frames(body(captured=1201)))
        self.assertFalse(self.client.start_snapshot_valid())
        self.send(frames(body(captured=1100),2)); self.assertTrue(self.client.start_snapshot_valid())
        self.clock-=.1; self.assertFalse(self.client.start_snapshot_valid())

    def test_offline_restart_and_close_clear_assembly_and_watermark(self):
        self.send(frames(sequence=100)); self.assertTrue(self.client.start_snapshot_valid())
        self.clock+=.5; self.client.tick(); self.assertIsNone(self.client.start_snapshot)
        self.heartbeat(0); self.send(frames(body(captured=0),1)); self.assertTrue(self.client.start_snapshot_valid())
        self.heartbeat(500); self.send(frames(body(captured=500),2)[:3])
        self.heartbeat(0); self.send(frames(body(captured=0),1)[3:]); self.assertIsNone(self.client.start_snapshot)
        self.client.close(); self.assertIsNone(self.client.start_snapshot)
        self.assertFalse(self.client.start_snapshot_valid())

    def test_nonclassic_or_wrong_dlc_never_enters_assembler(self):
        for attribute in ('is_extended_id','is_remote_frame','is_error_frame','is_fd'):
            msg=FakeMessage(START_BEGIN,frames()[0][1]); setattr(msg,attribute,True)
            self.client.ingest(msg)
        self.feed(START_BEGIN,b'\0'*7)
        self.assertEqual(self.client.start_bad_frames,5)
        self.assertIsNone(self.client.start_snapshot)

    def test_mixed_generation_and_all_channel_reason_codes(self):
        transfer=frames(); self.send(transfer[:1])
        self.feed(START_DATA,struct.pack('<HB',99,0)+b'\0'*5)
        self.send(transfer[1:]); self.assertIsNone(self.client.start_snapshot)
        payload=bytearray(body())
        for i in range(14): payload[32+i*8+1]=i
        self.send(frames(bytes(payload),2)); self.assertTrue(self.client.start_snapshot_valid())


if __name__=='__main__': unittest.main()
