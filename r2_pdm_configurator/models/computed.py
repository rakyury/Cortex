"""Bounded local telemetry expressions, evaluated by the real read-only R2 VM.

Values enter the VM as int32 integers in the Source's displayed unit. Decimal
conversion uses str(value), then scale and ROUND_HALF_UP (ties away from zero).
The GUI accepts a narrower domain than the device VM: source conversion and
ADD/SUB overflow are invalid, rather than showing a wrapped telemetry value.
Freshness comes from the base TelemetryRow quality; its rounded age is only
used for display and is never reinterpreted as a second freshness clock.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
import json
import math
import re
import struct
from types import MappingProxyType

from .telemetry import ROWS, TelemetryRow
from ..channel_engine import Snapshot
from ..vm import preview as vm_preview

MAX_DEFINITIONS = 16
MAX_NAME_LENGTH = 48
MAX_JSON_BYTES = 32 * 1024
SCHEMA = "r2-pdm-computed"
VERSION = 1
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


@dataclass(frozen=True, slots=True)
class Source:
    channel_id: str
    label: str
    vm_id: int
    scale: int
    unit: str


def _source(row, index):
    cid, label, kind, _ = row
    if cid == "SYS:Voltage":
        scale, unit = 1000, "mV"
    elif cid == "SYS:Current" or kind == "output":
        scale, unit = 1000, "mA"
    elif cid == "SYS:Temperature":
        scale, unit = 1, "°C"
    elif cid == "SYS:Errors":
        scale, unit = 1, "flags"
    elif cid == "SYS:Alive":
        scale, unit = 1, "count"
    elif kind == "digital":
        scale, unit = 1, "bool"
    else:
        scale, unit = 1000, "milli(analogue unit)"
    return Source(cid, f"{label} · {unit} (×{scale})", 100 + index, scale, unit)


SOURCES = tuple(_source(row, index) for index, row in enumerate(ROWS))
OPERATIONS = (
    ("identity", "Source value"), ("add", "Add"), ("sub", "Subtract"),
    ("min", "Minimum"), ("max", "Maximum"), ("gt", "Greater than"),
    ("ge", "Greater than or equal"), ("lt", "Less than"),
    ("le", "Less than or equal"), ("eq", "Equal"), ("ne", "Not equal"),
)
_SOURCES = MappingProxyType({source.channel_id: source for source in SOURCES})
_OPCODES = MappingProxyType({
    "add": 0x10, "sub": 0x11, "min": 0x50, "max": 0x51,
    "gt": 0x34, "ge": 0x35, "lt": 0x32, "le": 0x33, "eq": 0x30, "ne": 0x31,
})
_COMPARISONS = frozenset(("gt", "ge", "lt", "le", "eq", "ne"))
_QUALITY_PRIORITY = {"invalid": 0, "fault": 1, "unavailable": 2, "stale": 3, "degraded": 4}


@dataclass(frozen=True, slots=True)
class Definition:
    name: str
    source_id: str
    operation: str = "identity"
    other_source_id: str | None = None
    constant: int = 0


def _validate(definition):
    if type(definition) is not Definition:
        raise ValueError("Expected a computed channel Definition")
    name = definition.name
    if (type(name) is not str or not 1 <= len(name) <= MAX_NAME_LENGTH or
            name != name.strip() or not name.isprintable()):
        raise ValueError("Name must be 1..48 printable characters without surrounding spaces")
    if type(definition.source_id) is not str or definition.source_id not in _SOURCES:
        raise ValueError("Unknown primary telemetry source")
    if (type(definition.operation) is not str or
            definition.operation not in {key for key, _ in OPERATIONS}):
        raise ValueError("Unknown computed operation")
    other = definition.other_source_id
    if other is not None and (type(other) is not str or other not in _SOURCES):
        raise ValueError("Unknown secondary telemetry source")
    if definition.operation == "identity" and other is not None:
        raise ValueError("Source value does not accept a secondary source")
    if type(definition.constant) is not int or not INT32_MIN <= definition.constant <= INT32_MAX:
        raise ValueError("Constant must be an int32 integer in the primary source unit")
    return definition


def _validate_all(definitions):
    if len(definitions) > MAX_DEFINITIONS:
        raise ValueError("At most 16 computed channels are allowed")
    validated = tuple(_validate(item) for item in definitions)
    names = [item.name.casefold() for item in validated]
    if len(names) != len(set(names)):
        raise ValueError("Computed channel names must be unique, ignoring case")
    return validated


def _quantize(value, scale):
    if type(value) not in (int, float, Decimal):
        raise ValueError("Source must have a finite numeric value")
    try:
        with localcontext() as context:
            context.prec = 64
            decimal = Decimal(str(value))
            if not decimal.is_finite():
                raise ValueError("Source must have a finite numeric value")
            scaled = (decimal * scale).to_integral_value(rounding=ROUND_HALF_UP)
            if not INT32_MIN <= scaled <= INT32_MAX:
                raise ValueError("Source overflows int32 after unit conversion")
            return int(scaled)
    except (DecimalException, OverflowError) as error:
        raise ValueError("Source overflows int32 after unit conversion") from error


def _unit(source, row):
    if source.channel_id.startswith("AI:"):
        if (type(row.unit) is not str or not row.unit or len(row.unit) > 48 or
                not row.unit.isprintable() or row.unit != row.unit.strip()):
            raise ValueError("Analogue source has no valid unit tag")
        return f"milli({row.unit})"
    return source.unit


def _age(row):
    if type(row.age) is not str or len(row.age) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)? s", row.age):
        raise ValueError("Source has no valid sample age")
    age = float(row.age[:-2])
    if not math.isfinite(age):
        raise ValueError("Source has no valid sample age")
    return age


def _push_channel(source):
    return b"\x02" + struct.pack("<H", source.vm_id)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON value is not permitted: {value}")


class ComputedChannels:
    """Local definitions only: no transport, file IO or output binding exists."""
    __slots__ = ("_definitions",)

    def __init__(self, definitions=()):
        self._definitions = _validate_all(definitions)

    @property
    def definitions(self):
        return self._definitions

    def add(self, definition):
        self._definitions = _validate_all((*self._definitions, definition))

    def replace(self, index, definition):
        self._check_index(index)
        candidate = list(self._definitions)
        candidate[index] = definition
        self._definitions = _validate_all(candidate)

    def remove(self, index):
        self._check_index(index)
        self._definitions = self._definitions[:index] + self._definitions[index + 1:]

    def _check_index(self, index):
        if type(index) is not int or not 0 <= index < len(self._definitions):
            raise ValueError("Computed channel index is out of range")

    def dumps(self):
        return json.dumps({"schema": SCHEMA, "version": VERSION,
                           "definitions": [asdict(item) for item in self._definitions]},
                          ensure_ascii=False, indent=2, allow_nan=False)

    def loads(self, text):
        if type(text) is not str:
            raise ValueError("Computed definitions must be JSON text")
        try:
            if len(text) > MAX_JSON_BYTES or len(text.encode("utf-8")) > MAX_JSON_BYTES:
                raise ValueError("Computed JSON exceeds 32 KiB")
            data = json.loads(text, object_pairs_hook=_json_object, parse_constant=_reject_constant)
        except (UnicodeError, RecursionError) as error:
            raise ValueError("Invalid computed JSON text") from error
        if type(data) is not dict or set(data) != {"schema", "version", "definitions"}:
            raise ValueError("Computed JSON must contain exactly schema, version and definitions")
        if data["schema"] != SCHEMA or type(data["version"]) is not int or data["version"] != VERSION:
            raise ValueError("Unsupported computed JSON schema or version")
        items = data["definitions"]
        if type(items) is not list or len(items) > MAX_DEFINITIONS:
            raise ValueError("Definitions must be a list with at most 16 entries")
        fields = {"name", "source_id", "operation", "other_source_id", "constant"}
        candidate = []
        for item in items:
            if type(item) is not dict or set(item) != fields:
                raise ValueError("Each definition must contain exactly the supported fields")
            candidate.append(Definition(**item))
        # Commit only after every object and the complete collection validates.
        self._definitions = _validate_all(candidate)

    def evaluate(self, samples):
        rows, duplicates = {}, set()
        for row in samples:
            if isinstance(row, TelemetryRow) and row.channel_id in _SOURCES:
                if row.channel_id in rows:
                    duplicates.add(row.channel_id)
                rows[row.channel_id] = row
        return tuple(self._evaluate(definition, rows, duplicates) for definition in self._definitions)

    @staticmethod
    def _evaluate(definition, rows, duplicates):
        source = _SOURCES[definition.source_id]
        dependencies = [source]
        if definition.other_source_id is not None:
            dependencies.append(_SOURCES[definition.other_source_id])
        issues, values, units, ages = [], {}, [], []
        for dependency in dependencies:
            row = rows.get(dependency.channel_id)
            if row is None:
                issues.append(("unavailable", f"{dependency.channel_id}: missing source"))
                continue
            if dependency.channel_id in duplicates:
                issues.append(("invalid", f"{dependency.channel_id}: duplicate source"))
                continue
            try:
                ages.append(_age(row))
            except ValueError:
                # Non-live rows may legitimately lack any timestamp.
                if row.quality == "live":
                    issues.append(("invalid", f"{dependency.channel_id}: invalid sample age"))
            if row.quality != "live":
                quality = (row.quality if type(row.quality) is str and
                           row.quality in _QUALITY_PRIORITY else "invalid")
                issues.append((quality, f"{dependency.channel_id}: {row.quality}"))
                continue
            try:
                values[dependency.vm_id] = _quantize(row.numeric, dependency.scale)
                units.append(_unit(dependency, row))
            except ValueError as error:
                issues.append(("invalid", f"{dependency.channel_id}: {error}"))
        unit = units[0] if units else source.unit
        age = f"{max(ages):.1f} s" if ages else "—"
        result_unit = "bool" if definition.operation in _COMPARISONS else unit
        if not issues and len(set(units)) != 1:
            issues.append(("invalid", "Incompatible source units: " + " and ".join(units)))
        if not issues:
            lhs = values[source.vm_id]
            rhs = (values[dependencies[1].vm_id] if len(dependencies) == 2 else definition.constant)
            if definition.operation in ("add", "sub"):
                arithmetic = lhs + rhs if definition.operation == "add" else lhs - rhs
                if not INT32_MIN <= arithmetic <= INT32_MAX:
                    issues.append(("invalid", "Arithmetic overflows int32; wrapped telemetry is not displayed"))
        if issues:
            quality = min((item[0] for item in issues), key=_QUALITY_PRIORITY.__getitem__)
            return TelemetryRow(f"VM:{definition.name}", definition.name, unit=result_unit,
                                status=f"Local preview · {quality.capitalize()}", age=age,
                                detail="; ".join(item[1] for item in issues), quality=quality)
        code = _push_channel(source)
        if definition.operation != "identity":
            code += (_push_channel(dependencies[1]) if len(dependencies) == 2 else
                     b"\x01" + struct.pack("<i", definition.constant))
            code += bytes([_OPCODES[definition.operation]])
        code += b"\xff"
        try:
            value = vm_preview.execute(code, Snapshot(values))
        except ValueError as error:
            return TelemetryRow(f"VM:{definition.name}", definition.name, unit=result_unit,
                                status="Local preview · Invalid", age=age,
                                detail=f"VM rejected preview: {error}", quality="invalid")
        return TelemetryRow(f"VM:{definition.name}", definition.name, str(value), result_unit,
                            "Local preview · diagnostic", age,
                            "Local read-only R2 VM; scaled integers use ROUND_HALF_UP; no output binding",
                            value, "live")
