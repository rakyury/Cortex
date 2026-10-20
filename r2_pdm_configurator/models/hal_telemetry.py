"""Read-only presentation of actual HAL diagnostic channels, without sensor guesses."""
from .telemetry import TelemetryRow

HAL_CHANNELS = (
    *((200 + i, f'Output {i + 1:02} gate (interlocked)', 'bool') for i in range(14)),
    (230, 'Output interlock', 'bool'), (240, 'CAN received frames', 'count'),
    (241, 'Last CAN identifier', 'ID'), (242, 'CAN error count', 'count'),
    (243, 'Uptime', 'ms'), (244, 'Diagnostic fault mask', 'flags'),
    (300, 'Device VM diagnostic result', 'int32'),
)
HAL_ROWS = [(f'HAL:{number}', label, 'hal', number) for number, label, _ in HAL_CHANNELS]


def hal_snapshot(client):
    result = []
    for number, label, unit in HAL_CHANNELS:
        sample = client.samples.get(number) if client is not None else None
        quality, value, status, numeric, age = 'unavailable', '—', 'Unavailable', None, '—'
        detail = 'No valid HAL sample received. ADC/current/temperature are not implemented.'
        if sample is not None:
            elapsed = client.now() - sample.last_seen
            age = f'{max(0, elapsed):.1f} s'
            detail = f'Actual HAL channel {number}; sequence {sample.sequence}; received at {sample.last_seen:.3f} s'
            if sample.quality != 0:
                status = 'Unavailable (device)'
            elif number == 300 and not client.vm_active:
                status = 'Unavailable (no active VM)'
            elif not client.online():
                quality, status = 'degraded', 'No fresh HAL heartbeat'
            elif not client.sample_valid(number):
                quality, status = 'stale', 'Stale'
            else:
                quality, status, value, numeric = 'live', 'HAL diagnostic', str(sample.value), sample.value
                if (200 <= number <= 213 and sample.value != 0) or (number == 230 and sample.value != 1):
                    quality, status, value, numeric = 'fault', 'Interlock invariant violated', '—', None
                elif number == 244 and sample.value:
                    quality, status = 'fault', 'Diagnostic faults'
        result.append(TelemetryRow(f'HAL:{number}', label, value, unit, status, age, detail, numeric, quality))
    return tuple(result)


def hal_diagnostics(client):
    note = ('HAL diagnostics only · hardware outputs remain OFF pending HIL.\n'
            'ADC current, battery voltage, temperature and physical input telemetry are unavailable. '
            'Gate states are not measured load currents.')
    if client is None:
        return 'HAL disconnected.\n' + note
    return (f'{"HAL online" if client.online() else "No fresh compatible HAL heartbeat"} · '
            f'RX {client.rx_frames} · rejected {client.bad_frames}\n' + note)
