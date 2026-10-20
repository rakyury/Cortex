import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

from r2_pdm_configurator.channel_engine import Snapshot
from r2_pdm_configurator.channel_engine.nodes import (
    MovingAverage, ExponentialAverage, OnDelay, HysteresisAlarm, LinearTable,
)
from r2_pdm_configurator.vm import PreviewError, execute, validate
from r2_pdm_configurator.vm.preview import _BINARY, _UNARY


def const(value):
    return b"\x01" + struct.pack("<i", value)


def program(*values, op):
    return b"".join(const(v) for v in values) + bytes([op, 255])


class ValidationTests(unittest.TestCase):
    def test_malformed_and_write_bytecode_rejected(self):
        bad = [b"", b"\x01\xff", b"\x10\xff", const(1), const(1) + b"\xff\x00",
               const(1) + b"\x90\xc8\x00\xff", const(1) * 2 + b"\x73\xc8\x00\xff",
               const(1) + b"\x70\x00\x00\xff", b"\x61", const(0) + b"\x61\x02\x01\xff",
               b"\xff", const(1) * 49 + b"\xff", b"\x00" * 256 + const(1) + b"\xff",
               b"\x00" * 513, const(1) + b"\xfe\xff"]
        for code in bad:
            with self.subTest(code=code[:15]), self.assertRaises(PreviewError):
                execute(code)

    def test_read_only_snapshot_and_invalid_values(self):
        original = {100: 37, 200: 0, 230: 1}
        snapshot = Snapshot(original)
        original[100] = 99
        self.assertEqual(execute(b"\x02\x64\x00\xff", snapshot), 37)
        self.assertEqual(snapshot.values[200], 0)
        with self.assertRaises(TypeError):
            snapshot.values[200] = 1
        with self.assertRaises(AttributeError):
            snapshot.values = {100: float("nan")}
        with self.assertRaises(AttributeError):
            del snapshot.values
        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertEqual(execute(b"\x02\x64\x00\xff", snapshot), 37)
        for value in (float("nan"), float("inf"), 1.5, True, 1 << 31, -(1 << 31) - 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Snapshot({100: value})
        for key in (-1, 65536, 1.2, True, 0xA000):
            with self.assertRaises(ValueError):
                Snapshot({key: 0})
        with self.assertRaises(ValueError):
            Snapshot({i + 100: 0 for i in range(129)})
        with self.assertRaises(ValueError):
            execute(b"\x02\x00\xa0\xff", snapshot)
        with self.assertRaises(PreviewError):
            execute(program(0, 100, 1001, op=0x54))

    def test_seeded_malformed_fuzz_is_bounded(self):
        randomizer = random.Random(446)
        for _ in range(2000):
            code = bytes(randomizer.randrange(256) for _ in range(randomizer.randrange(1, 100)))
            try:
                result = execute(code)
            except ValueError:
                continue
            self.assertTrue(-(1 << 31) <= result < 1 << 31)

    def test_node_bounds(self):
        cases = [lambda: MovingAverage(0), lambda: MovingAverage(17), lambda: ExponentialAverage(256),
                 lambda: OnDelay(-1), lambda: OnDelay(1 << 31), lambda: HysteresisAlarm(10, 0),
                 lambda: LinearTable([1, 1], [2, 3]), lambda: LinearTable([], []),
                 lambda: LinearTable([1, 2], [3])]
        for create in cases:
            with self.assertRaises(ValueError):
                create()


class CParityTests(unittest.TestCase):
    """Compile pinned device C and compare actual outputs, never a second mock."""
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        reference = root / "vm" / "reference"
        manifest = json.loads((reference / "manifest.json").read_text())
        for relative, expected in manifest["sha256_lf"].items():
            actual = hashlib.sha256((reference / relative).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            if actual != expected:
                raise AssertionError(f"pinned C reference changed: {relative}")
        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not compiler:
            raise RuntimeError("C compiler required for real firmware parity tests (CC/cc/clang/gcc)")
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / ("vm_reference.exe" if os.name == "nt" else "vm_reference")
        cmd = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror"]
        if os.name == "nt":
            # Keep the embedded ISO C source byte-identical. Windows otherwise
            # deprecates standard bounded strncpy/sscanf in favor of MS APIs.
            cmd += ["-D_CRT_SECURE_NO_WARNINGS"]
        if os.environ.get("PDM_VM_SANITIZE") == "1":
            cmd += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        cmd += [str(Path(__file__).with_suffix(".c").with_name("test_vm_reference.c"))]
        cmd += [str(path) for path in sorted(reference.rglob("*.c"))]
        cmd += ["-o", str(cls.binary)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        except subprocess.CalledProcessError as error:
            cls.temp.cleanup()
            raise RuntimeError(error.stdout + error.stderr) from error

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def reference(self, lines):
        result = subprocess.run([str(self.binary)], input="\n".join(lines) + "\n",
                                capture_output=True, text=True, check=True, timeout=15)
        return result.stdout.splitlines()

    def test_all_read_only_opcodes_against_device_c(self):
        edges = [-2147483648, -2147483647, -1000, -7, -1, 0, 1, 7, 31, 32, 1000, 2147483647]
        vectors = [program(a, b, op=op) for op in sorted(_BINARY) for a in edges for b in edges]
        vectors += [program(a, op=op) for op in sorted(_UNARY) for a in edges]
        for a in edges:
            vectors += [program(a, -10, 10, op=0x52), program(a, 40, -50, op=0x60),
                        program(a, -2147483648, 2147483647, -2147483648, 2147483647, op=0x53),
                        program(a, 0, 0, -20, 20, op=0x53),
                        program(a, 2147483647, 500, op=0x54)]
        vectors += [b"\x02\x64\x00\xff", b"\x02\x01\x00\xff", b"\x02\x15\x00\xff",
                    b"\x02\xff\xff\xff", const(0) + b"\x72\x64\x00\xff",
                    const(-1) + b"\x72\x64\x00\xff", const(65536) + b"\x72\x64\x00\xff",
                    const(3) + b"\x04\x10\xff", const(3) + const(5) + b"\x05\x11\xff",
                    const(1) + const(2) + const(3) + b"\x06\x03\x11\xff",
                    const(3) + b"\x00\xff", const(3) + b"\x61\x00\xff"]
        for index in (-1, 0, 1, 2, 999):
            vectors.append(const(index) + b"\x61\x02" + struct.pack("<ii", 123, -456) + b"\xff")
        randomizer = random.Random(2026)
        for _ in range(500):
            vectors.append(program(randomizer.randint(-2147483648, 2147483647),
                                   randomizer.randint(-2147483648, 2147483647),
                                   op=randomizer.choice(sorted(_BINARY))))
        expected = [int(value) for value in self.reference([code.hex() for code in vectors])]
        self.assertEqual(len(expected), len(vectors))
        snapshot = Snapshot({100: 37})
        for code, value in zip(vectors, expected):
            with self.subTest(code=code.hex()):
                self.assertEqual(execute(code, snapshot), value)

    def test_stateful_golden_sequence_against_device_c(self):
        sma, ema, alarm, timer = MovingAverage(3), ExponentialAverage(128), HysteresisAlarm(-10, 10), OnDelay(100)
        table = LinearTable([-2147483648, 0, 2147483647], [-2147483648, 100, 2147483647])
        values = [-20, -9, 10, 100, 0, -10, -2147483648, 2147483647]
        ticks = [0xFFFFFFFF - 50, 0xFFFFFFFF - 1, 49, 50, 51, 60, 160, 161]
        expected = [tuple(map(int, line.split(","))) for line in self.reference(["nodes"])]
        actual = [(sma.update(v), ema.update(v), alarm.update(v), timer.update(v, t), table.lookup(v))
                  for v, t in zip(values, ticks)]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
