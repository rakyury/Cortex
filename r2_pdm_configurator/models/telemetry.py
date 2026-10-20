"""Immutable presentation snapshots; no Qt, bus I/O, or output commands."""
from dataclasses import dataclass
import math

ROWS = ([(f'SYS:{name}', name, 'system', 0) for name in
         ('Voltage', 'Current', 'Temperature', 'Errors', 'Alive')] +
        [(f'OUT:{n:02}', f'Output {n:02}', 'output', n) for n in range(1, 15)] +
        [(f'DI:{n:02}', f'Digital {n}', 'digital', n) for n in range(1, 9)] +
        [(f'AI:{n:02}', f'Analogue {n}', 'analogue', n) for n in range(1, 9)])
# Each output/analogue request is serialized; allow a complete poll cycle.
FRESH_SECONDS = {'system': 1.0, 'output': 7.0, 'digital': 2.0, 'analogue': 6.0}


@dataclass(frozen=True)
class TelemetryRow:
    channel_id: str
    label: str
    value: str = '—'
    unit: str = ''
    status: str = 'Unavailable'
    age: str = '—'
    detail: str = 'No timestamped sample received'
    numeric: float | None = None
    quality: str = 'unavailable'

    @property
    def columns(self):
        return (self.channel_id, self.label, self.value, self.unit, self.status, self.age)


def snapshot(client) -> tuple[TelemetryRow, ...]:
    if client is None:
        return tuple(TelemetryRow(cid, label) for cid, label, _, _ in ROWS)
    now = client.now()
    online = client.online()
    result = []
    for cid, label, kind, number in ROWS:
        seen = None
        value, unit, status, numeric = '—', '', 'Live', None
        if kind == 'system':
            s = client.system
            seen = s.last_seen or None
            key = cid.split(':')[1]
            numeric, unit = {
                'Voltage': (s.battery_v, 'V'), 'Current': (s.total_current_a, 'A'),
                'Temperature': (s.temperature_c, '°C'), 'Errors': (s.error_flags, ''),
                'Alive': (s.alive, ''),
            }[key]
            if math.isfinite(numeric):
                value = f'0x{int(numeric):04X}' if key == 'Errors' else f'{numeric:g}'
            if s.error_flags:
                status = f'Fault flags 0x{s.error_flags:04X}'
        elif kind == 'output' and number in client.channels:
            ch = client.channels[number]
            seen = ch.last_seen
            label = ch.name or label
            numeric, unit = ch.current_a, 'A'
            value = f'{numeric:.1f}'
            status = 'ON' if ch.enabled else 'OFF'
        elif kind == 'digital' and client.digital_mask is not None:
            seen = getattr(client, 'digital_last_seen', None)
            numeric = (client.digital_mask >> (number - 1)) & 1
            value, status = str(numeric), 'Input'
        elif kind == 'analogue' and number in client.analogues:
            ai = client.analogues[number]
            seen = ai.last_seen
            numeric, unit = ai.value, f'unit {ai.units}'
            value, status = f'{numeric:.3f}', f'{ai.voltage_v:.3f} V'
            if not math.isfinite(ai.voltage_v):
                numeric = float('nan')
        quality = 'live'
        age = now - seen if seen is not None else None
        if age is None:
            quality, status = 'unavailable', 'Unavailable'
        elif not math.isfinite(age) or age < 0 or numeric is None or not math.isfinite(numeric):
            quality, status = 'invalid', 'Invalid'
        elif age >= FRESH_SECONDS[kind]:
            quality, status = 'stale', 'Stale'
        elif not online:
            quality, status = 'degraded', 'Degraded: no heartbeat'
        elif kind == 'system' and client.system.error_flags:
            quality = 'fault'
        detail = ('No timestamped sample received' if seen is None else
                  f'Last sample: {value} {unit}; received at {seen:.3f} s (monotonic); '
                  f'freshness limit {FRESH_SECONDS[kind]:g} s')
        if quality in {'unavailable', 'invalid', 'stale', 'degraded'}:
            value, numeric = '—', None
        result.append(TelemetryRow(cid, label, value, unit, status,
                                  f'{age:.1f} s' if age is not None and math.isfinite(age) else '—',
                                  detail, numeric, quality))
    return tuple(result)


def diagnostics(client, rows) -> str:
    if client is None:
        return 'Disconnected — no live telemetry'
    counts = {quality: sum(row.quality == quality for row in rows)
              for quality in ('live', 'stale', 'invalid', 'degraded', 'unavailable', 'fault')}
    summary = ' · '.join(f'{quality}: {count}' for quality, count in counts.items() if count)
    return (f'{"PDM online" if client.online() else "No fresh primary heartbeat"} · {summary}\n'
            f'RX {client.rx_frames} · malformed / timeouts {client.bad_frames}\n'
            'Legacy channel replies have no transaction ID; readings are diagnostic. '
            'A successful send is not configuration acknowledgement.')
