"""Cortex worker API -> actual paired C scheduler; no physical adapters.

Missing firmware/compiler is a failure here. These are cross-repository tests,
not a Python reimplementation of admission or an electrical hardware emulator.
"""
import unittest
import time
from unittest.mock import patch

from r2_pdm_configurator.start_simulation import StartSimulation


class StartSimulationIntegrationTests(unittest.TestCase):
    def simulation(self, **kwargs):
        sim = StartSimulation(**kwargs)
        self.addCleanup(sim.close)
        return sim

    def safe(self, snapshot):
        self.assertTrue(snapshot['simulation_only'])
        self.assertTrue(snapshot['fixture_only'])
        self.assertFalse(snapshot['hardware_authorized'])
        self.assertEqual(snapshot['physical_outputs_on'], 0)
        self.assertEqual(snapshot['output_backend'], 'simulated_zero_output_backend')
        self.assertLessEqual(snapshot['starts_reserved'], snapshot['slots'])
        self.assertLessEqual(snapshot['total_reserved_ma'], snapshot['total_budget_ma'])
        self.assertEqual(len(snapshot['queue']), len(set(snapshot['queue'])))
        for group in snapshot['groups']:
            self.assertLessEqual(group['reserved_ma'], group['budget_ma'])
        self.assertEqual(sum(bool(c['slot_reserved']) for c in snapshot['channels']),
                         snapshot['starts_reserved'])
        return snapshot

    def test_both_profiles_all_slot_limits_dispatch_actual_c_fifo(self):
        for profile in (14, 24):
            for slots in (2, 3, 4):
                with self.subTest(profile=profile, slots=slots):
                    sim = self.simulation(profile=profile, slots=slots,
                                          total_budget_ma=1000000, group_budget_ma=1000000)
                    initial = self.safe(sim.snapshot())
                    self.assertEqual(initial['count'], profile)
                    self.assertEqual(len(initial['channels']), profile)
                    self.assertEqual(initial['queue'], [])
                    self.assertTrue(all(c['state'] == 'OFF' for c in initial['channels']))
                    self.safe(sim.request_all())
                    state = self.safe(sim.step(10))
                    self.assertEqual(state['starts_reserved'], slots)
                    self.assertEqual([c['channel'] for c in state['channels'] if c['state'] == 'STARTING'],
                                     list(range(1, slots + 1)))
                    self.assertEqual(state['queue'], list(range(slots + 1, profile + 1)))
                    self.assertEqual(state['channels'][slots]['reason'], 'SLOTS')
                    before = (state['queue'], state['total_reserved_ma'], state['starts_reserved'])
                    repeated = self.safe(sim.request(slots + 1))
                    self.assertEqual((repeated['queue'], repeated['total_reserved_ma'], repeated['starts_reserved']), before)
                    for _ in range(profile):
                        starting = [c['channel'] for c in state['channels'] if c['state'] == 'STARTING']
                        for channel in starting:
                            self.safe(sim.complete(channel))
                        state = self.safe(sim.step(10))
                        if all(c['state'] == 'ON' for c in state['channels']):
                            break
                    self.assertTrue(all(c['state'] == 'ON' for c in state['channels']))
                    self.assertEqual(state['queue'], [])
                    self.assertEqual(state['starts_reserved'], 0)
                    sim.close()

    def test_local_simulation_never_creates_can_adapter(self):
        with patch('can.Bus', side_effect=AssertionError('Simulation opened physical CAN')), \
             patch('can.interface.Bus', side_effect=AssertionError('Simulation opened physical CAN')):
            sim = self.simulation(profile=24, slots=4)
            self.safe(sim.request_all())
            self.safe(sim.step(10))
            self.safe(sim.fault(1))
            self.safe(sim.cancel(2))
            self.safe(sim.reset())
            self.safe(sim.confirm_off(1))
            self.safe(sim.step(10))
            sim.close()

    def test_total_budget_counts_existing_on_envelope(self):
        fixture = self.simulation(profile=24, slots=4)
        initial = fixture.snapshot()
        base = initial['total_reserved_ma']
        peak = initial['channels'][0]['start_ma']
        fixture.close()
        sim = self.simulation(profile=24, slots=4,
                              total_budget_ma=base + 4 * peak, group_budget_ma=1000000)
        self.safe(sim.request(1))
        self.safe(sim.step(10))
        self.safe(sim.complete(1))
        on = self.safe(sim.step(10))
        self.assertEqual(on['channels'][0]['state'], 'ON')
        self.assertEqual(on['channels'][0]['reserve_ma'], on['channels'][0]['continuous_ma'])
        for channel in range(2, 6):
            self.safe(sim.request(channel))
        state = self.safe(sim.step(10))
        self.assertEqual(state['starts_reserved'], 3)
        self.assertEqual(state['channels'][4]['state'], 'QUEUED')
        self.assertEqual(state['channels'][4]['reason'], 'TOTAL_BUDGET')
        self.assertEqual(state['total_reserved_ma'], base + 3 * peak + on['channels'][0]['continuous_ma'])

    def test_group_budget_blocks_fifo_without_bypassing_head(self):
        fixture = self.simulation(profile=24, slots=4)
        initial = fixture.snapshot()
        first = initial['channels'][0]
        same_group = next(c for c in initial['channels'][1:] if c['group'] == first['group'])
        other_group = next(c for c in initial['channels'][1:] if c['group'] != first['group'])
        group_base = next(g['reserved_ma'] for g in initial['groups'] if g['id'] == first['group'])
        fixture.close()
        sim = self.simulation(profile=24, slots=4, total_budget_ma=1000000,
                              group_budget_ma=group_base + first['start_ma'])
        for channel in (first['channel'], same_group['channel'], other_group['channel']):
            self.safe(sim.request(channel))
        state = self.safe(sim.step(10))
        self.assertEqual(state['starts_reserved'], 1)
        self.assertEqual(state['channels'][same_group['channel'] - 1]['reason'], 'GROUP_BUDGET')
        self.assertEqual(state['queue'], [same_group['channel'], other_group['channel']])

    def test_fault_reservation_requires_off_proof_and_explicit_clear(self):
        sim = self.simulation(profile=14, slots=2)
        self.safe(sim.request(1))
        active = self.safe(sim.step(10))
        peak = active['channels'][0]['reserve_ma']
        faulted = self.safe(sim.fault(1))
        faulted = self.safe(sim.step(10))
        self.assertEqual(faulted['channels'][0]['state'], 'FAULT')
        self.assertEqual(faulted['channels'][0]['reserve_ma'], peak)
        self.assertEqual(faulted['starts_reserved'], 1)
        self.safe(sim.confirm_off(1))
        stopped = self.safe(sim.step(10))
        self.assertEqual(stopped['channels'][0]['state'], 'FAULT')
        self.assertEqual(stopped['channels'][0]['reserve_ma'], 0)
        self.assertEqual(stopped['starts_reserved'], 0)
        self.safe(sim.clear_fault(1))
        cleared = self.safe(sim.step(10))
        self.assertEqual(cleared['channels'][0]['state'], 'OFF')
        self.assertEqual(cleared['queue'], [])

    def test_start_deadline_and_reset_do_not_replay_authorization(self):
        sim = self.simulation(profile=24, slots=2)
        self.safe(sim.request_all())
        self.safe(sim.step(10))
        timed_out = self.safe(sim.step(60000))
        self.assertEqual(timed_out['channels'][0]['state'], 'FAULT')
        self.assertEqual(timed_out['channels'][0]['reason'], 'DEADLINE')
        self.assertGreater(timed_out['channels'][0]['reserve_ma'], 0)
        self.assertEqual(timed_out['queue'], [])  # Waiting requests expired too.
        reset = self.safe(sim.reset())
        self.assertEqual(reset['queue'], [])
        self.assertGreater(reset['channels'][0]['reserve_ma'], 0)
        for channel in (1, 2):
            self.safe(sim.confirm_off(channel))
        stopped = self.safe(sim.step(10))
        self.assertEqual(stopped['starts_reserved'], 0)
        self.assertEqual(stopped['queue'], [])
        self.assertFalse(any(c['state'] in ('STARTING', 'ON') for c in stopped['channels']))

    def test_qt_worker_displays_actual_c_states_budgets_and_faults(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication
        from r2_pdm_configurator.ui.start_simulation import StartSimulationPanel

        application = QApplication.instance() or QApplication([])
        panel = StartSimulationPanel()

        def wait_for(predicate, timeout=70):
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                application.processEvents()
                QTest.qWait(10)
            self.assertTrue(predicate(), panel.message.text())

        def close_panel():
            panel.shutdown()
            wait_for(lambda: panel._thread is None)
            panel.close()

        self.addCleanup(close_panel)
        panel.profile.setCurrentIndex(1)  # Actual C profile24.
        panel.slots.setCurrentIndex(2)  # Four startup slots.
        panel.start()
        wait_for(lambda: panel._active or panel._thread is None)
        self.assertTrue(panel._active, panel.message.text())
        self.assertEqual(panel.table.rowCount(), 24)
        self.safe(panel.snapshot)

        def action(name):
            panel.perform(name)
            wait_for(lambda: not panel._busy)
            self.assertTrue(panel._active, panel.message.text())
            self.safe(panel.snapshot)

        action('request_all')
        action('step')
        self.assertEqual(panel.snapshot['starts_reserved'], 4)
        self.assertEqual(panel.table.item(0, 2).text(), 'STARTING')
        self.assertEqual(panel.table.item(4, 2).text(), 'QUEUED')
        self.assertIn('slots', panel.table.item(4, 3).text())
        self.assertIn('140.000', panel.summary.text())  # 20A baseline +4x30A.
        panel.table.selectRow(0)
        action('complete')
        self.assertEqual(panel.table.item(0, 2).text(), 'ON')
        self.assertEqual(panel.table.item(0, 4).text(), '10.000')
        panel.table.selectRow(1)
        action('fault')
        self.assertEqual(panel.table.item(1, 2).text(), 'FAULT')
        self.assertEqual(panel.table.item(1, 4).text(), '30.000')
        self.assertIn('OFF', panel.message.text())
        action('confirm_off')
        self.assertEqual(panel.table.item(1, 4).text(), '0.000')
        action('clear_fault')
        self.assertEqual(panel.table.item(1, 2).text(), 'OFF')
        close_panel()
        self.assertIsNone(panel.snapshot)
        self.assertEqual(panel.table.rowCount(), 0)


if __name__ == '__main__':
    unittest.main()
