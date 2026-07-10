"""Response shaping for hardware-facing APIs."""

from __future__ import annotations

from typing import Any, Mapping


ESP32_REPORT_KEYS = (
    'status',
    'ok',
    'event',
    'accepted',
    'ignored',
    'duplicate',
    'fast_path',
    'fallback_to_legacy',
    'reason',
    'message',
    'fruit_id',
    'active_fruit_id',
    'source',
    'pending_capture',
    'capture_token',
    'station_index',
    'capture_requested',
    'motor_command',
    'timing_revision',
    'reported_timing_revision',
    'capture_timing_status',
    'capture_timing_revision',
    'capture_timing_applied_revision',
)


def compact_esp32_report(payload: Mapping[str, Any], server_status: str) -> dict[str, Any]:
    compact = {key: payload[key] for key in ESP32_REPORT_KEYS if key in payload}
    compact['server_status'] = payload.get('status', server_status)
    return compact
