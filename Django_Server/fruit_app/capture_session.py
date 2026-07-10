"""Pure capture-session transitions shared by the HTTP endpoints.

The module deliberately has no Django imports.  Keeping the state mutation in
one place makes the three-station handshake testable and avoids a view growing
new ad-hoc state changes whenever firmware adds an event.
"""

from __future__ import annotations

from typing import Any, Callable, MutableMapping, Optional


def request_station_capture(
    state: MutableMapping[str, Any],
    *,
    fruit_id: str,
    station_index: int,
    now_string: Callable[[], str],
    monotonic: Callable[[], float],
    increment_token: bool,
) -> int:
    """Transition a stopped station into the single-photo camera request.

    ``increment_token`` is true for the established command/ready handshake.
    The HC-SR04 fast path creates a new session immediately before this call,
    so it keeps that session token instead of issuing a second one.
    """
    if increment_token:
        state['capture_token'] += 1

    station_statuses = state.setdefault('station_statuses', {})
    station_statuses[str(station_index)] = 'ready'
    state['motor_command'] = None
    state['active_station_index'] = station_index
    state['pending_capture'] = True
    state['status'] = 'waiting_camera'
    state['command_created_at'] = now_string()
    state['command_created_monotonic'] = monotonic()
    state['capture_started_at'] = None
    state['capture_started_monotonic'] = None
    state['command_to_phone_start_ms'] = None
    state['upload_received_at'] = None
    state['message'] = (
        f'{fruit_id} station {station_index} is stable; waiting for one camera upload.'
    )
    return state['capture_token']


def append_transition_trace(
    state: MutableMapping[str, Any],
    *,
    event: str,
    now_string: Callable[[], str],
    monotonic: Callable[[], float],
    fruit_id: Optional[str] = None,
    station_index: Optional[int] = None,
    command_id: Optional[int] = None,
    trigger_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    max_events: int = 64,
) -> dict[str, Any]:
    """Append a bounded, JSON-safe transition trace entry and bump revision."""
    state['trace_sequence'] = int(state.get('trace_sequence') or 0) + 1
    state['state_revision'] = int(state.get('state_revision') or 0) + 1

    entry: dict[str, Any] = {
        'seq': state['trace_sequence'],
        'event': event,
        'at': now_string(),
        'monotonic_ms': round(monotonic() * 1000, 1),
        'fruit_id': fruit_id if fruit_id is not None else state.get('active_fruit_id'),
    }
    if station_index is not None:
        entry['station_index'] = station_index
    if command_id is not None:
        entry['command_id'] = command_id
    if trigger_id:
        entry['trigger_id'] = trigger_id
    if details:
        entry['details'] = details

    trace = state.setdefault('transition_trace', [])
    trace.append(entry)
    if len(trace) > max_events:
        del trace[:-max_events]
    return entry
