"""Read-only presentation of coherent device start-admission snapshots."""
from dataclasses import dataclass


@dataclass(frozen=True)
class StartDiagnosticsView:
    status: str
    summary: str
    groups: str = ''
    rows: tuple[tuple[str, ...], ...] = ()
    quality: str = 'unavailable'


def _amps(value):
    return 'Unknown' if value is None else f'{value / 1000:.3f} A'


def start_diagnostics_view(client):
    """No polling, writes, or Python admission policy; consume client snapshots."""
    if client is None:
        return StartDiagnosticsView('HAL disconnected · no scheduler data',
            'Connect using STM32Cube HAL diagnostics to receive passive scheduler telemetry.')
    if not client.online():
        return StartDiagnosticsView('No fresh compatible HAL heartbeat · values cleared',
            'Device queue, reservations and budgets are unavailable.', quality='stale')
    snapshot = getattr(client, 'start_snapshot', None)
    error = getattr(client, 'start_snapshot_error', '')
    if snapshot is None:
        return StartDiagnosticsView('Waiting for a complete scheduler snapshot',
            error or 'This firmware may not publish scheduler diagnostics. No requests are sent.')
    if not client.start_snapshot_valid():
        return StartDiagnosticsView('Scheduler snapshot stale or incomplete · values cleared',
            client.start_snapshot_error or 'Fresh heartbeat alone does not make scheduler values fresh.', quality='stale')
    age = max(0.0, client.now() - snapshot.last_seen)
    origin = 'SIMULATION FIXTURES · not approved hardware limits' if snapshot.simulation_only else 'Device diagnostics'
    inhibit = 'inhibited' if snapshot.inhibited else 'inhibit not asserted (diagnostic flag)'
    off = 'asserted' if snapshot.physical_outputs_off else 'NOT asserted'
    identity = (f'Profile {snapshot.profile} · {snapshot.count} advertised channels · {inhibit} · age {age:.1f} s'
                f' · output-OFF flag {off}')
    if not snapshot.available:
        return StartDiagnosticsView(f'{origin} · UNCOMMISSIONED / UNAVAILABLE',
            identity + '\nStart slots: Unknown · total budget: Unknown · reservations: Unknown. '
            'No commissioned channel or group limits were advertised.', quality='unavailable')
    rows = []
    for channel in snapshot.channels:
        reason = channel.reason.replace('_', ' ').lower()
        if channel.state == 'QUEUED' and (channel.queue_position or 0) > 1:
            reason = 'Waiting behind queue head'
        elif channel.state == 'QUEUED' and channel.reason == 'OK':
            reason = 'Awaiting dispatch'
        rows.append((str(channel.channel), str(channel.queue_position or '—'), channel.state,
                     reason, _amps(channel.reserve_ma), str(channel.group),
                     'Held' if channel.slot_reserved else '—'))
    groups = ' · '.join(f'Group {group.id}: {_amps(group.reserved_ma)} reserved / {_amps(group.budget_ma)} budget'
                        for group in snapshot.groups)
    slots_used = snapshot.starts_reserved
    summary = (identity + f'\nStart slots {slots_used} / {snapshot.slots} · '
               f'Total {_amps(snapshot.total_reserved_ma)} reserved / {_amps(snapshot.total_budget_ma)} budget · '
               f'Current {_amps(snapshot.total_measured_ma)} · queue {len(snapshot.queue)}')
    return StartDiagnosticsView(origin + ' · coherent snapshot', summary, groups, tuple(rows),
                                'simulation' if snapshot.simulation_only else 'live')
