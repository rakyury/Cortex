"""Computed GUI telemetry must use the real VM and never mask bad inputs."""
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal
import json
import struct
import unittest
from unittest.mock import patch

from r2_pdm_configurator.models.computed import (
    ComputedChannels, Definition, SOURCES, OPERATIONS, MAX_JSON_BYTES, SCHEMA, VERSION,
)
from r2_pdm_configurator.models.telemetry import ROWS, TelemetryRow
from r2_pdm_configurator.vm import preview


def sample(channel_id="SYS:Voltage", numeric=12.345, quality="live", age="0.1 s", unit=None):
    if unit is None:
        unit = ("V" if channel_id == "SYS:Voltage" else "A" if channel_id == "SYS:Current" or
                channel_id.startswith("OUT:") else "°C" if channel_id == "SYS:Temperature" else
                "unit 1" if channel_id.startswith("AI:") else "")
    return TelemetryRow(channel_id, channel_id, str(numeric), unit, "Live", age,
                        "source diagnostic", numeric, quality)


def envelope(definitions):
    return {"schema": SCHEMA, "version": VERSION,
            "definitions": [asdict(item) for item in definitions]}


class DefinitionTests(unittest.TestCase):
    def test_sources_cover_all_35_rows_and_are_frozen(self):
        self.assertEqual(len(SOURCES), 35)
        self.assertEqual(tuple(item.channel_id for item in SOURCES), tuple(row[0] for row in ROWS))
        self.assertEqual(len({item.vm_id for item in SOURCES}), 35)
        self.assertEqual({key for key, _ in OPERATIONS},
                         {"identity", "add", "sub", "min", "max", "gt", "ge", "lt", "le", "eq", "ne"})
        for source in SOURCES:
            self.assertIn(f"×{source.scale}", source.label)
            with self.assertRaises(FrozenInstanceError):
                source.scale = 1

    def test_collection_limit_replace_remove_and_immutability(self):
        model = ComputedChannels()
        for i in range(16):
            model.add(Definition(f"channel {i}", "SYS:Voltage"))
        before = model.definitions
        with self.assertRaises(ValueError):
            model.add(Definition("17", "SYS:Voltage"))
        self.assertEqual(model.definitions, before)
        self.assertIsInstance(model.definitions, tuple)
        with self.assertRaises(AttributeError):
            model.definitions = ()
        with self.assertRaises(FrozenInstanceError):
            model.definitions[0].name = "edited"
        model.replace(0, Definition("changed", "SYS:Current"))
        self.assertEqual(model.definitions[0].name, "changed")
        before = model.definitions
        with self.assertRaises(ValueError):
            model.replace(1, Definition("CHANGED", "SYS:Current"))
        self.assertEqual(model.definitions, before)
        for index in (-1, 16, True, 0.0, "0"):
            for operation in (model.remove, lambda idx: model.replace(idx, Definition("x", "SYS:Voltage"))):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    operation(index)
        model.remove(0)
        self.assertEqual(len(model.definitions), 15)
        model.add(Definition("new", "SYS:Current"))
        self.assertEqual(len(model.definitions), 16)

    def test_definition_validation_is_atomic(self):
        original = Definition("valid", "SYS:Voltage")
        model = ComputedChannels([original])
        invalid = [replace(original, name=value) for value in ("", "x" * 49, " leading", "trailing ", "bad\nname", 123)]
        invalid += [replace(original, constant=value) for value in (True, 0.1, float("nan"), 1 << 31, -(1 << 31) - 1)]
        invalid += [replace(original, source_id="VM:valid"), replace(original, source_id="OUT:15"),
                    replace(original, operation="store"), replace(original, operation=[]),
                    replace(original, other_source_id="OUT:01"),
                    replace(original, operation="add", other_source_id="bad"),
                    replace(original, operation="add", other_source_id=100),
                    replace(original, name="VALID"), object()]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                model.add(item)
            self.assertEqual(model.definitions, (original,))

    def test_json_roundtrip_preserves_supported_definitions(self):
        items = [Definition("Напруга", "SYS:Voltage"),
                 Definition("Current", "SYS:Current", "add", "OUT:01", -4),
                 Definition("Alarm", "SYS:Temperature", "ge", None, 100)]
        model = ComputedChannels(items)
        parsed = json.loads(model.dumps())
        self.assertEqual(parsed, envelope(items))
        target = ComputedChannels()
        target.loads(model.dumps())
        self.assertEqual(target.definitions, tuple(items))
        target.loads(json.dumps(envelope([])))
        self.assertEqual(target.definitions, ())

    def test_json_schema_and_atomic_rejection(self):
        original = Definition("retained", "SYS:Voltage")
        model = ComputedChannels([original])
        valid = envelope([Definition("replacement", "SYS:Current")])
        cases = [[], None, {**valid, "extra": 1}, {**valid, "version": True},
                 {**valid, "version": 2}, {**valid, "schema": "other"},
                 {**valid, "definitions": {}}, {**valid, "definitions": [None]},
                 {**valid, "definitions": [asdict(original)] * 17},
                 {**valid, "definitions": [asdict(original), asdict(original)]},
                 {**valid, "definitions": [{**asdict(original), "output": 1}]},
                 {**valid, "definitions": [{"name": "x", "source_id": "SYS:Voltage"}]},
                 {**valid, "definitions": [asdict(original), {**asdict(original), "name": "bad", "constant": False}]}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                model.loads(json.dumps(item))
            self.assertEqual(model.definitions, (original,))
        bad_text = ["not json", "{" + '"schema":"x","schema":"y"' + "}",
                    model.dumps().replace('"constant": 0', '"constant": NaN'),
                    model.dumps().replace('"constant": 0', '"constant": Infinity'),
                    "[" * 2000 + "]" * 2000, "\ud800", b"{}"]
        for text in bad_text:
            with self.subTest(text=str(text)[:50]), self.assertRaises(ValueError):
                model.loads(text)
            self.assertEqual(model.definitions, (original,))

    def test_json_size_limit_is_utf8_bytes(self):
        model = ComputedChannels([Definition("é" * 48, "SYS:Voltage")])
        text = model.dumps()
        exact = text + " " * (MAX_JSON_BYTES - len(text.encode("utf-8")))
        model.loads(exact)
        before = model.definitions
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            model.loads(exact + " ")
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            model.loads(text + " " * (MAX_JSON_BYTES - len(text)))
        self.assertEqual(model.definitions, before)


class EvaluationTests(unittest.TestCase):
    def evaluate_one(self, definition, rows):
        return ComputedChannels([definition]).evaluate(rows)[0]

    def test_actual_vm_bytecode_and_scaled_constant(self):
        definition = Definition("Voltage offset", "SYS:Voltage", "add", constant=250)
        with patch("r2_pdm_configurator.models.computed.vm_preview.execute", wraps=preview.execute) as execute:
            result = self.evaluate_one(definition, [sample()])
        execute.assert_called_once()
        code, snapshot = execute.call_args.args
        vm_id = SOURCES[0].vm_id
        self.assertEqual(code, b"\x02" + struct.pack("<H", vm_id) + b"\x01" + struct.pack("<i", 250) + b"\x10\xff")
        self.assertEqual(snapshot.get(vm_id), 12345)
        self.assertEqual((result.numeric, result.unit, result.quality), (12595, "mV", "live"))
        self.assertEqual(result.channel_id, "VM:Voltage offset")
        self.assertEqual(result.status, "Local preview · diagnostic")

    def test_actual_vm_secondary_channel_and_max_age(self):
        rows = [sample("SYS:Current", 1.25, age="0.2 s"), sample("OUT:01", 2.5, age="1.4 s")]
        with patch("r2_pdm_configurator.models.computed.vm_preview.execute", wraps=preview.execute) as execute:
            result = self.evaluate_one(Definition("sum", "SYS:Current", "add", "OUT:01"), rows)
        code, _ = execute.call_args.args
        sources = {item.channel_id: item for item in SOURCES}
        self.assertEqual(code, b"\x02" + struct.pack("<H", sources["SYS:Current"].vm_id) +
                         b"\x02" + struct.pack("<H", sources["OUT:01"].vm_id) + b"\x10\xff")
        self.assertEqual((result.numeric, result.unit, result.age), (3750, "mA", "1.4 s"))

    def test_operations_use_correct_vm_operand_order(self):
        expected = {"identity": 7, "add": 10, "sub": 4, "min": 3, "max": 7,
                    "gt": 1, "ge": 1, "lt": 0, "le": 0, "eq": 0, "ne": 1}
        for operation, value in expected.items():
            with self.subTest(operation=operation):
                result = self.evaluate_one(Definition("test", "SYS:Alive", operation, constant=3),
                                           [sample("SYS:Alive", 7)])
                self.assertEqual(result.numeric, value)
                self.assertEqual(result.quality, "live")
                self.assertEqual(result.unit, "bool" if operation in {"gt", "ge", "lt", "le", "eq", "ne"} else "count")

    def test_decimal_half_up_positive_negative_and_int32_boundaries(self):
        definition = Definition("voltage", "SYS:Voltage")
        for raw, expected in [(12.3455, 12346), (-12.3455, -12346),
                              (Decimal("0.0005"), 1), (Decimal("-0.0005"), -1),
                              (Decimal("2147483.647"), 2147483647),
                              (Decimal("-2147483.648"), -2147483648)]:
            with self.subTest(raw=raw):
                self.assertEqual(self.evaluate_one(definition, [sample(numeric=raw)]).numeric, expected)
        for raw, expected in [(0.5, 1), (-0.5, -1)]:
            self.assertEqual(self.evaluate_one(Definition("temp", "SYS:Temperature"),
                                              [sample("SYS:Temperature", raw)]).numeric, expected)

    def test_invalid_numeric_and_conversion_overflow_never_reach_vm(self):
        for value in (None, True, "12", float("nan"), float("inf"), -float("inf"),
                      Decimal("NaN"), Decimal("1e1000000"), Decimal("2147483.6475"),
                      Decimal("-2147483.6485"), 2147484, -2147484):
            with self.subTest(value=value), patch("r2_pdm_configurator.models.computed.vm_preview.execute") as execute:
                result = self.evaluate_one(Definition("test", "SYS:Voltage"), [sample(numeric=value)])
                self.assertEqual(result.quality, "invalid")
                self.assertIsNone(result.numeric)
                execute.assert_not_called()

    def test_arithmetic_overflow_is_invalid_in_gui_domain(self):
        cases = [(2147483647, "add", 1), (-2147483648, "add", -1),
                 (2147483647, "sub", -1), (-2147483648, "sub", 1)]
        for lhs, operation, rhs in cases:
            with self.subTest(lhs=lhs, operation=operation), patch("r2_pdm_configurator.models.computed.vm_preview.execute") as execute:
                result = self.evaluate_one(Definition("test", "SYS:Alive", operation, constant=rhs),
                                           [sample("SYS:Alive", lhs)])
                self.assertEqual(result.quality, "invalid")
                self.assertIsNone(result.numeric)
                self.assertIn("overflows int32", result.detail)
                execute.assert_not_called()

    def test_missing_nonlive_and_unknown_quality_never_reach_vm(self):
        definition = Definition("test", "SYS:Current", "add", "OUT:01")
        for quality in ("stale", "invalid", "degraded", "fault", "unavailable", "unknown"):
            rows = [sample("SYS:Current", 1), sample("OUT:01", 2, quality)]
            with self.subTest(quality=quality), patch("r2_pdm_configurator.models.computed.vm_preview.execute") as execute:
                result = self.evaluate_one(definition, rows)
                self.assertEqual(result.quality, quality if quality != "unknown" else "invalid")
                self.assertIsNone(result.numeric)
                self.assertEqual(result.value, "—")
                execute.assert_not_called()
        with patch("r2_pdm_configurator.models.computed.vm_preview.execute") as execute:
            result = self.evaluate_one(definition, [sample("SYS:Current", 1)])
            self.assertEqual(result.quality, "unavailable")
            self.assertIn("missing", result.detail)
            execute.assert_not_called()

    def test_incompatible_units_and_dynamic_analogue_units(self):
        for operation in ("add", "eq", "min"):
            with self.subTest(operation=operation), patch("r2_pdm_configurator.models.computed.vm_preview.execute") as execute:
                result = self.evaluate_one(Definition("test", "SYS:Voltage", operation, "SYS:Current"),
                                           [sample(), sample("SYS:Current", 1)])
                self.assertEqual(result.quality, "invalid")
                self.assertIn("Incompatible", result.detail)
                execute.assert_not_called()
        definition = Definition("analogues", "AI:01", "sub", "AI:02")
        rows = [sample("AI:01", 3.5, unit="unit 7"), sample("AI:02", 1.25, unit="unit 7")]
        result = self.evaluate_one(definition, rows)
        self.assertEqual((result.numeric, result.unit), (2250, "milli(unit 7)"))
        result = self.evaluate_one(definition, [rows[0], replace(rows[1], unit="unit 8")])
        self.assertEqual(result.quality, "invalid")
        for unit in ("", " ", "bad\nunit", "x" * 49):
            result = self.evaluate_one(Definition("analogue", "AI:01"), [replace(rows[0], unit=unit)])
            self.assertEqual(result.quality, "invalid")

    def test_duplicate_rows_and_bad_ages_are_invalid(self):
        definition = Definition("test", "SYS:Voltage")
        result = self.evaluate_one(definition, [sample(), sample(numeric=999)])
        self.assertEqual(result.quality, "invalid")
        for age in ("—", "nan s", "-1 s", "inf s", "", "1e3 s"):
            with self.subTest(age=age):
                result = self.evaluate_one(definition, [sample(age=age)])
                self.assertEqual(result.quality, "invalid")
        result = self.evaluate_one(definition, [sample(age="—", quality="unavailable")])
        self.assertEqual(result.quality, "unavailable")
        # Quality is authoritative: rounded display age may equal its cutoff.
        self.assertEqual(self.evaluate_one(definition, [sample(age="1.0 s")]).quality, "live")

    def test_evaluation_does_not_mutate_sources_or_expose_device_writes(self):
        rows = [sample("OUT:01", 1.5), sample("OUT:02", 2.5)]
        before = tuple(rows)
        definition = Definition("sum", "OUT:01", "add", "OUT:02")
        model = ComputedChannels([definition])
        first, second = model.evaluate(rows), model.evaluate(rows)
        self.assertEqual(first, second)
        self.assertEqual(tuple(rows), before)
        self.assertEqual(model.definitions, (definition,))
        self.assertEqual(first[0].numeric, 4000)
        self.assertEqual(model.evaluate([])[0].quality, "unavailable")

    def test_vm_rejection_becomes_invalid_diagnostic(self):
        with patch("r2_pdm_configurator.models.computed.vm_preview.execute", side_effect=ValueError("invalid code")):
            result = self.evaluate_one(Definition("test", "SYS:Voltage"), [sample()])
        self.assertEqual(result.quality, "invalid")
        self.assertIsNone(result.numeric)
        self.assertIn("VM rejected", result.detail)


if __name__ == "__main__":
    unittest.main()
