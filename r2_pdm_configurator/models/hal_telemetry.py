"""Read-only presentation of actual HAL diagnostic channels, without sensor guesses."""
from .telemetry import TelemetryRow

DIAGNOSTIC_CHANNELS = (
    *((200 + i, f'Output {i + 1:02} gate (interlocked)', 'bool') for i in range(14)),
    (230, 'Output interlock', 'bool'), (240, 'CAN received frames', 'count'),
    (241, 'Last CAN identifier', 'ID'), (242, 'CAN error count', 'count'),
    (243, 'Uptime', 'ms'), (244, 'Diagnostic fault mask', 'flags'),
    (300, 'Device VM diagnostic result', 'int32'),
)
ADC_CHANNELS = (
    (100, 'Battery voltage', 'V'),
    (101, 'Total IS current estimate (14 channels)', 'A'),
    (102, 'MCU die temperature', '°C'),
    *((110 + i, f'Output {i + 1:02} IS current estimate', 'A') for i in range(14)),
)
# Append ADC rows so the existing diagnostic VM result remains at index 20.
HAL_CHANNELS = DIAGNOSTIC_CHANNELS + ADC_CHANNELS
_ADC_IDS = frozenset(number for number, _, _ in ADC_CHANNELS)
HAL_ROWS = [(f'HAL:{number}', label, 'hal', number) for number, label, _ in HAL_CHANNELS]


def hal_snapshot(client):
    result = []
    for number, label, unit in HAL_CHANNELS:
        sample = client.samples.get(number) if client is not None else None
        quality, value, status, numeric, age = 'unavailable', '—', 'Unavailable', None, '—'
        detail = ('No ADC sample received; device firmware may not support ADC telemetry.'
                  if number in _ADC_IDS else 'No valid HAL diagnostic sample received.')
        if sample is not None:
            elapsed = client.now() - sample.last_seen
            age = f'{max(0, elapsed):.1f} s'
            detail = f'Actual HAL channel {number}; sequence {sample.sequence}; received at {sample.last_seen:.3f} s'
            if number in _ADC_IDS and sample.quality in (1, 3, 4):
                quality, status = {
                    1: ('unavailable', 'Unavailable (not sampled)'),
                    3: ('invalid', 'Invalid / saturated / acquisition fault'),
                    4: ('stale', 'Stale acquisition (device)'),
                }[sample.quality]
            elif number not in _ADC_IDS and sample.quality == 1:
                status = 'Unavailable (device)'
            elif sample.quality != 0 and not (number in _ADC_IDS and sample.quality == 2):
                quality, status = 'invalid', 'Unsupported sample quality'
            elif number == 300 and not client.vm_active:
                status = 'Unavailable (no active VM)'
            elif not client.online():
                quality, status = 'degraded', 'No fresh HAL heartbeat'
            elif not client.sample_valid(number):
                quality, status = 'stale', 'Stale'
            else:
                quality, status, value, numeric = 'live', 'HAL diagnostic', str(sample.value), sample.value
                if number in _ADC_IDS:
                    numeric = sample.value / 1000
                    value = f'{numeric:.3f}'
                    if sample.quality == 2:
                        quality, status = 'nominal', 'Nominal calibration · estimate'
                        detail += '; nominal board gain, not per-board calibration'
                    else:
                        status = 'Factory calibration · diagnostic'
                    if number == 102:
                        detail += '; MCU die temperature, not board/driver/coolant temperature'
                    elif number == 101 or 110 <= number <= 123:
                        detail += '; diagnostic IS-pin estimate, not independently verified load current; gate OFF does not force zero'
                elif (200 <= number <= 213 and sample.value != 0) or (number == 230 and sample.value != 1):
                    quality, status, value, numeric = 'fault', 'Interlock invariant violated', '—', None
                elif number == 244 and sample.value:
                    quality, status = 'fault', 'Diagnostic faults'
        result.append(TelemetryRow(f'HAL:{number}', label, value, unit, status, age, detail, numeric, quality))
    return tuple(result)


def hal_diagnostics(client):
    note = ('HAL diagnostics only · hardware outputs remain OFF pending HIL.\n'
            'Orange values use nominal board calibration, not per-board calibration. '
            'IS currents are diagnostic estimates even with gates OFF. Temperature is MCU die.\n'
            'Missing/invalid/stale ADC values are unavailable; physical input telemetry remains unavailable.')
    if client is None:
        return 'HAL disconnected.\n' + note
    return (f'{"HAL online" if client.online() else "No fresh compatible HAL heartbeat"} · '
            f'RX {client.rx_frames} · rejected {client.bad_frames}\n' + note)
