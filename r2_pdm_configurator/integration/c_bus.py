"""A simulated CAN adapter backed by the actual firmware C state machine.

No USB device, physical bus, GPIO or flash is accessed. The subprocess runs the
same pdm_link.c and vendored Racing Dash VM as the ARM build. Fault injection
changes frames at the simulated transport boundary, not the firmware code.
"""
from collections import deque
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading

import can


def compile_simulator(destination):
    source = os.environ.get("PDM_FIRMWARE_ROOT")
    if not source:
        raise RuntimeError("Set PDM_FIRMWARE_ROOT to the paired SynapsePDM checkout")
    root = Path(source).resolve() / "pdm_cube"
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise RuntimeError("A host C compiler is required for the HAL simulator")
    command = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-g"]
    if os.name == "nt":
        command += ["-D_CRT_SECURE_NO_WARNINGS"]
    if os.environ.get("PDM_LINK_SANITIZE") == "1":
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-fno-sanitize-recover=all"]
    command += ["-I" + str(root / "src"), str(root / "tests/link_simulator.c"),
                str(root / "src/pdm_link.c"), str(root / "src/pdm_adc.c")]
    command += [str(p) for p in sorted((root / "src/r2_shared").rglob("*.c"))]
    command += ["-o", str(destination)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("C simulator compilation failed:\n" + result.stdout + result.stderr)


class CSimulatorBus:
    def __init__(self, binary):
        self.now_ms = 100
        self.received = deque()
        self.sent = []
        self.history = []
        self.inbound = lambda message: message
        self.outbound = lambda message: message
        self._lines = queue.Queue()
        self._closed = False
        self._shutdown_error = None
        self._stderr = tempfile.TemporaryFile(mode="w+t")
        self._process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=self._stderr,
                                         text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self.command("RESET")
        except Exception:
            try:
                self.shutdown()
            except RuntimeError:
                pass
            raise

    def _read(self):
        for line in self._process.stdout:
            self._lines.put(line.rstrip())
        self._lines.put(None)

    def command(self, line):
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()
        result = []
        while True:
            try:
                reply = self._lines.get(timeout=10)
            except queue.Empty as error:
                raise RuntimeError("Firmware simulator stopped responding to " + line) from error
            if reply is None:
                self._stderr.seek(0)
                raise RuntimeError("Firmware simulator exited: " + self._stderr.read())
            if reply == "END":
                return result
            parts = reply.split()
            if len(parts) != 2:
                raise RuntimeError("Unexpected simulator response: " + reply)
            message = can.Message(arbitration_id=int(parts[0], 16),
                                  data=bytes.fromhex(parts[1]), is_extended_id=False)
            self.history.append(message)
            result.append(message)
            message = self.inbound(message)
            if message is not None:
                self.received.append(message)

    def send(self, message, timeout=None):
        self.sent.append(message)
        message = self.outbound(message)
        if message is not None:
            if message.is_extended_id or message.is_remote_frame or len(message.data) != 8:
                raise ValueError("This simulated adapter expects standard 8-byte data frames")
            self.command(f"RX {self.now_ms} {message.arbitration_id:x} {bytes(message.data).hex()}")

    def recv(self, timeout=0):
        return self.received.popleft() if self.received else None

    def advance(self, milliseconds=10):
        self.now_ms = (self.now_ms + milliseconds) & 0xffffffff
        return self.command(f"TICK {self.now_ms}")

    def shutdown(self):
        if self._closed:
            if self._shutdown_error is not None:
                raise RuntimeError(self._shutdown_error)
            return
        if not self._process.stdin.closed:
            try:
                self._process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._reader.join(timeout=5)
        self._process.stdout.close()
        self._stderr.seek(0)
        diagnostic = self._stderr.read()
        self._stderr.close()
        self._closed = True
        if self._process.returncode:
            self._shutdown_error = "Firmware simulator failed: " + diagnostic
            raise RuntimeError(self._shutdown_error)
