"""Qt contract/lifecycle tests; actual C semantics have a separate integration suite."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest
from unittest.mock import patch
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from r2_pdm_configurator.app import MainWindow
from r2_pdm_configurator.ui.start_simulation import StartSimulationPanel


class FakeSession:
    def __init__(self, **kwargs):
        self.calls = []
        self.closed = False
        self.thread_id = threading.get_ident()
        count = kwargs['profile']
        self.value = dict(profile=count, count=count, slots=kwargs['slots'], now_ms=0,
            queue=[], starts_reserved=0, total_budget_ma=kwargs['total_budget_ma'], total_reserved_ma=0,
            groups=[dict(id=i, budget_ma=kwargs['group_budget_ma'], reserved_ma=0) for i in (0, 1)],
            channels=[dict(channel=i, state='OFF', reason='OK', reserve_ma=0, measured_ma=0,
                queue_position=None, group=i % 2, priority=0, continuous_ma=5000, start_ma=30000,
                slot_reserved=False) for i in range(1, count + 1)],
            simulation_only=True, fixture_only=True, physical_outputs_on=0,
            hardware_authorized=False, output_backend='simulated_zero_output_backend')

    def snapshot(self):
        # Each response is detached, like a parsed subprocess JSON result.
        import copy
        return copy.deepcopy(self.value)

    def close(self):
        self.closed = True

    def __getattr__(self, name):
        def action(*args):
            self.calls.append((name, args, threading.get_ident()))
            if name == 'request_all':
                for i, channel in enumerate(self.value['channels']):
                    channel['state'] = 'QUEUED'
                    channel['queue_position'] = i + 1
                    channel['reason'] = 'TOTAL_BUDGET' if i == 0 else 'OK'
                self.value['queue'] = list(range(1, self.value['count'] + 1))
            elif name == 'step':
                self.value['now_ms'] += args[0]
            elif name == 'fault':
                self.value['channels'][args[0] - 1].update(state='FAULT', reason='UNHEALTHY', reserve_ma=30000)
            return self.snapshot()
        return action


class StartSimulationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sessions = []
        self.widgets = []

    def factory(self, **kwargs):
        session = FakeSession(**kwargs)
        self.sessions.append(session)
        return session

    def wait(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(condition(), 'Qt operation did not finish before deadline')

    def panel(self, factory=None):
        panel = StartSimulationPanel(backend_factory=factory or self.factory)
        self.widgets.append(panel)
        return panel

    def launch(self, panel):
        panel.start()
        self.wait(lambda: panel._active)

    def tearDown(self):
        for widget in self.widgets:
            panel = widget.start_simulation if isinstance(widget, MainWindow) else widget
            panel.shutdown()
            self.wait(lambda: panel._thread is None)
            widget.close()

    def test_lazy_start_profiles_and_fixture_budgets_on_worker(self):
        panel = self.panel()
        self.assertEqual(self.sessions, [])
        self.assertEqual(panel.table.rowCount(), 0)
        panel.profile.setCurrentIndex(1)
        panel.slots.setCurrentIndex(2)
        panel.total_budget.setValue(120)
        self.launch(panel)
        self.assertEqual(panel.table.rowCount(), 24)
        self.assertEqual(panel.snapshot['slots'], 4)
        self.assertEqual(panel.snapshot['total_budget_ma'], 120000)
        self.assertIn('120.000 A', panel.summary.text())
        self.assertNotEqual(self.sessions[0].thread_id, threading.get_ident())
        self.assertFalse(panel.profile.isEnabled())
        self.assertIn('virtual states', panel.notice.text())

    def test_compilation_does_not_block_event_loop_or_close(self):
        release = threading.Event()
        entered = threading.Event()
        def delayed(**kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test startup timeout')
            return self.factory(**kwargs)
        panel = self.panel(delayed)
        panel.start()
        self.wait(entered.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait(lambda: bool(ticks))
        self.assertFalse(panel.shutdown())
        self.assertFalse(panel.start_btn.isEnabled())
        release.set()
        self.wait(lambda: panel._thread is None)
        self.assertTrue(self.sessions[0].closed)
        self.assertIsNone(panel.snapshot)

    def test_queue_reason_and_manual_actions_are_serialized(self):
        panel = self.panel()
        self.launch(panel)
        panel.perform('request_all')
        self.wait(lambda: not panel._busy)
        self.assertEqual(panel.table.item(0, 3).text(), 'total budget')
        self.assertEqual(panel.table.item(1, 3).text(), 'Waiting behind queue head')
        panel.table.selectRow(3)
        for operation in ('request', 'complete', 'fault', 'confirm_off', 'cancel', 'clear_fault', 'reset'):
            panel.perform(operation)
            self.wait(lambda: not panel._busy)
        panel.step_ms.setValue(110)
        panel.perform('step')
        self.wait(lambda: not panel._busy)
        calls = self.sessions[0].calls
        self.assertEqual(calls[1][:2], ('request', (4,)))
        self.assertEqual(calls[-1][:2], ('step', (110,)))
        self.assertTrue(all(call[2] == self.sessions[0].thread_id for call in calls))
        self.assertEqual(panel.table.item(3, 4).text(), '30.000')
        self.assertEqual(panel.table.item(3, 5).text(), '0.000')

    def test_missing_compiler_is_actionable_and_restart_available(self):
        def unavailable(**kwargs):
            raise RuntimeError('C compiler not found: set CC')
        panel = self.panel(unavailable)
        panel.start()
        self.wait(lambda: panel._thread is None)
        self.assertIn('C compiler not found', panel.message.text())
        self.assertIn('unavailable', panel.summary.text())
        self.assertTrue(panel.start_btn.isEnabled())
        self.assertFalse(panel.step_btn.isEnabled())
        self.assertEqual(panel.table.rowCount(), 0)

    def test_failed_command_removes_old_state_and_closes_session(self):
        panel = self.panel()
        self.launch(panel)
        def broken(*args):
            raise RuntimeError('Simulator reply timed out')
        self.sessions[0].step = broken
        panel.perform('step')
        self.wait(lambda: panel._thread is None)
        self.assertTrue(self.sessions[0].closed)
        self.assertEqual(panel.table.rowCount(), 0)
        self.assertIn('timed out', panel.message.text())

    def test_stop_restart_replaces_24_row_state_with_14(self):
        panel = self.panel()
        panel.profile.setCurrentIndex(1)
        self.launch(panel)
        panel.shutdown()
        self.wait(lambda: panel._thread is None)
        panel.profile.setCurrentIndex(0)
        self.launch(panel)
        self.assertEqual(panel.table.rowCount(), 14)
        self.assertEqual(len(self.sessions), 2)
        self.assertTrue(self.sessions[0].closed)

    def test_refusal_result_and_not_yet_dispatched_queue_are_truthful(self):
        panel = self.panel()
        self.launch(panel)
        session = self.sessions[0]
        session.value['channels'][0].update(state='QUEUED', reason='OK', queue_position=1)
        session.value.update(last_command='REQUEST', last_result='FAULT_LATCHED')
        panel.perform('request')
        self.wait(lambda: not panel._busy)
        self.assertEqual(panel.table.item(0, 3).text(), 'Awaiting manual dispatch step')
        self.assertIn('fault latched', panel.message.text())

    def test_mainwindow_close_waits_asynchronously_for_compilation_cleanup(self):
        release = threading.Event()
        entered = threading.Event()
        def delayed(**kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test startup timeout')
            return self.factory(**kwargs)
        window = MainWindow()
        self.widgets.append(window)
        window.start_simulation._factory = delayed
        window.start_simulation.start()
        self.wait(entered.is_set)
        window.close()
        self.assertTrue(window._simulation_close_pending)
        self.assertIsNotNone(window.start_simulation._thread)
        release.set()
        self.wait(lambda: window.start_simulation._thread is None)
        self.assertFalse(window._simulation_close_pending)
        self.assertTrue(self.sessions[0].closed)

    def test_mainwindow_keeps_simulator_separate_from_real_hal_and_legacy(self):
        window = MainWindow()
        self.widgets.append(window)
        window.start_simulation._factory = self.factory
        self.assertTrue(window.start_simulation_dock.isHidden())
        window.protocol.setCurrentIndex(1)
        with patch('r2_pdm_configurator.app.open_adapter') as adapter:
            window.start_simulation.profile.setCurrentIndex(1)
            self.launch(window.start_simulation)
            window.start_simulation.perform('request_all')
            self.wait(lambda: not window.start_simulation._busy)
            adapter.assert_not_called()
        self.assertEqual(window.model.rowCount(), 38)
        self.assertEqual(window.channel.maximum(), 14)
        self.assertIsNone(window.client)
        window.close()
        self.wait(lambda: window.start_simulation._thread is None)
        self.assertTrue(self.sessions[0].closed)


if __name__ == '__main__':
    unittest.main()
