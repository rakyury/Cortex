"""Exercise the real python-can virtual adapter; no physical adapter is opened."""
import unittest
import uuid

import can

from r2_pdm_configurator.pdm_can import PdmCanClient, open_adapter


class VirtualBusIntegrationTests(unittest.TestCase):
    def setUp(self):
        channel = "pdm-qa-" + uuid.uuid4().hex
        self.bus = open_adapter("virtual", channel)
        self.device = can.Bus(interface="virtual", channel=channel)
        self.clock = 100.0
        self.client = PdmCanClient(self.bus, now=lambda: self.clock)

    def tearDown(self):
        self.client.close()
        self.device.shutdown()

    def send(self, cid, data, **kwargs):
        self.device.send(can.Message(arbitration_id=cid, is_extended_id=False,
                                     data=data, **kwargs))

    def test_connect_and_offline_tick_do_not_transmit(self):
        self.client.tick()
        self.assertIsNone(self.device.recv(0))
        self.assertFalse(self.client.armed)

    def test_fake_device_services_all_inputs_without_config_or_output_writes(self):
        requests = []
        for _ in range(180):
            self.send(0x720, bytes([1, 100, 30, 135, 0, 25, 0, 0]))
            self.client.tick()
            while (request := self.device.recv(0)) is not None:
                requests.append(request)
                if request.arbitration_id == 0x700:
                    # The legacy response has no channel identity. These tests
                    # prove codec/transport behavior, not reliable attribution.
                    for part in range(4):
                        self.send(0x701, bytes([part]) + bytes(7))
                elif request.arbitration_id == 0x712:
                    for part in range(2):
                        self.send(0x713, bytes([part]) + bytes(7))
                elif request.arbitration_id == 0x710:
                    self.send(0x711, bytes([0xA5]) + bytes(7))
            self.clock += 0.025
        self.client.tick()
        self.assertEqual(set(self.client.channels), set(range(1, 15)))
        self.assertEqual(set(self.client.analogues), set(range(1, 9)))
        self.assertEqual(self.client.digital_mask, 0xA5)
        self.assertEqual({r.arbitration_id for r in requests}, {0x700, 0x710, 0x712})
        self.assertTrue(all(not r.is_extended_id and r.dlc == 8 for r in requests))
        self.clock += 1.1
        self.client.tick()
        self.assertFalse(self.client.online())
        self.assertFalse(self.client.armed)

    def test_malformed_traffic_and_receive_budget_are_bounded(self):
        for _ in range(20):
            self.send(0x720, bytes(7))
        self.client.tick(receive_budget=5)
        self.assertEqual(self.client.bad_frames, 5)
        self.assertFalse(self.client.online())
        self.assertIsNone(self.device.recv(0))
        self.client.tick(receive_budget=15)
        self.assertEqual(self.client.bad_frames, 20)


if __name__ == "__main__":
    unittest.main()
