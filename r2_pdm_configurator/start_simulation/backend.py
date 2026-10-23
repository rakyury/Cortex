"""Synchronous bounded bridge. GUI callers must use their dedicated worker.

There is intentionally no Python policy fallback if C compilation is unavailable.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading


class SimulationError(RuntimeError):
    pass

_STATES = {'OFF', 'QUEUED', 'STARTING', 'ON', 'FAULT'}
_REASONS = {'OK', 'BAD_CONFIG', 'BAD_CHANNEL', 'OLD_REQUEST', 'EXPIRED', 'UNHEALTHY',
            'UNKNOWN_OFF', 'SLOTS', 'TOTAL_BUDGET', 'GROUP_BUDGET', 'COOLDOWN',
            'DEADLINE', 'FAULT_LATCHED', 'CANCELLED'}

def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise SimulationError(f'{name} must be an integer from {low} to {high}')
    return value


def _firmware_path(explicit):
    selected = explicit or os.environ.get('PDM_FIRMWARE_ROOT')
    if selected:
        candidates = [Path(selected)]
    else:
        # Supports both workspace/Cortex and workspace/lanes/cortex-starts.
        candidates = [parent / 'SynapsePDM' for parent in Path(__file__).resolve().parents]
    for candidate in candidates:
        root = candidate.resolve()
        if all((root / 'pdm_cube/src' / name).is_file() for name in
               ('pdm_board_profile.c', 'pdm_board_profile.h', 'pdm_start_admission.c', 'pdm_start_admission.h')):
            return root
    raise SimulationError('Scheduler firmware sources unavailable. Set PDM_FIRMWARE_ROOT to the paired SynapsePDM checkout.')


class StartSimulation:
    def __init__(self, profile=24, slots=2, firmware_root=None, compiler=None,
                 total_budget_ma=300000, group_budget_ma=200000):
        _integer(profile, 14, 24, 'Profile')
        if profile not in (14, 24):
            raise SimulationError('Profile must be 14 or 24')
        _integer(slots, 2, 4, 'Slots')
        _integer(total_budget_ma, 20000, 1000000, 'Total fixture budget')
        _integer(group_budget_ma, 10000, 1000000, 'Group fixture budget')
        self.profile = profile
        self.slots = slots
        self._closed = False
        self._process = None
        self._reader = None
        self._stderr = None
        self._temp = None
        self._lines = queue.Queue(maxsize=2)
        self._lock = threading.Lock()
        self._last = None
        root = _firmware_path(firmware_root)
        cc = compiler or os.environ.get('CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc or not (Path(cc).is_file() or shutil.which(cc)):
            raise SimulationError('A host C compiler is unavailable. Install GCC/Clang or set CC to its executable.')
        self._temp = tempfile.TemporaryDirectory(prefix='pdm-start-')
        binary = Path(self._temp.name) / ('scheduler.exe' if os.name == 'nt' else 'scheduler')
        flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-g']
        if os.name == 'nt':
            flags.append('-D_CRT_SECURE_NO_WARNINGS')
        if os.environ.get('PDM_LINK_SANITIZE') == '1':
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        command = [str(cc), *flags, '-I' + str(root / 'pdm_cube/src'), str(Path(__file__).with_name('bridge.c')),
                   str(root / 'pdm_cube/src/pdm_board_profile.c'), str(root / 'pdm_cube/src/pdm_start_admission.c'),
                   '-o', str(binary)]
        self.firmware_root = str(root)
        self._environment = os.environ.copy()
        if os.name == 'nt':
            self._environment['PATH'] = str(Path(shutil.which(cc) or cc).resolve().parent) + os.pathsep + self._environment.get('PATH', '')
        self._environment.setdefault('ASAN_OPTIONS', 'detect_leaks=1:halt_on_error=1')
        self._environment.setdefault('UBSAN_OPTIONS', 'halt_on_error=1:print_stacktrace=1')
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=self._environment,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                raise SimulationError('Scheduler compilation failed: ' + (result.stderr or result.stdout)[-4000:])
            self._stderr = tempfile.TemporaryFile(mode='w+t')
            self._process = subprocess.Popen([str(binary), str(profile), str(slots), str(total_budget_ma), str(group_budget_ma)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, text=True, bufsize=1,
                env=self._environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self._reader = threading.Thread(target=self._read, daemon=True)
            self._reader.start()
            self._last = self._receive()
        except Exception as error:
            self._cleanup()
            if isinstance(error, SimulationError):
                raise
            raise SimulationError(f'Scheduler could not start: {error}') from error

    def _read(self):
        try:
            while True:
                line = self._process.stdout.readline(32769)
                if not line:
                    self._lines.put_nowait(None)
                    break
                if len(line) > 32768 or not line.endswith('\n'):
                    self._lines.put_nowait(None)
                    break
                self._lines.put_nowait(line)
        except (OSError, ValueError, queue.Full):
            pass

    def _receive(self):
        try:
            line = self._lines.get(timeout=2)
        except queue.Empty as error:
            raise SimulationError('Scheduler response timed out') from error
        if line is None:
            raise SimulationError('Scheduler process exited unexpectedly')
        try:
            data = json.loads(line)
            if (not isinstance(data, dict) or type(data['profile']) is not int or type(data['count']) is not int or
                data['profile'] != self.profile or data['count'] != self.profile or
                data['slots'] != self.slots or
                type(data['slots']) is not int or not isinstance(data['channels'], list) or
                len(data['channels']) != self.profile or data['physical_outputs_on'] != 0 or
                type(data['physical_outputs_on']) is not int or
                data['hardware_authorized'] is not False or data['simulation_only'] is not True or
                data['fixture_only'] is not True or data['output_backend'] != 'simulated_zero_output_backend'):
                raise ValueError('Invalid simulation boundary')
            for field in ('now_ms', 'starts_reserved', 'total_budget_ma', 'total_reserved_ma', 'total_measured_ma'):
                _integer(data[field], 0, 0xffffffff, field)
            if (data['starts_reserved'] > self.slots or not isinstance(data['last_command'], str) or
                len(data['last_command']) > 128 or data['last_result'] not in
                _REASONS | {'PARTIAL_OR_REJECTED', 'INVALID_STATE', 'REJECTED'}):
                raise ValueError('Invalid command result')
            if (not isinstance(data['queue'], list) or len(set(data['queue'])) != len(data['queue']) or
                any(type(item) is not int or not 1 <= item <= self.profile for item in data['queue'])):
                raise ValueError('Invalid queue')
            if not isinstance(data['groups'], list) or len(data['groups']) != 2:
                raise ValueError('Invalid groups')
            for index, group in enumerate(data['groups']):
                if not isinstance(group, dict) or type(group['id']) is not int or group['id'] != index:
                    raise ValueError('Invalid group ID')
                for field in ('budget_ma', 'reserved_ma'):
                    _integer(group[field], 0, 0xffffffff, field)
            for index, channel in enumerate(data['channels'], 1):
                if (not isinstance(channel, dict) or type(channel['channel']) is not int or
                    channel['channel'] != index or channel['state'] not in _STATES):
                    raise ValueError('Invalid channel state')
                for field in ('reserve_ma', 'measured_ma', 'continuous_ma', 'start_ma'):
                    _integer(channel[field], 0, 0xffffffff, field)
                if (channel['reason'] not in _REASONS or type(channel['group']) is not int or
                    channel['group'] not in (0, 1) or type(channel['slot_reserved']) is not bool):
                    raise ValueError('Invalid channel metadata')
                _integer(channel['priority'], 0, 3, 'Priority')
                position = channel['queue_position']
                if position is not None:
                    _integer(position, 1, len(data['queue']), 'Queue position')
                    if data['queue'][position - 1] != index or channel['state'] != 'QUEUED':
                        raise ValueError('Inconsistent queue position')
                elif index in data['queue'] or channel['state'] == 'QUEUED':
                    raise ValueError('Missing queue position')
        except (TypeError, ValueError, KeyError, SimulationError) as error:
            raise SimulationError('Malformed scheduler snapshot') from error
        return data

    def _command(self, text):
        with self._lock:
            if self._closed:
                raise SimulationError('Scheduler session is closed')
            try:
                self._process.stdin.write(text + '\n')
                self._process.stdin.flush()
                self._last = self._receive()
                return deepcopy(self._last)
            except Exception as error:
                self._cleanup()
                if isinstance(error, SimulationError):
                    raise
                raise SimulationError(f'Scheduler command failed: {error}') from error

    def _channel(self, command, channel):
        _integer(channel, 1, self.profile, 'Channel')
        return self._command(f'{command} {channel}')

    def snapshot(self): return self._command('SNAPSHOT')
    def request(self, channel): return self._channel('REQUEST', channel)
    def request_all(self): return self._command('REQUEST_ALL')
    def complete(self, channel): return self._channel('COMPLETE', channel)
    def fault(self, channel): return self._channel('FAULT', channel)
    def confirm_off(self, channel): return self._channel('CONFIRM_OFF', channel)
    def cancel(self, channel): return self._channel('CANCEL', channel)
    def clear_fault(self, channel): return self._channel('CLEAR_FAULT', channel)
    def reset(self): return self._command('RESET')
    def step(self, ms=10):
        _integer(ms, 1, 60000, 'Step milliseconds')
        return self._command(f'STEP {ms}')

    def _cleanup(self):
        self._closed = True
        process = self._process
        if process is not None:
            if process.stdin and not process.stdin.closed:
                try: process.stdin.close()
                except OSError: pass
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            if self._reader is not None: self._reader.join(timeout=1)
            if process.stdout: process.stdout.close()
        if self._stderr is not None: self._stderr.close()
        if self._temp is not None: self._temp.cleanup()

    def close(self):
        with self._lock:
            if not self._closed:
                self._cleanup()
                if self._process is not None and self._process.returncode:
                    raise SimulationError(f'Scheduler exited with status {self._process.returncode}')


create_session = StartSimulation
