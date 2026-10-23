"""Process-boundary tests. Scheduler policy is tested against actual C separately."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from r2_pdm_configurator.start_simulation import SimulationError, StartSimulation


class StartBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'pdm_cube/src'
        source.mkdir(parents=True)
        for name in ('pdm_board_profile.c', 'pdm_board_profile.h', 'pdm_start_admission.c', 'pdm_start_admission.h'):
            (source / name).write_text('', encoding='utf-8')
        self.real_popen = subprocess.Popen
        self.processes = []

    def session(self, mode='normal'):
        data = dict(profile=14, count=14, slots=2, now_ms=100, simulation_only=True, fixture_only=True,
            physical_outputs_on=0, hardware_authorized=False, output_backend='simulated_zero_output_backend',
            starts_reserved=0,total_budget_ma=300000,total_reserved_ma=20000,total_measured_ma=20000,
            queue=[],groups=[dict(id=i,budget_ma=200000,reserved_ma=10000) for i in range(2)],
            last_command='INIT',last_result='OK',channels=[dict(channel=i,
            state='OFF',reason='OK',group=0,priority=0,slot_reserved=False,queue_position=None,
            reserve_ma=0,measured_ma=0,continuous_ma=10000,start_ma=30000)
            for i in range(1,15)])
        script = self.root / 'fake.py'
        script.write_text('import json,sys,time\n' + f'data=json.loads({json.dumps(json.dumps(data))})\n' +
            'print(json.dumps(data),flush=True)\nfor line in sys.stdin:\n' +
            (' time.sleep(10)\n' if mode == 'timeout' else
             ' print("invalid",flush=True)\n' if mode == 'malformed' else
             ' sys.exit(7)\n' if mode == 'exit' else
             ' data["last_command"]=line.strip();print(json.dumps(data),flush=True)\n'), encoding='utf-8')
        def popen(*args, **kwargs):
            process = self.real_popen([sys.executable, str(script)], **kwargs)
            self.processes.append(process)
            return process
        with patch('r2_pdm_configurator.start_simulation.backend.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, '', '')), patch(
                   'r2_pdm_configurator.start_simulation.backend.subprocess.Popen', side_effect=popen):
            session = StartSimulation(profile=14, firmware_root=self.root, compiler=sys.executable)
        self.addCleanup(session.close)
        return session

    def test_commands_are_explicit_and_snapshot_is_not_aliased(self):
        session = self.session()
        self.assertEqual(session.request(3)['last_command'], 'REQUEST 3')
        for method, argument, expected in ((session.step,10,'STEP 10'), (session.cancel,2,'CANCEL 2'),
                (session.complete,2,'COMPLETE 2'), (session.fault,2,'FAULT 2'),
                (session.confirm_off,2,'CONFIRM_OFF 2'), (session.clear_fault,2,'CLEAR_FAULT 2')):
            self.assertEqual(method(argument)['last_command'], expected)
        self.assertEqual(session.request_all()['last_command'], 'REQUEST_ALL')
        self.assertEqual(session.reset()['last_command'], 'RESET')
        snapshot=session.snapshot(); snapshot['channels'].clear()
        self.assertEqual(len(session.snapshot()['channels']),14)

    def test_invalid_user_arguments_do_not_destroy_session(self):
        session=self.session()
        for value in (0,15,-1,True,1.5,'1'):
            with self.assertRaises(SimulationError): session.request(value)
        for value in (0,60001,False,'10'):
            with self.assertRaises(SimulationError): session.step(value)
        self.assertEqual(session.snapshot()['count'],14)

    def test_close_releases_process_and_directory(self):
        session=self.session(); directory=Path(session._temp.name)
        session.close(); session.close()
        self.assertIsNotNone(self.processes[0].poll())
        self.assertFalse(directory.exists())
        with self.assertRaises(SimulationError): session.snapshot()

    def test_malformed_reply_closes_session(self):
        session=self.session('malformed')
        with self.assertRaisesRegex(SimulationError,'Malformed'): session.snapshot()
        self.assertTrue(session._closed)
        self.assertIsNotNone(self.processes[0].poll())

    def test_valid_json_with_invalid_shapes_is_rejected_before_ui(self):
        mutations = [lambda data: data['channels'][0].pop('reserve_ma'),
                     lambda data: data['groups'][0].pop('budget_ma'),
                     lambda data: data['channels'][0].update(state='UNKNOWN'),
                     lambda data: data['channels'][0].update(reason='FUTURE_REASON'),
                     lambda data: data['channels'][0].update(group=True),
                     lambda data: data['channels'][0].update(measured_ma='100'),
                     lambda data: data['channels'][0].update(slot_reserved=1),
                     lambda data: data['groups'][1].update(id=0),
                     lambda data: data.update(queue=[1,1]),
                     lambda data: data['channels'][0].update(queue_position=1)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                session=self.session()
                data=session.snapshot(); mutate(data)
                session._lines.put_nowait(json.dumps(data))
                with self.assertRaisesRegex(SimulationError,'Malformed'): session.snapshot()
                self.assertTrue(session._closed)

    def test_exited_process_is_reported_and_reaped(self):
        session=self.session('exit')
        with self.assertRaisesRegex(SimulationError,'exited'): session.snapshot()
        self.assertEqual(self.processes[0].poll(),7)

    def test_timeout_is_bounded_and_kills_process(self):
        session=self.session('timeout')
        with self.assertRaisesRegex(SimulationError,'timed out'): session.snapshot()
        self.assertIsNotNone(self.processes[0].poll())

    def test_missing_sources_and_compiler_are_clear_errors(self):
        with self.assertRaisesRegex(SimulationError,'firmware sources'):
            StartSimulation(firmware_root=self.root/'missing')
        with self.assertRaisesRegex(SimulationError,'compiler'):
            StartSimulation(firmware_root=self.root,compiler=str(self.root/'missing-cc'))

    def test_compilation_failure_and_timeout_cleanup(self):
        for result in (subprocess.CompletedProcess([],1,'','fixture compiler error'),
                       subprocess.TimeoutExpired('cc',60)):
            options={'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
            with patch('r2_pdm_configurator.start_simulation.backend.subprocess.run',**options):
                with self.assertRaises(SimulationError):
                    StartSimulation(firmware_root=self.root,compiler=sys.executable)

    def test_invalid_fixture_settings_rejected_before_compile(self):
        for kwargs in ({'profile':15},{'slots':1},{'slots':True},{'total_budget_ma':19999},
                       {'group_budget_ma':9999},{'profile':24.0}):
            with self.assertRaises(SimulationError): StartSimulation(**kwargs)


if __name__=='__main__': unittest.main()
