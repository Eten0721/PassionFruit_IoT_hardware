import csv
import json
import os
import re
import shutil
import time
from datetime import datetime
from functools import cache
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import render
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import api_payloads, capture_session, capture_timing, dataset_store, motor_commands, webrtc_signaling
from .runtime_state import RuntimeState


LABELS = ['上等', '中等', '下等', '加工']
CLASSIFICATION_CODES = {
    '上等': 'high',
    '中等': 'medium',
    '下等': 'low',
    '加工': 'processing',
}
IMAGE_COUNT = 3
IMAGE_FILENAMES = [f'img_{idx:02d}.jpg' for idx in range(1, IMAGE_COUNT + 1)]
FRUIT_ID_PATTERN = re.compile(r'^fruit_(\d{3,})$')
METADATA_FIELDNAMES = [
    'fruit_id',
    'label',
    'capture_time',
    'path',
    'capture_count',
    'station_01_ok',
    'station_02_ok',
    'station_03_ok',
    'note',
]
HOME_ANGLE = 0
RELEASE_ANGLE = 90
# Conservative defaults that still preserve the first-station fast path.
FIRST_STATION_SETTLE_MS = 300
SERVO_SETTLE_MS = 200
FRUIT_SETTLE_MS = 350
FINAL_GATE_RETURN_DELAY_MS = 300
IDLE_COMMAND_POLL_INTERVAL_MS = 250
FEEDER_STOP_US = 1500
FEEDER_DRIVE_US = 1300
FEEDER_MAX_RUN_MS = 150
CAPTURE_TIMING_RECOMMENDED = {
    'first_station_settle_ms': FIRST_STATION_SETTLE_MS,
    'servo_settle_ms': SERVO_SETTLE_MS,
    'fruit_settle_ms': FRUIT_SETTLE_MS,
    'final_gate_return_delay_ms': FINAL_GATE_RETURN_DELAY_MS,
    'idle_command_poll_interval_ms': IDLE_COMMAND_POLL_INTERVAL_MS,
    'feeder_stop_us': FEEDER_STOP_US,
    'feeder_drive_us': FEEDER_DRIVE_US,
    'feeder_max_run_ms': FEEDER_MAX_RUN_MS,
    'feeder_calibrated': False,
}
CAPTURE_TIMING_FIELDS = tuple(
    field for field in CAPTURE_TIMING_RECOMMENDED
    if field != 'feeder_calibrated'
)
FEEDER_MECHANICAL_FIELDS = (
    'feeder_stop_us',
    'feeder_drive_us',
    'feeder_max_run_ms',
)
CAPTURE_TIMING_STEP_MS = 50
CAPTURE_TIMING_MIN_MS = 50
CAPTURE_TIMING_MAX_MS = 3000
CAPTURE_TIMING_FIELD_LIMITS = {
    'idle_command_poll_interval_ms': (100, 5000),
    'feeder_stop_us': (1400, 1600),
    'feeder_drive_us': (1000, 2000),
    'feeder_max_run_ms': (50, 500),
}
CAPTURE_TIMING_FIELD_STEPS = {
    'feeder_stop_us': 5,
    'feeder_drive_us': 10,
    'feeder_max_run_ms': 5,
}
CAPTURE_TIMING_FIELD_UNITS = {
    'feeder_stop_us': 'us',
    'feeder_drive_us': 'us',
}
ESP32_ONLINE_WINDOW_SECONDS = 20
ESP32_START_TIMEOUT_SECONDS = 10
HARDWARE_STEP_TIMEOUT_SECONDS = 20
CAMERA_UPLOAD_TIMEOUT_SECONDS = 45
SORTER_PENDING_TIMEOUT_SECONDS = 30
SORTER_RUNNING_TIMEOUT_SECONDS = 30
CAMERA_READY_WINDOW_SECONDS = 5
FILE_OPERATION_RETRIES = 2
FILE_OPERATION_RETRY_DELAY_SECONDS = 0.05
DISCARD_CLEANUP_RETRY_INTERVAL_SECONDS = 30
UPLOAD_STAGING_SUFFIX = '.uploading'
CLIENT_TIMING_KEYS = (
    # Current camera page timestamps.
    'request_received_at_ms',
    'video_ready_at_ms',
    'capture_started_sent_at_ms',
    'frame_drawn_at_ms',
    'blob_ready_at_ms',
    'upload_started_at_ms',
    'frame_to_blob_ready_ms',
    # Keep the pre-fast-path aliases accepted during staggered deployment.
    'request_received_ms',
    'video_ready_ms',
    'capture_started_sent_ms',
    'frame_drawn_ms',
    'blob_ready_ms',
    'upload_started_ms',
    'request_to_video_ready_ms',
    'request_to_frame_drawn_ms',
    'request_to_blob_ready_ms',
    'request_to_upload_started_ms',
)

APP_STATE = RuntimeState({
    'pending_capture': False,
    'capture_token': 0,
    'active_fruit_id': None,
    'capture_time': None,
    'source': None,
    'active_station_index': None,
    'station_statuses': {},
    'motor_command': None,
    'motor_command_id': 0,
    'last_error_reason': None,
    'last_error_command': None,
    'last_error_command_id': None,
    'wait_started_at': None,
    'wait_started_monotonic': None,
    'last_esp32_poll_at': None,
    'last_esp32_poll_monotonic': None,
    'last_esp32_report_at': None,
    'command_created_at': None,
    'command_created_monotonic': None,
    'capture_started_at': None,
    'capture_started_monotonic': None,
    'command_to_phone_start_ms': None,
    'client_timing': {},
    'upload_received_at': None,
    # Fast station-1 reports are idempotent across HTTPS response timeouts.
    'fast_path_trigger_id': None,
    'fast_path_fruit_id': None,
    'fast_path_capture_token': None,
    # A small in-memory trace gives dashboard and Serial logs one timing chain.
    'state_revision': 0,
    'trace_sequence': 0,
    'transition_trace': [],
    'capture_timing': dict(CAPTURE_TIMING_RECOMMENDED),
    'capture_timing_revision': 1,
    'capture_timing_applied_revision': 0,
    'capture_timing_applied_at': None,
    'capture_timing_loaded_path': None,
    'capture_timing_warning': None,
    'auto_run_enabled': False,
    'auto_feed_pending': False,
    'auto_run_finishing': False,
    'auto_run_recovery_reason': None,
    'camera_last_live_frame_at': None,
    'camera_last_live_frame_monotonic': None,
    'esp32_boot_id': None,
    'esp32_feeder_capable': False,
    'esp32_feeder_state': None,
    'esp32_feeder_sensor_state': None,
    'esp32_last_feed_command_id': 0,
    'last_completed_feed_command_id': 0,
    'feeder_test_passed_revision': None,
    'feeder_test_result': None,
    'last_delayed_feed_command_id': 0,
    'discard_cleanup_last_monotonic': None,
    'dataset_operation': None,
    'sorter_status': 'idle',
    'sorter_command_id': None,
    'sorter_fruit_id': None,
    'sorter_label': None,
    'sorter_classification_code': None,
    'sorter_error': None,
    'sorter_started_monotonic': None,
    'sorter_deadline_monotonic': None,
    'status': 'idle',
    'message': '等待手機相機、ESP32 與送料校正完成。',
})
STATE_LOCK = APP_STATE.lock
WEBRTC_STATE = {
    'offer': None,
    'offer_id': 0,
    'answer': None,
    'answer_id': 0,
    'dashboard_ice': [],
    'camera_ice': [],
    'updated_at': None,
}


class DatasetFileBusyError(Exception):
    pass


class CaptureCommandError(Exception):
    def __init__(self, message, status=409, reason='capture_unavailable'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason


def _print_timing_log(event_name, **fields):
    parts = [f'DJANGO_TIMING {event_name}']
    for key, value in fields.items():
        if value is not None:
            parts.append(f'{key}={value}')
    print(' '.join(parts), flush=True)


def _record_transition(
    event,
    *,
    fruit_id=None,
    station_index=None,
    command_id=None,
    trigger_id=None,
    details=None,
):
    """Record one bounded state transition while ``STATE_LOCK`` is held."""
    return capture_session.append_transition_trace(
        APP_STATE,
        event=event,
        now_string=_now_string,
        monotonic=time.monotonic,
        fruit_id=fruit_id,
        station_index=station_index,
        command_id=command_id,
        trigger_id=trigger_id,
        details=details,
    )


def home_view(request):
    return render(request, 'home.html')


def camera_view(request):
    _ensure_dataset_structure()
    return _no_store_response(render(request, 'camera.html'))


def dashboard_view(request):
    _ensure_dataset_structure()
    return _no_store_response(render(request, 'dashboard.html'))


@require_GET
def state_api(request):
    _ensure_dataset_structure()
    _retry_deferred_discards_if_due()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        payload = _state_payload()
    return _no_store_response(JsonResponse(payload))


@require_GET
def camera_state_api(request):
    """Return only the fields needed by the high-frequency camera poller."""
    _ensure_dataset_structure()
    with STATE_LOCK:
        if request.GET.get('camera_ready') == '1':
            APP_STATE['camera_last_live_frame_at'] = _now_string()
            APP_STATE['camera_last_live_frame_monotonic'] = time.monotonic()
            _queue_pending_auto_feed()
        _apply_session_timeouts()
        payload = _camera_state_payload()
    return _no_store_response(JsonResponse(payload))


@csrf_exempt
@require_POST
def set_counter_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    raw_value = data.get('start_id') or data.get('next_id') or data.get('value')
    try:
        next_id = int(raw_value)
    except (TypeError, ValueError):
        return _json_error('請輸入有效的起始 ID。', status=400)
    if next_id < 1:
        return _json_error('起始 ID 必須大於 0。', status=400)

    with STATE_LOCK:
        fruit_id = _format_fruit_id(next_id)
        if _fruit_id_exists(fruit_id):
            return _json_error(f'{fruit_id} 已有有效資料，請先重置 dataset 或改用其他起始 ID。', status=409)
        _write_counter(next_id)
        APP_STATE['message'] = f'下一筆資料將從 {fruit_id} 開始。'
    return JsonResponse({'status': 'success', 'next_fruit_id': fruit_id})


@csrf_exempt
@require_POST
def capture_timing_api(request):
    """Persist a complete next-fruit timing profile while the controller is idle."""
    _ensure_dataset_structure()
    data = _request_data(request)
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if (
            APP_STATE.get('auto_run_enabled')
            or _sorter_busy()
            or APP_STATE['status'] != 'idle'
            or APP_STATE.get('active_fruit_id')
            or APP_STATE.get('motor_command')
        ):
            return _json_error(
                '拍攝流程進行中，請等待目前 fruit 完成、分類或跳過後再調整停穩時間。',
                status=409,
                reason='capture_timing_update_requires_idle',
            )

        try:
            timing_data = dict(data)
            timing_data.setdefault(
                'idle_command_poll_interval_ms',
                APP_STATE['capture_timing']['idle_command_poll_interval_ms'],
            )
            for field in (
                'feeder_stop_us',
                'feeder_drive_us',
                'feeder_max_run_ms',
                'feeder_calibrated',
            ):
                timing_data.setdefault(field, APP_STATE['capture_timing'][field])
            timing = _normalise_capture_timing(timing_data, require_all=True)
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status, reason=exc.reason)

        feeder_changed = any(
            timing[field] != APP_STATE['capture_timing'][field]
            for field in FEEDER_MECHANICAL_FIELDS
        )
        if feeder_changed:
            timing['feeder_calibrated'] = False
        elif (
            timing['feeder_calibrated']
            and not APP_STATE['capture_timing']['feeder_calibrated']
            and APP_STATE.get('feeder_test_passed_revision')
            != APP_STATE['capture_timing_revision']
        ):
            return _json_error(
                '必須先以相同 revision 完成 HC-SR04 測試送料，才能確認校正。',
                status=409,
                reason='feeder_test_required',
            )

        if timing == APP_STATE['capture_timing']:
            return JsonResponse(_state_payload(extra={
                'ok': True,
                'capture_timing_unchanged': True,
            }))

        previous_timing = APP_STATE['capture_timing']
        previous_revision = APP_STATE['capture_timing_revision']
        previous_applied_at = APP_STATE['capture_timing_applied_at']
        previous_passed_revision = APP_STATE.get('feeder_test_passed_revision')
        APP_STATE['capture_timing'] = timing
        profile_changed = any(
            timing[field] != previous_timing[field]
            for field in CAPTURE_TIMING_FIELDS
        )
        APP_STATE['capture_timing_revision'] = previous_revision + int(profile_changed)
        if profile_changed:
            APP_STATE['capture_timing_applied_at'] = None
        if feeder_changed:
            APP_STATE['feeder_test_passed_revision'] = None
        try:
            capture_timing.write(
                _capture_timing_path(),
                timing,
                APP_STATE['capture_timing_revision'],
            )
        except OSError as exc:
            APP_STATE['capture_timing'] = previous_timing
            APP_STATE['capture_timing_revision'] = previous_revision
            APP_STATE['capture_timing_applied_at'] = previous_applied_at
            APP_STATE['feeder_test_passed_revision'] = previous_passed_revision
            return _json_error(
                f'無法保存拍攝停穩設定：{exc}',
                status=500,
                reason='capture_timing_persist_failed',
            )
        _record_transition(
            'capture_timing_updated',
            details={
                'revision': APP_STATE['capture_timing_revision'],
                **timing,
            },
        )
        payload = _state_payload(extra={
            'ok': True,
            'capture_timing_updated': True,
        })
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def feeder_test_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        reason = _feeder_test_disabled_reason()
        if reason:
            return _json_error(
                '目前無法測試送料，請先讓系統安全閒置、連接送料韌體並等待設定套用。',
                status=409,
                reason=reason,
            )
        try:
            command = _set_motor_command('feed_one', feed_context='calibration')
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status, reason=exc.reason)
        APP_STATE['feeder_test_passed_revision'] = None
        APP_STATE['last_error_reason'] = None
        APP_STATE['last_error_command'] = None
        APP_STATE['last_error_command_id'] = None
        APP_STATE['status'] = 'waiting_feeder'
        APP_STATE['message'] = 'ESP32 正在執行一次送料校正測試。'
        payload = _state_payload(extra={'ok': True, 'feeder_test_started': True})
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def auto_run_api(request):
    _ensure_dataset_structure()
    enabled = _request_data(request).get('enabled')
    if not isinstance(enabled, bool):
        return _json_error(
            'enabled 必須是布林值。',
            status=400,
            reason='invalid_auto_run_enabled',
        )

    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if not enabled:
            waiting_without_fruit = (
                APP_STATE.get('status') == 'waiting_fruit'
                and not APP_STATE.get('active_fruit_id')
            )
            if waiting_without_fruit:
                APP_STATE['auto_run_recovery_reason'] = (
                    APP_STATE.get('auto_run_recovery_reason')
                    or 'feed_arrival_unconfirmed'
                )
                APP_STATE['auto_run_finishing'] = False
            elif APP_STATE.get('auto_run_enabled'):
                APP_STATE['auto_run_finishing'] = not APP_STATE.get('auto_feed_pending')
            _disable_auto_run()
            APP_STATE['message'] = '已要求暫停；目前百香果會完成既有流程，不再送入下一顆。'
            _record_transition('auto_run_paused')
            return JsonResponse(_state_payload(extra={'ok': True}))

        if APP_STATE.get('auto_run_enabled'):
            return JsonResponse(_state_payload(extra={'ok': True}))

        reason = _auto_run_disabled_reason(allow_recovery=True)
        if reason:
            return _json_error(
                '目前無法開始自動運轉，請確認相機、ESP32、設定與送料校正皆已就緒。',
                status=409,
                reason=reason,
            )

        recovery_confirmed = bool(APP_STATE.get('auto_run_recovery_reason'))
        APP_STATE['auto_run_recovery_reason'] = None
        APP_STATE['last_error_reason'] = None
        APP_STATE['last_error_command'] = None
        APP_STATE['last_error_command_id'] = None
        APP_STATE['status'] = 'idle'
        APP_STATE['esp32_feeder_state'] = 'idle'
        APP_STATE['auto_run_enabled'] = True
        APP_STATE['auto_run_finishing'] = False
        try:
            command = _queue_production_feed()
            command['recovery_confirmed'] = recovery_confirmed
        except CaptureCommandError as exc:
            APP_STATE['auto_run_enabled'] = False
            return _json_error(exc.message, status=exc.status, reason=exc.reason)
        _record_transition('auto_run_started')
        return JsonResponse(_state_payload(extra={'ok': True}))


@csrf_exempt
@require_POST
def esp32_trigger_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        APP_STATE['last_esp32_report_at'] = _now_string()
        try:
            payload = _create_capture_session(source='esp32')
        except CaptureCommandError as exc:
            return _json_error(
                exc.message,
                status=exc.status,
                reason=exc.reason,
                extra={'ok': False},
            )
        payload.update({
            'ok': True,
            'trigger_source': 'esp32',
        })
    return JsonResponse(payload)


@require_GET
def esp32_command_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        APP_STATE['last_esp32_poll_at'] = _now_string()
        APP_STATE['last_esp32_poll_monotonic'] = time.monotonic()
        previous_boot_id = APP_STATE.get('esp32_boot_id')
        boot_id = (request.GET.get('boot_id') or '').strip() or None
        feeder_state = (request.GET.get('feeder_state') or '').strip()
        feeder_sensor_state = (request.GET.get('feeder_sensor_state') or '').strip()
        current_command = APP_STATE.get('motor_command') or {}
        if (
            previous_boot_id
            and boot_id
            and boot_id != previous_boot_id
            and current_command.get('command') == 'feed_one'
        ):
            APP_STATE['motor_command'] = None
            _disable_auto_run()
            APP_STATE['auto_run_finishing'] = False
            APP_STATE['last_error_reason'] = 'esp32_restarted_during_feed'
            APP_STATE['last_error_command'] = current_command.get('command')
            APP_STATE['last_error_command_id'] = current_command.get('command_id')
            APP_STATE['auto_run_recovery_reason'] = 'esp32_restarted_during_feed'
            APP_STATE['status'] = 'error'
            APP_STATE['message'] = (
                'ESP32 在送料完成確認前重新啟動；已停止自動運轉且不重送，'
                '請暫停並檢查送料區域。'
            )
            _record_transition(
                'esp32_restarted_during_feed',
                command_id=current_command.get('command_id'),
                details={'previous_boot_id': previous_boot_id, 'boot_id': boot_id},
            )
        APP_STATE['esp32_boot_id'] = boot_id
        APP_STATE['esp32_feeder_capable'] = request.GET.get('capability') == 'feeder_v1'
        APP_STATE['esp32_feeder_state'] = (
            feeder_state if feeder_state in ('idle', 'awaiting_fruit') else None
        )
        APP_STATE['esp32_feeder_sensor_state'] = (
            feeder_sensor_state
            if feeder_sensor_state in ('clear', 'blocked', 'unavailable')
            else None
        )
        APP_STATE['esp32_last_feed_command_id'] = max(
            _safe_int(request.GET.get('last_feed_command_id')) or 0,
            0,
        )
        known_feed_wait = (
            APP_STATE.get('last_completed_feed_command_id', 0) > 0
            and APP_STATE['esp32_last_feed_command_id']
            == APP_STATE['last_completed_feed_command_id']
        )
        recovery_feed_pending = (
            current_command.get('command') == 'feed_one'
            and current_command.get('recovery_confirmed')
        )
        if (
            feeder_state == 'awaiting_fruit'
            and not APP_STATE.get('active_fruit_id')
            and not known_feed_wait
            and not recovery_feed_pending
        ):
            _disable_auto_run()
            APP_STATE['auto_run_finishing'] = False
            APP_STATE['auto_run_recovery_reason'] = 'feed_arrival_unconfirmed'
            APP_STATE['last_error_command'] = 'feed_one'
            APP_STATE['last_error_command_id'] = APP_STATE['esp32_last_feed_command_id']
            if APP_STATE['status'] != 'error':
                APP_STATE['status'] = 'waiting_fruit'
                APP_STATE['message'] = '進料未確認；等待 HC-SR04 接手，請勿重新送料。'
        _sync_active_state_with_filesystem()
        payload = _esp32_command_payload()
        _mark_sorter_running_if_dispatched(payload)

    if request.GET.get('format') == 'text':
        return HttpResponse(_format_command_text(payload), content_type='text/plain; charset=utf-8')
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def esp32_report_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    event = (data.get('event') or '').strip()
    command_id = _safe_int(data.get('command_id'))

    with STATE_LOCK:
        APP_STATE['last_esp32_report_at'] = _now_string()
        if event == 'timing_config_applied':
            try:
                payload = _handle_timing_config_applied(data)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(_compact_esp32_payload(payload))

        if event in (
            'feed_cycle_completed',
            'feeder_max_run_timeout',
            'feeder_sensor_unavailable',
            'feeder_sensor_not_clear',
        ):
            try:
                payload = _handle_feed_result(event, data, command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(_compact_esp32_payload(payload))

        if event == 'fruit_arrival_delayed':
            payload = _handle_fruit_arrival_delayed(command_id)
            return JsonResponse(_compact_esp32_payload(payload))

        if event == 'hcsr04_station_1_ready':
            payload = _handle_hcsr04_station_1_ready(data)
            return JsonResponse(_compact_esp32_payload(payload))

        if event == 'hcsr04_trigger':
            _record_transition('hcsr04_trigger_received')
            if not _auto_trigger_enabled():
                reason = _auto_trigger_disabled_reason()
                payload = _state_payload(extra={
                    'ok': True,
                    'event': event,
                    'ignored': True,
                    'reason': reason,
                })
                return JsonResponse(_compact_esp32_payload(payload))
            try:
                payload = _create_capture_session(source='esp32')
            except CaptureCommandError as exc:
                if exc.reason == 'active_fruit_exists':
                    payload = _state_payload(extra={
                        'ok': True,
                        'event': event,
                        'ignored': True,
                        'reason': exc.reason,
                    })
                    return JsonResponse(_compact_esp32_payload(payload))
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            payload.update({'ok': True, 'event': event})
            return JsonResponse(_compact_esp32_payload(payload))

        if event.startswith('station_') and event.endswith('_ready'):
            station_index = _station_from_ready_event(event)
            if station_index is None:
                return _json_error('station ready 事件格式不正確。', status=400, reason='invalid_event')
            try:
                payload = _handle_station_ready(station_index, command_id=command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(_compact_esp32_payload(payload))

        if event == 'capture_sequence_finished':
            try:
                payload = _handle_sequence_finished(command_id=command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(_compact_esp32_payload(payload))

        if event in ('classification_sorter_completed', 'classification_sorter_failed'):
            try:
                payload = _handle_sorter_report(event, data, command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(_compact_esp32_payload(payload))

        if event == 'motor_error':
            message = data.get('message') or 'ESP32 回報馬達動作失敗。'
            _set_error_state('motor_error', message)
            return JsonResponse(_compact_esp32_payload(
                _state_payload(extra={'ok': True, 'event': event}),
            ))

        return _json_error('未知的 ESP32 回報事件。', status=400, reason='unknown_event', extra={'ok': False})


@csrf_exempt
@require_POST
def recapture_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if _sorter_busy():
            return _json_error(
                '硬體分類器正在執行，完成或逾時前不可重新拍攝。',
                status=409,
                reason='classifier_busy',
            )
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重新拍攝。', status=409)
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可重新拍攝的資料。', status=409)

        _disable_auto_run()
        APP_STATE['auto_run_finishing'] = False
        if not _esp32_is_online():
            return _json_error(
                'ESP32 尚未連線或已超過 20 秒未輪詢，無法重新啟動三站流程。',
                status=503,
                reason='esp32_offline',
            )
        fruit_dir = _temp_dir() / fruit_id
        if not fruit_dir.exists():
            return _json_error('暫存資料夾不存在，請重新建立拍攝。', status=404)

        try:
            _clear_temp_images(fruit_dir)
        except DatasetFileBusyError as exc:
            return _json_error(str(exc), status=409)

        _start_new_capture_session(
            fruit_id,
            source='manual_recapture',
            status='waiting_esp32_start',
            message=f'已重新啟動 {fruit_id}，等待 ESP32 開始三站閘門流程。',
        )
        _set_motor_command('start_sequence', station_index=1)
        payload = _state_payload(extra={'recaptured_fruit_id': fruit_id})
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def capture_started_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    with STATE_LOCK:
        fruit_id = data.get('fruit_id')
        capture_token = _safe_int(data.get('capture_token'))
        station_index = _safe_int(data.get('station_index'))
        if fruit_id != APP_STATE['active_fruit_id'] or capture_token != APP_STATE['capture_token']:
            return _json_error(
                '拍攝命令已過期，請重新拍攝。',
                status=409,
                reason='capture_command_mismatch',
                extra=_capture_started_debug_payload(fruit_id, capture_token, station_index),
            )
        if station_index != APP_STATE.get('active_station_index'):
            return _json_error(
                '拍攝站點與目前狀態不符，請等待 Django 下一次拍攝請求。',
                status=409,
                reason='station_mismatch',
                extra=_capture_started_debug_payload(fruit_id, capture_token, station_index),
            )
        if APP_STATE['status'] not in ('waiting_camera', 'uploading', 'waiting_motor'):
            return _json_error(
                '目前狀態不是等待手機拍攝，請勿上傳照片。',
                status=409,
                reason='capture_not_requested',
                extra=_capture_started_debug_payload(fruit_id, capture_token, station_index),
            )

        APP_STATE['capture_started_at'] = _now_string()
        APP_STATE['capture_started_monotonic'] = time.monotonic()
        command_created = APP_STATE.get('command_created_monotonic')
        if command_created is not None:
            APP_STATE['command_to_phone_start_ms'] = round(
                (APP_STATE['capture_started_monotonic'] - command_created) * 1000,
                1,
            )
        _apply_client_timing(data.get('client_timing'))
        _print_timing_log(
            'phone_capture_started',
            fruit_id=fruit_id,
            station_index=station_index,
            command_to_phone_start_ms=APP_STATE['command_to_phone_start_ms'],
        )
        _record_transition(
            'phone_capture_started',
            fruit_id=fruit_id,
            station_index=station_index,
            details={'command_to_phone_start_ms': APP_STATE['command_to_phone_start_ms']},
        )
        if APP_STATE['status'] == 'waiting_camera':
            APP_STATE['message'] = f'{fruit_id} 第 {station_index} 站手機端已開始拍攝。'
        payload = {
            'status': 'success',
            'timing': _timing_payload(),
            'capture_still_requested': APP_STATE['status'] == 'waiting_camera' and APP_STATE['pending_capture'],
        }
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def upload_images_api(request):
    _ensure_dataset_structure()
    image_file = request.FILES.get('image')
    image_files = request.FILES.getlist('images')
    if image_file is None and len(image_files) == 1:
        image_file = image_files[0]
    if image_file is None:
        return _json_error('每次只能上傳當站的單張照片，欄位名稱請使用 image。', status=400, reason='missing_image')

    with STATE_LOCK:
        active_fruit_id = APP_STATE['active_fruit_id']
        if not active_fruit_id:
            return _json_error('目前沒有等待上傳的 fruit 資料夾。', status=409)
        request_fruit_id = request.POST.get('fruit_id')
        request_capture_token = _safe_int(request.POST.get('capture_token'))
        station_index = _safe_int(request.POST.get('station_index'))
        if request_fruit_id != active_fruit_id or request_capture_token != APP_STATE['capture_token']:
            return _json_error('上傳命令已過期，請重新拍攝。', status=409)
        if station_index != APP_STATE.get('active_station_index'):
            return _json_error('上傳站點與目前拍攝請求不符。', status=409, reason='station_mismatch')
        if APP_STATE['status'] != 'waiting_camera' or not APP_STATE['pending_capture']:
            return _json_error('目前沒有開放手機上傳照片。', status=409, reason='capture_not_requested')
        fruit_dir = _temp_dir() / active_fruit_id
        if not fruit_dir.exists():
            return _json_error('暫存資料夾不存在，請重新拍攝。', status=404)
        target_filename = IMAGE_FILENAMES[station_index - 1]
        if (fruit_dir / target_filename).exists():
            return _json_error(f'第 {station_index} 站照片已存在，請勿重複上傳。', status=409, reason='duplicate_station_image')
        APP_STATE['pending_capture'] = False
        APP_STATE['status'] = 'uploading'
        APP_STATE['message'] = f'{active_fruit_id} 正在接收第 {station_index} 站照片，請勿刪除或重置。'

    try:
        _save_station_image(fruit_dir, station_index, image_file)
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            if (
                APP_STATE['active_fruit_id'] == active_fruit_id
                and APP_STATE['capture_token'] == request_capture_token
            ):
                APP_STATE['pending_capture'] = True
                APP_STATE['status'] = 'waiting_camera'
                _start_wait_timer()
                APP_STATE['message'] = f'{active_fruit_id} 第 {station_index} 站上傳失敗，手機端可重試。{exc}'
        return _json_error(f'照片上傳失敗：{exc}', status=409, reason='upload_failed')

    with STATE_LOCK:
        if active_fruit_id != APP_STATE['active_fruit_id'] or request_capture_token != APP_STATE['capture_token']:
            return _json_error('上傳完成時拍攝命令已過期，請重新拍攝。', status=409)
        _apply_capture_meta(request.POST.get('capture_meta'))
        APP_STATE['pending_capture'] = False
        upload_received_monotonic = time.monotonic()
        APP_STATE['upload_received_at'] = _now_string()
        capture_started_monotonic = APP_STATE.get('capture_started_monotonic')
        command_created_monotonic = APP_STATE.get('command_created_monotonic')
        capture_to_upload_ms = None
        command_to_upload_ms = None
        if capture_started_monotonic is not None:
            capture_to_upload_ms = round((upload_received_monotonic - capture_started_monotonic) * 1000, 1)
        if command_created_monotonic is not None:
            command_to_upload_ms = round((upload_received_monotonic - command_created_monotonic) * 1000, 1)
        _print_timing_log(
            'upload_received_at',
            fruit_id=active_fruit_id,
            station_index=station_index,
            capture_to_upload_ms=capture_to_upload_ms,
            command_to_upload_ms=command_to_upload_ms,
        )
        _record_transition(
            'station_image_saved',
            fruit_id=active_fruit_id,
            station_index=station_index,
            details={
                'capture_to_upload_ms': capture_to_upload_ms,
                'command_to_upload_ms': command_to_upload_ms,
            },
        )
        APP_STATE['station_statuses'][str(station_index)] = 'captured'
        _set_motor_command('release_gate', station_index=station_index)
        APP_STATE['status'] = 'waiting_motor'
        APP_STATE['message'] = f'{active_fruit_id} 第 {station_index} 站照片已保存，等待 ESP32 放行第 {station_index} 閘門。'
        payload = _state_payload(extra={
            'uploaded_fruit_id': active_fruit_id,
            'station_index': station_index,
        })
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def classify_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    label = data.get('label')
    note = (data.get('note') or '').strip()
    if label not in LABELS:
        return _json_error(
            '分類必須是上等、中等、下等或加工。',
            status=400,
            reason='invalid_label',
        )

    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可分類的暫存資料。', status=409)

        src_dir = _temp_dir() / fruit_id
        if not src_dir.exists():
            return _json_error('暫存資料夾不存在，請重新拍攝。', status=404)
        if APP_STATE['status'] != 'uploaded' or _temp_image_count(src_dir) != IMAGE_COUNT:
            return _json_error(f'{fruit_id} 尚未完成 {IMAGE_COUNT} 張照片上傳。', status=409)

        dest_dir = _dataset_root() / label / fruit_id
        if dest_dir.exists():
            if _is_ignored_reset_path(dest_dir):
                moved_warning = _move_reset_leftover_to_pending(dest_dir, reason='classify')
                if not dest_dir.exists():
                    _remove_ignored_reset_path(dest_dir)
                else:
                    return _json_error(moved_warning, status=409)
            else:
                return _json_error(f'{label}/{fruit_id} 已存在，請調整 counter 或先整理資料夾。', status=409)
        if dest_dir.exists():
            return _json_error(f'{label}/{fruit_id} 已存在，請調整 counter 或先整理資料夾。', status=409)
        relative_path = f'{label}/{fruit_id}'
        capture_time = APP_STATE['capture_time'] or _now_string()
        operation_token = APP_STATE.begin_dataset_operation('classify', fruit_id)
        APP_STATE['message'] = f'{fruit_id} 正在分類為「{label}」。'

    try:
        _safe_move(src_dir, dest_dir)
        _append_metadata(fruit_id, label, capture_time, relative_path, note, dest_dir)
        fruit_number = _fruit_number(fruit_id)
        if fruit_number is not None:
            _write_counter(max(_read_counter(), fruit_number + 1))
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            APP_STATE.finish_dataset_operation(operation_token)
            APP_STATE['message'] = f'{fruit_id} 分類失敗，可稍後重試。'
        return _json_error(str(exc), status=409, reason='dataset_file_busy')

    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return _json_error('分類完成時狀態已變更，已保留資料供人工確認。', status=409, reason='stale_operation')
        if APP_STATE.get('active_fruit_id') != fruit_id:
            APP_STATE.finish_dataset_operation(operation_token)
            return _json_error('分類完成時 fruit 已變更，已保留資料供人工確認。', status=409, reason='stale_operation')
        APP_STATE.finish_dataset_operation(operation_token)
        _clear_classified_dataset_state(fruit_id, label)
        _record_transition('fruit_classified', fruit_id=fruit_id, details={'label': label})
        sorter_result = _queue_sorter_command(fruit_id, label)
        payload = {
            'status': 'success',
            'data_classified': True,
            'fruit_id': fruit_id,
            'label': label,
            'path': relative_path,
            'next_fruit_id': _format_fruit_id(_read_counter()),
            **sorter_result,
        }
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def discard_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再刪除。', status=409)
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可刪除的暫存資料。', status=409)

        _disable_auto_run()
        APP_STATE['auto_run_finishing'] = False
        fruit_dir = _temp_dir() / fruit_id
        operation_token = APP_STATE.begin_dataset_operation('discard', fruit_id)
        APP_STATE['message'] = f'{fruit_id} 正在刪除或隔離。'

    try:
        discard_result = _discard_temp_fruit(fruit_dir, fruit_id)
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            APP_STATE.finish_dataset_operation(operation_token)
            APP_STATE['message'] = f'{fruit_id} 刪除失敗，可稍後重試。'
        return _json_error(str(exc), status=409, reason='dataset_file_busy')
    discard_mode = discard_result['mode']
    if discard_mode == 'deleted':
        message = f'{fruit_id} 已刪除，counter 不會自動增加。'
    elif discard_mode == 'quarantined':
        message = f'{fruit_id} 暫時無法刪除，已隔離到 _delete_pending，現在可繼續拍攝。'
    else:
        message = (
            f'{fruit_id} 暫時被其他程式占用，已標記為待清理並跳過此 ID；'
            f'下一筆將從 {discard_result["next_fruit_id"]} 開始。'
        )

    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return _json_error('刪除完成時狀態已變更。', status=409, reason='stale_operation')
        APP_STATE.finish_dataset_operation(operation_token)
        _clear_active_state(message, status='idle')
        _record_transition(
            'fruit_discarded',
            fruit_id=fruit_id,
            details={'discard_mode': discard_mode},
        )
        payload = _state_payload(extra={
            'discarded_fruit_id': fruit_id,
            'discard_mode': discard_mode,
            'discard_cleanup_pending': discard_mode != 'deleted',
            'discard_cleanup_path': discard_result.get('cleanup_path'),
            'next_fruit_id': discard_result.get('next_fruit_id') or _format_fruit_id(_read_counter()),
        })
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def reset_dataset_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重置 dataset。', status=409)
        if _sorter_busy():
            return _json_error(
                '硬體分類器正在執行，完成或逾時前不可重置 dataset。',
                status=409,
                reason='classifier_busy',
            )
        operation_token = APP_STATE.begin_dataset_operation('reset', APP_STATE.get('active_fruit_id'))
        APP_STATE['message'] = '正在重置 dataset。'

    try:
        delete_warnings = _reset_dataset_contents()
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            APP_STATE.finish_dataset_operation(operation_token)
            APP_STATE['message'] = 'dataset 重置失敗，可稍後重試。'
        return _json_error(str(exc), status=409, reason='dataset_file_busy')

    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return _json_error('重置完成時狀態已變更。', status=409, reason='stale_operation')
        APP_STATE.finish_dataset_operation(operation_token)
        _clear_active_state('dataset 已重置，下一筆資料將從 fruit_001 開始。', status='idle')
        _reset_sorter_state()
        APP_STATE['capture_token'] = 0
        APP_STATE['transition_trace'] = []
        APP_STATE['trace_sequence'] = 0
        _reset_timing_state()
        _record_transition('dataset_reset', details={'delete_warning_count': len(delete_warnings)})
        payload = _state_payload(extra={'reset_done': True, 'delete_warnings': delete_warnings})
    return JsonResponse(payload)


@require_GET
def dataset_image_api(request, fruit_id, filename):
    if not FRUIT_ID_PATTERN.match(fruit_id) or filename not in IMAGE_FILENAMES:
        raise Http404('image not found')

    image_path = _find_fruit_image(fruit_id, filename)
    if not image_path:
        raise Http404('image not found')
    stat = image_path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    last_modified = http_date(stat.st_mtime)
    if request.headers.get('If-None-Match') == etag:
        response = HttpResponseNotModified()
    else:
        response = FileResponse(image_path.open('rb'), content_type='image/jpeg')
    response['ETag'] = etag
    response['Last-Modified'] = last_modified
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@require_GET
def open_dataset_folder_api(request):
    dataset_root = _dataset_root()
    _ensure_dataset_structure()
    if os.name == 'nt' and hasattr(os, 'startfile'):
        os.startfile(dataset_root)
        return JsonResponse({'status': 'success', 'path': str(dataset_root), 'opened': True})
    return JsonResponse({'status': 'success', 'path': str(dataset_root), 'opened': False})


@csrf_exempt
@require_POST
def webrtc_offer_api(request):
    data = _request_data(request)
    offer = data.get('offer') or data.get('description') or data
    if not _is_session_description(offer, 'offer'):
        return _json_error('WebRTC offer 格式不正確。', status=400)

    with STATE_LOCK:
        offer_id = webrtc_signaling.accept_offer(WEBRTC_STATE, offer, _now_string)
    return JsonResponse({'status': 'success', 'offer_id': offer_id})


@csrf_exempt
@require_POST
def webrtc_answer_api(request):
    data = _request_data(request)
    answer = data.get('answer') or data.get('description') or data
    if not _is_session_description(answer, 'answer'):
        return _json_error('WebRTC answer 格式不正確。', status=400)

    with STATE_LOCK:
        answer_id = webrtc_signaling.accept_answer(WEBRTC_STATE, answer, _now_string)
    return JsonResponse({'status': 'success', 'answer_id': answer_id})


@csrf_exempt
@require_POST
def webrtc_ice_api(request):
    data = _request_data(request)
    role = data.get('role')
    candidate = data.get('candidate')
    if role not in ('dashboard', 'camera'):
        return _json_error('role 必須是 dashboard 或 camera。', status=400)
    if not candidate:
        return JsonResponse({'status': 'success', 'ignored': True})

    with STATE_LOCK:
        webrtc_signaling.add_ice_candidate(
            WEBRTC_STATE,
            role=role,
            candidate=candidate,
            now_string=_now_string,
        )
    return JsonResponse({'status': 'success'})


@require_GET
def webrtc_state_api(request):
    with STATE_LOCK:
        dashboard_ice = WEBRTC_STATE['dashboard_ice']
        camera_ice = WEBRTC_STATE['camera_ice']
        dashboard_from = _ice_from_index(request.GET.get('dashboard_ice_from'), len(dashboard_ice))
        camera_from = _ice_from_index(request.GET.get('camera_ice_from'), len(camera_ice))
        known_offer_id = _safe_int(request.GET.get('known_offer_id'))
        known_answer_id = _safe_int(request.GET.get('known_answer_id'))
        payload = webrtc_signaling.build_state_payload(
            WEBRTC_STATE,
            dashboard_from=dashboard_from,
            camera_from=camera_from,
            known_offer_id=known_offer_id,
            known_answer_id=known_answer_id,
        )
    return JsonResponse(payload)


def reset_runtime_state_for_tests():
    with STATE_LOCK:
        APP_STATE.update({
            'pending_capture': False,
            'capture_token': 0,
            'active_fruit_id': None,
            'capture_time': None,
            'source': None,
            'active_station_index': None,
            'station_statuses': {},
            'motor_command': None,
            'motor_command_id': 0,
            'last_error_reason': None,
            'last_error_command': None,
            'last_error_command_id': None,
            'wait_started_at': None,
            'wait_started_monotonic': None,
            'last_esp32_poll_at': None,
            'last_esp32_poll_monotonic': None,
            'last_esp32_report_at': None,
            'command_created_at': None,
            'command_created_monotonic': None,
            'capture_started_at': None,
            'capture_started_monotonic': None,
            'command_to_phone_start_ms': None,
            'client_timing': {},
            'upload_received_at': None,
            'fast_path_trigger_id': None,
            'fast_path_fruit_id': None,
            'fast_path_capture_token': None,
            'state_revision': 0,
            'trace_sequence': 0,
            'transition_trace': [],
            'capture_timing': dict(CAPTURE_TIMING_RECOMMENDED),
            'capture_timing_revision': 1,
            'capture_timing_applied_revision': 0,
            'capture_timing_applied_at': None,
            'capture_timing_loaded_path': None,
            'capture_timing_warning': None,
            'auto_run_enabled': False,
            'auto_feed_pending': False,
            'auto_run_finishing': False,
            'auto_run_recovery_reason': None,
            'camera_last_live_frame_at': None,
            'camera_last_live_frame_monotonic': None,
            'esp32_boot_id': None,
            'esp32_feeder_capable': False,
            'esp32_feeder_state': None,
            'esp32_feeder_sensor_state': None,
            'esp32_last_feed_command_id': 0,
            'last_completed_feed_command_id': 0,
            'feeder_test_passed_revision': None,
            'feeder_test_result': None,
            'last_delayed_feed_command_id': 0,
            'discard_cleanup_last_monotonic': None,
            'dataset_operation': None,
            'sorter_status': 'idle',
            'sorter_command_id': None,
            'sorter_fruit_id': None,
            'sorter_label': None,
            'sorter_classification_code': None,
            'sorter_error': None,
            'sorter_started_monotonic': None,
            'sorter_deadline_monotonic': None,
            'status': 'idle',
            'message': '等待手機相機、ESP32 與送料校正完成。',
        })
        WEBRTC_STATE.update({
            'offer': None,
            'offer_id': 0,
            'answer': None,
            'answer_id': 0,
            'dashboard_ice': [],
            'camera_ice': [],
            'updated_at': None,
        })
        _reset_dataset_caches()


def _dataset_root():
    return Path(settings.DATASET_ROOT)


def _no_store_response(response):
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def _temp_dir():
    return _dataset_root() / 'temp'


def _counter_path():
    return _dataset_root() / 'counter.json'


def _capture_timing_path():
    return Path(settings.CAPTURE_TIMING_CONFIG_PATH)


def _legacy_capture_timing_path():
    return _dataset_root() / 'capture_timing.json'


def _metadata_path():
    return _dataset_root() / 'metadata.csv'


def _reset_state_path():
    return _dataset_root() / 'reset_state.json'


def _discard_state_path():
    return _dataset_root() / 'discard_state.json'


def _ensure_dataset_structure():
    _initialise_dataset_structure(_dataset_root())


@cache
def _initialise_dataset_structure(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'temp').mkdir(parents=True, exist_ok=True)
    for label in LABELS:
        (root / label).mkdir(parents=True, exist_ok=True)
    if not (root / 'counter.json').exists():
        _write_counter(1)
    _ensure_capture_timing_config()
    _ensure_metadata_header()


def _request_data(request):
    content_type = request.META.get('CONTENT_TYPE', '')
    if 'application/json' in content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


def _read_counter():
    return _load_counter(_dataset_root())


@cache
def _load_counter(root):
    try:
        with (Path(root) / 'counter.json').open('r', encoding='utf-8') as counter_file:
            data = json.load(counter_file)
        return max(int(data.get('next_id', 1)), 1)
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        _write_counter(1)
        return 1


def _write_counter(next_id):
    _counter_path().parent.mkdir(parents=True, exist_ok=True)
    with _counter_path().open('w', encoding='utf-8') as counter_file:
        json.dump({'next_id': int(next_id)}, counter_file, ensure_ascii=False, indent=2)
        counter_file.write('\n')
    _load_counter.cache_clear()


def _normalise_capture_timing(raw_timing, *, require_all):
    raw_timing = raw_timing if isinstance(raw_timing, dict) else {}
    raw_calibrated = raw_timing.get('feeder_calibrated')
    if raw_calibrated is None and require_all:
        raise CaptureCommandError(
            '缺少送料校正確認欄位：feeder_calibrated。',
            status=400,
            reason='capture_timing_field_missing',
        )
    if raw_calibrated not in (True, False, 0, 1):
        raise CaptureCommandError(
            'feeder_calibrated 必須是布林值。',
            status=400,
            reason='capture_timing_invalid_value',
        )
    try:
        timing = capture_timing.normalise(
            raw_timing,
            fields=CAPTURE_TIMING_FIELDS,
            require_all=require_all,
            default_step=CAPTURE_TIMING_STEP_MS,
            default_minimum=CAPTURE_TIMING_MIN_MS,
            default_maximum=CAPTURE_TIMING_MAX_MS,
            field_limits=CAPTURE_TIMING_FIELD_LIMITS,
            field_steps=CAPTURE_TIMING_FIELD_STEPS,
            field_units=CAPTURE_TIMING_FIELD_UNITS,
        )
    except capture_timing.TimingValidationError as exc:
        raise CaptureCommandError(exc.message, status=400, reason=exc.reason) from None
    if timing['feeder_drive_us'] == timing['feeder_stop_us']:
        raise CaptureCommandError(
            'feeder_drive_us 不得與 feeder_stop_us 相同。',
            status=400,
            reason='feeder_drive_matches_stop',
        )
    timing['feeder_calibrated'] = bool(raw_calibrated)
    return timing


def _read_capture_timing_config(path):
    try:
        return capture_timing.read(
            Path(path),
            lambda raw: _normalise_capture_timing(raw, require_all=True),
            migration_defaults={
                'idle_command_poll_interval_ms': IDLE_COMMAND_POLL_INTERVAL_MS,
                'feeder_stop_us': FEEDER_STOP_US,
                'feeder_drive_us': FEEDER_DRIVE_US,
                'feeder_max_run_ms': FEEDER_MAX_RUN_MS,
                'feeder_calibrated': False,
            },
            migrate_profile=_migrate_feeder_profile,
        )
    except CaptureCommandError:
        return None


def _migrate_feeder_profile(timing):
    if not ({'feeder_run_ms', 'fruit_arrival_warning_ms'} & timing.keys()):
        return timing, False
    timing.pop('feeder_run_ms', None)
    timing.pop('fruit_arrival_warning_ms', None)
    timing['feeder_max_run_ms'] = FEEDER_MAX_RUN_MS
    timing['feeder_calibrated'] = False
    return timing, True


def _ensure_capture_timing_config():
    path = _capture_timing_path()
    path_key = str(path)
    if APP_STATE.get('capture_timing_loaded_path') == path_key:
        return

    path_existed = path.exists()
    loaded_config = _read_capture_timing_config(path)
    needs_write = False
    warning = None
    if loaded_config is None:
        loaded_config = _read_capture_timing_config(_legacy_capture_timing_path())
        if loaded_config is None:
            loaded_config = (dict(CAPTURE_TIMING_RECOMMENDED), 1, False)
            warning = (
                '送料 runtime profile 損壞或驗證失敗，已載入推薦值並取消校正。'
                if path_existed
                else '送料 runtime profile 遺失，已載入推薦值並取消校正。'
            )
        needs_write = True

    timing, revision, migrated = loaded_config
    if migrated:
        revision += 1
        needs_write = True
    if needs_write:
        capture_timing.write(_capture_timing_path(), timing, revision)
    APP_STATE['capture_timing'] = timing
    APP_STATE['capture_timing_revision'] = revision
    APP_STATE['capture_timing_applied_revision'] = 0
    APP_STATE['capture_timing_applied_at'] = None
    APP_STATE['capture_timing_loaded_path'] = path_key
    APP_STATE['capture_timing_warning'] = warning
    _remove_legacy_capture_timing_config()


def _remove_legacy_capture_timing_config():
    legacy_path = _legacy_capture_timing_path()
    if legacy_path == _capture_timing_path() or not legacy_path.exists():
        return
    try:
        _safe_unlink(legacy_path)
    except DatasetFileBusyError as exc:
        print(f'無法移除舊版停穩設定檔 {legacy_path}：{exc}', flush=True)


def _capture_timing_status():
    if APP_STATE['capture_timing_applied_revision'] == APP_STATE['capture_timing_revision']:
        return 'applied'
    if not _esp32_is_online():
        return 'waiting_esp32'
    return 'pending_esp32_apply'


def _capture_timing_state_payload():
    return {
        'capture_timing': dict(APP_STATE['capture_timing']),
        'capture_timing_recommended': dict(CAPTURE_TIMING_RECOMMENDED),
        'capture_timing_revision': APP_STATE['capture_timing_revision'],
        'capture_timing_applied_revision': APP_STATE['capture_timing_applied_revision'],
        'capture_timing_applied_at': APP_STATE['capture_timing_applied_at'],
        'capture_timing_status': _capture_timing_status(),
    }


def _format_fruit_id(number):
    return f'fruit_{int(number):03d}'


def _fruit_number(fruit_id):
    match = FRUIT_ID_PATTERN.match(fruit_id or '')
    return int(match.group(1)) if match else None


def _next_available_number(start_number):
    number = max(int(start_number), 1)
    while _fruit_id_exists(_format_fruit_id(number)):
        number += 1
    return number


def _fruit_id_exists(fruit_id):
    temp_fruit_dir = _temp_dir() / fruit_id
    if temp_fruit_dir.exists() and _is_deferred_discard_path(temp_fruit_dir):
        return True
    if (
        temp_fruit_dir.exists()
        and not _is_ignored_reset_path(temp_fruit_dir)
        and not _is_reusable_temp_dir(temp_fruit_dir)
    ):
        return True
    return any(
        (_dataset_root() / label / fruit_id).exists()
        and not _is_ignored_reset_path(_dataset_root() / label / fruit_id)
        for label in LABELS
    )


def _find_fruit_image(fruit_id, filename):
    candidates = [_temp_dir() / fruit_id / filename]
    candidates.extend(_dataset_root() / label / fruit_id / filename for label in LABELS)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _build_image_list(fruit_id):
    return [dict(image) for image in _load_image_list(_dataset_root(), fruit_id)]


@cache
def _load_image_list(root, fruit_id):
    images = []
    for filename in IMAGE_FILENAMES:
        image_path = _find_fruit_image(fruit_id, filename)
        if image_path:
            images.append({
                'filename': filename,
                'url': f'/api/image/{fruit_id}/{filename}/?v={int(image_path.stat().st_mtime)}',
            })
    return tuple(images)


def _reset_dataset_caches():
    _initialise_dataset_structure.cache_clear()
    _load_counter.cache_clear()
    _load_image_list.cache_clear()


def _ensure_metadata_header():
    path = _metadata_path()
    if not path.exists():
        with path.open('w', encoding='utf-8-sig', newline='') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
            writer.writeheader()
        return

    with path.open('r', encoding='utf-8-sig', newline='') as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames == METADATA_FIELDNAMES:
            return
        rows = list(reader)

    upgraded_rows = []
    for row in rows:
        upgraded_rows.append({
            'fruit_id': row.get('fruit_id', ''),
            'label': row.get('label', ''),
            'capture_time': row.get('capture_time', ''),
            'path': row.get('path', ''),
            'capture_count': row.get('capture_count') or '',
            'station_01_ok': row.get('station_01_ok') or '',
            'station_02_ok': row.get('station_02_ok') or '',
            'station_03_ok': row.get('station_03_ok') or '',
            'note': row.get('note', ''),
        })

    with path.open('w', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
        writer.writeheader()
        writer.writerows(upgraded_rows)


def _append_metadata(fruit_id, label, capture_time, relative_path, note, fruit_dir):
    _ensure_metadata_header()
    station_ok = [
        str((Path(fruit_dir) / filename).exists()).lower()
        for filename in IMAGE_FILENAMES
    ]
    with _metadata_path().open('a', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
        writer.writerow({
            'fruit_id': fruit_id,
            'label': label,
            'capture_time': capture_time,
            'path': relative_path,
            'capture_count': _temp_image_count(fruit_dir),
            'station_01_ok': station_ok[0],
            'station_02_ok': station_ok[1],
            'station_03_ok': station_ok[2],
            'note': note,
        })


def _create_capture_session(source):
    _sync_active_state_with_filesystem()
    if _sorter_busy():
        raise CaptureCommandError(
            '硬體分類器正在執行，完成或逾時前不可開始下一次拍攝。',
            reason='classifier_busy',
        )
    if APP_STATE.get('motor_command'):
        raise CaptureCommandError(
            '單一 motor command slot 目前已有命令，不能開始拍攝。',
            reason='sorter_command_slot_busy',
        )
    if APP_STATE['active_fruit_id']:
        raise CaptureCommandError(
            '目前已有未分類的暫存資料，請先分類或刪除。',
            reason='active_fruit_exists',
        )

    fruit_number = _next_available_number(_read_counter())
    fruit_id = _format_fruit_id(fruit_number)
    fruit_dir = _prepare_fruit_dir(fruit_id, source)

    if source == 'esp32':
        message = f'ESP32 已觸發 {fruit_id}，等待 ESP32 輪詢 start_sequence。'
    else:
        message = f'已建立 {fruit_id}，等待 ESP32 輪詢開始三站流程。'
    _start_new_capture_session(fruit_id, source, 'waiting_esp32_start', message)
    _set_motor_command('start_sequence', station_index=1)
    if source == 'esp32':
        # The report receipt was logged before creating the session, which
        # resets the per-fruit trace. Record it again on the new fruit so the
        # legacy path retains the same sensor-to-save trace contract as the
        # guarded station-1 shortcut.
        _record_transition('hcsr04_trigger_received', fruit_id=fruit_id)
        _record_transition('hcsr04_trigger_accepted', fruit_id=fruit_id)
    return _state_payload(extra={
        'status': 'success',
        'fruit_id': fruit_id,
        'capture_token': APP_STATE['capture_token'],
        'source': source,
    })


def _handle_hcsr04_station_1_ready(data):
    """Accept the guarded HC-SR04 shortcut without a start-command round trip.

    The ESP32 must prove that all gates are home and the fruit is stationary at
    station 1.  A network timeout can cause this exact report to be sent again;
    the stored trigger id then returns the same session instead of allocating a
    second fruit directory.
    """
    trigger_id = _normalise_trigger_id(data.get('trigger_id'))
    gates_home = _safe_bool(data.get('gates_home'))
    station_settled = _safe_bool(data.get('station_settled'))
    reported_station = _safe_int(data.get('station_index'))
    _record_transition(
        'hcsr04_station_1_ready_received',
        trigger_id=trigger_id,
        station_index=reported_station,
        details={
            'gates_home': gates_home,
            'station_settled': station_settled,
        },
    )

    if not trigger_id:
        return _fast_path_fallback(
            trigger_id=None,
            reason='missing_trigger_id',
            message='Fast station-1 report requires trigger_id.',
        )
    if not _fast_path_enabled():
        return _fast_path_fallback(
            trigger_id=trigger_id,
            reason='fast_path_disabled',
            message='Fast station-1 path is disabled; use the legacy trigger handshake.',
        )
    if not gates_home or not station_settled or reported_station != 1:
        return _fast_path_fallback(
            trigger_id=trigger_id,
            reason='fast_path_unsafe',
            message='Fast station-1 safety proof is incomplete.',
        )

    if (
        APP_STATE.get('fast_path_trigger_id') == trigger_id
        and APP_STATE.get('active_fruit_id') == APP_STATE.get('fast_path_fruit_id')
    ):
        _record_transition(
            'hcsr04_station_1_ready_duplicate',
            trigger_id=trigger_id,
            fruit_id=APP_STATE.get('fast_path_fruit_id'),
            station_index=1,
            details={'retry': True},
        )
        return _fast_path_payload(trigger_id, duplicate=True)

    if not _auto_trigger_enabled():
        return _fast_path_ignored(
            trigger_id=trigger_id,
            reason=_auto_trigger_disabled_reason(),
            message='Fast station-1 path is currently locked; wait for sensor rearm.',
        )

    try:
        fruit_number = _next_available_number(_read_counter())
        fruit_id = _format_fruit_id(fruit_number)
        _prepare_fruit_dir(fruit_id, source='esp32_fast_station_1')
    except CaptureCommandError as exc:
        return _fast_path_fallback(
            trigger_id=trigger_id,
            reason=exc.reason,
            message='Fast station-1 session could not be prepared; use the legacy trigger handshake.',
        )

    _start_new_capture_session(
        fruit_id,
        source='esp32_fast_station_1',
        status='waiting_camera',
        message=f'{fruit_id} fast station 1 is ready; waiting for one camera upload.',
    )
    # ``_start_new_capture_session`` starts a per-fruit trace, so re-add the
    # sensor event after the reset to retain the full sensor-to-save timeline.
    _record_transition(
        'hcsr04_station_1_ready_received',
        fruit_id=fruit_id,
        station_index=1,
        trigger_id=trigger_id,
        details={'gates_home': True, 'station_settled': True},
    )
    APP_STATE['fast_path_trigger_id'] = trigger_id
    APP_STATE['fast_path_fruit_id'] = fruit_id
    capture_session.request_station_capture(
        APP_STATE,
        fruit_id=fruit_id,
        station_index=1,
        now_string=_now_string,
        monotonic=time.monotonic,
        increment_token=False,
    )
    APP_STATE['fast_path_capture_token'] = APP_STATE['capture_token']
    _start_wait_timer()
    _record_transition(
        'fast_path_station_1_capture_requested',
        fruit_id=fruit_id,
        station_index=1,
        trigger_id=trigger_id,
        details={'gates_home': True, 'station_settled': True},
    )
    _print_timing_log(
        'fast_path_station_1_requested',
        fruit_id=fruit_id,
        capture_token=APP_STATE['capture_token'],
        trigger_id=trigger_id,
    )
    return _fast_path_payload(trigger_id, duplicate=False)


def _fast_path_payload(trigger_id, duplicate):
    fruit_id = APP_STATE.get('fast_path_fruit_id') or APP_STATE.get('active_fruit_id')
    capture_token = APP_STATE.get('fast_path_capture_token') or APP_STATE.get('capture_token')
    return _state_payload(extra={
        'ok': True,
        'event': 'hcsr04_station_1_ready',
        'accepted': True,
        'fast_path': True,
        'duplicate': duplicate,
        'trigger_id': trigger_id,
        'fruit_id': fruit_id,
        'capture_token': capture_token,
        'station_index': 1,
        'capture_requested': bool(APP_STATE.get('pending_capture')),
    })


def _fast_path_fallback(trigger_id, reason, message):
    _record_transition(
        'fast_path_fallback_to_legacy',
        trigger_id=trigger_id,
        details={'reason': reason},
    )
    return _state_payload(extra={
        'ok': False,
        'event': 'hcsr04_station_1_ready',
        'accepted': False,
        'fast_path': False,
        'fallback_to_legacy': True,
        'trigger_id': trigger_id,
        'reason': reason,
        'message': message,
    })


def _fast_path_ignored(trigger_id, reason, message):
    """Return a successful no-op when auto capture is intentionally locked.

    A lock such as an unclassified temp fruit is not a protocol incompatibility.
    Returning ``fallback_to_legacy`` here would cause the ESP32 to send a second
    legacy trigger even though Django must reject it as well.
    """
    _record_transition(
        'fast_path_ignored',
        trigger_id=trigger_id,
        details={'reason': reason},
    )
    return _state_payload(extra={
        'ok': True,
        'event': 'hcsr04_station_1_ready',
        'accepted': False,
        'fast_path': False,
        'fallback_to_legacy': False,
        'ignored': True,
        'trigger_id': trigger_id,
        'reason': reason,
        'message': message,
    })


def _fast_path_enabled():
    return bool(getattr(settings, 'ENABLE_AUTO_STATION_1_FAST_PATH', True))


def _prepare_fruit_dir(fruit_id, source):
    fruit_dir = _temp_dir() / fruit_id
    if fruit_dir.exists() and _is_ignored_reset_path(fruit_dir):
        moved_warning = _move_reset_leftover_to_pending(fruit_dir, reason=source)
        if fruit_dir.exists():
            raise CaptureCommandError(
                moved_warning,
                reason='reset_leftover_busy',
            )
    if fruit_dir.exists() and not _is_reusable_temp_dir(fruit_dir):
        raise CaptureCommandError(
            f'{fruit_id} temp 資料夾已有內容，請先分類、刪除或 reset dataset。',
            reason='temp_folder_not_reusable',
        )
    fruit_dir.mkdir(parents=True, exist_ok=True)
    try:
        _clear_temp_images(fruit_dir)
    except DatasetFileBusyError as exc:
        raise CaptureCommandError(str(exc), reason='file_busy') from exc
    return fruit_dir


def _start_new_capture_session(fruit_id, source, status, message):
    APP_STATE['pending_capture'] = False
    APP_STATE['capture_token'] += 1
    APP_STATE['active_fruit_id'] = fruit_id
    APP_STATE['capture_time'] = _now_string()
    APP_STATE['source'] = source
    APP_STATE['active_station_index'] = None
    APP_STATE['station_statuses'] = {str(index): 'pending' for index in range(1, IMAGE_COUNT + 1)}
    APP_STATE['motor_command'] = None
    APP_STATE['last_error_reason'] = None
    APP_STATE['upload_received_at'] = None
    APP_STATE['fast_path_trigger_id'] = None
    APP_STATE['fast_path_fruit_id'] = None
    APP_STATE['fast_path_capture_token'] = None
    APP_STATE['transition_trace'] = []
    APP_STATE['trace_sequence'] = 0
    APP_STATE['status'] = status
    APP_STATE['message'] = message
    _reset_timing_state()
    _start_wait_timer()
    _record_transition(
        'capture_session_created',
        fruit_id=fruit_id,
        details={'source': source, 'status': status},
    )


def _clear_classified_dataset_state(fruit_id, label):
    existing_command = APP_STATE.get('motor_command')
    APP_STATE['pending_capture'] = False
    APP_STATE['active_fruit_id'] = None
    APP_STATE['capture_time'] = None
    APP_STATE['source'] = None
    APP_STATE['fast_path_trigger_id'] = None
    APP_STATE['fast_path_fruit_id'] = None
    APP_STATE['fast_path_capture_token'] = None
    APP_STATE['active_station_index'] = None
    APP_STATE['station_statuses'] = {}
    APP_STATE['last_error_reason'] = None
    APP_STATE['status'] = 'classified'
    APP_STATE['message'] = f'{fruit_id} 已分類為「{label}」。'
    if not existing_command:
        _clear_wait_timer()
        _reset_timing_state()


def _queue_sorter_command(fruit_id, label):
    classification_code = CLASSIFICATION_CODES[label]
    try:
        command = _set_motor_command(
            'classify_fruit',
            fruit_id=fruit_id,
            classification_code=classification_code,
        )
    except CaptureCommandError as exc:
        _set_sorter_failed(fruit_id, label, classification_code, exc.reason)
        return _sorter_queue_result(False)

    now = time.monotonic()
    APP_STATE['sorter_status'] = 'pending'
    APP_STATE['sorter_command_id'] = command['command_id']
    APP_STATE['sorter_fruit_id'] = fruit_id
    APP_STATE['sorter_label'] = label
    APP_STATE['sorter_classification_code'] = classification_code
    APP_STATE['sorter_error'] = None
    APP_STATE['sorter_started_monotonic'] = None
    APP_STATE['sorter_deadline_monotonic'] = now + SORTER_PENDING_TIMEOUT_SECONDS
    APP_STATE['message'] = f'{fruit_id} 資料已分類，等待 ESP32 執行硬體分類器。'
    _record_transition(
        'classification_sorter_queued',
        fruit_id=fruit_id,
        command_id=command['command_id'],
        details={'label': label, 'classification_code': classification_code},
    )
    return _sorter_queue_result(True)


def _set_sorter_failed(fruit_id, label, classification_code, reason):
    APP_STATE['sorter_status'] = 'failed'
    APP_STATE['sorter_command_id'] = None
    APP_STATE['sorter_fruit_id'] = fruit_id
    APP_STATE['sorter_label'] = label
    APP_STATE['sorter_classification_code'] = classification_code
    APP_STATE['sorter_error'] = reason
    APP_STATE['sorter_started_monotonic'] = None
    APP_STATE['sorter_deadline_monotonic'] = None
    APP_STATE['message'] = f'{fruit_id} 資料已分類，但硬體分類命令建立失敗：{reason}'
    _record_transition(
        'classification_sorter_queue_failed',
        fruit_id=fruit_id,
        details={'classification_code': classification_code, 'reason': reason},
    )


def _reset_sorter_state():
    APP_STATE['sorter_status'] = 'idle'
    APP_STATE['sorter_command_id'] = None
    APP_STATE['sorter_fruit_id'] = None
    APP_STATE['sorter_label'] = None
    APP_STATE['sorter_classification_code'] = None
    APP_STATE['sorter_error'] = None
    APP_STATE['sorter_started_monotonic'] = None
    APP_STATE['sorter_deadline_monotonic'] = None


def _sorter_queue_result(queued):
    return {
        'sorter_command_queued': bool(queued),
        **_sorter_state_payload(),
    }


def _sorter_state_payload():
    return {
        'sorter_status': APP_STATE.get('sorter_status', 'idle'),
        'sorter_command_id': APP_STATE.get('sorter_command_id'),
        'sorter_fruit_id': APP_STATE.get('sorter_fruit_id'),
        'sorter_label': APP_STATE.get('sorter_label'),
        'sorter_error': APP_STATE.get('sorter_error'),
        'sorter_busy': _sorter_busy(),
        'sorter_command_queued': bool(
            APP_STATE.get('sorter_command_id')
            and APP_STATE.get('sorter_status') in ('pending', 'running')
        ),
    }


def _sorter_busy():
    return APP_STATE.get('sorter_status') in ('pending', 'running')


def _mark_sorter_running_if_dispatched(payload):
    if payload.get('command') != 'classify_fruit':
        return
    if APP_STATE.get('sorter_status') != 'pending':
        return
    if payload.get('command_id') != APP_STATE.get('sorter_command_id'):
        return
    now = time.monotonic()
    APP_STATE['sorter_status'] = 'running'
    APP_STATE['sorter_started_monotonic'] = now
    APP_STATE['sorter_deadline_monotonic'] = now + SORTER_RUNNING_TIMEOUT_SECONDS
    APP_STATE['message'] = f'{APP_STATE.get("sorter_fruit_id")} 的硬體分類器執行中。'
    _record_transition(
        'classification_sorter_running',
        fruit_id=APP_STATE.get('sorter_fruit_id'),
        command_id=APP_STATE.get('sorter_command_id'),
    )


def _handle_sorter_report(event, data, command_id):
    expected_command_id = APP_STATE.get('sorter_command_id')
    classification_code = (data.get('classification_code') or '').strip()
    expected_code = APP_STATE.get('sorter_classification_code')
    terminal = APP_STATE.get('sorter_status') in ('completed', 'failed', 'timeout')
    if terminal and command_id == expected_command_id:
        return _state_payload(extra={
            'ok': True,
            'event': event,
            'ignored': True,
            'duplicate': True,
            'reason': 'sorter_terminal_state',
        })
    if command_id != expected_command_id or not expected_command_id:
        raise CaptureCommandError(
            '分類器回報的 command_id 與目前 sorter 命令不符。',
            reason='command_id_mismatch',
        )
    if classification_code != expected_code:
        raise CaptureCommandError(
            '分類器回報的 classification_code 與目前命令不符。',
            reason='classification_code_mismatch',
        )

    current_command = APP_STATE.get('motor_command') or {}
    if (
        current_command.get('command') != 'classify_fruit'
        or current_command.get('command_id') != command_id
    ):
        raise CaptureCommandError(
            '目前 motor command slot 不是這筆分類命令。',
            reason='sorter_command_slot_mismatch',
        )

    fruit_id = APP_STATE.get('sorter_fruit_id')
    APP_STATE['motor_command'] = None
    APP_STATE['sorter_deadline_monotonic'] = None
    if event == 'classification_sorter_completed':
        APP_STATE['sorter_status'] = 'completed'
        APP_STATE['sorter_error'] = None
        APP_STATE['status'] = 'idle'
        APP_STATE['auto_feed_pending'] = APP_STATE.get('auto_run_enabled', False)
        APP_STATE['auto_run_finishing'] = False
        APP_STATE['message'] = f'{fruit_id} 的硬體分類器控制流程已完成。'
    else:
        reason = (data.get('message') or 'classifier_failed').strip()
        APP_STATE['sorter_status'] = 'failed'
        APP_STATE['sorter_error'] = reason
        APP_STATE['message'] = f'{fruit_id} 的硬體分類器失敗：{reason}'
    _clear_wait_timer()
    _record_transition(
        event,
        fruit_id=fruit_id,
        command_id=command_id,
        details={'classification_code': classification_code, 'sorter_error': APP_STATE.get('sorter_error')},
    )
    _queue_pending_auto_feed()
    return _state_payload(extra={'ok': True, 'event': event})


def _set_motor_command(
    command,
    station_index=None,
    *,
    fruit_id=None,
    classification_code=None,
    feed_context=None,
):
    if APP_STATE.get('motor_command'):
        raise CaptureCommandError(
            '單一 motor command slot 目前已有命令，不能覆蓋。',
            reason='sorter_command_slot_busy',
        )

    try:
        command_id = motor_commands.allocate(
            Path(settings.MOTOR_COMMAND_SEQUENCE_PATH),
            APP_STATE.get('motor_command_id', 0),
        )
    except motor_commands.CommandSequenceError as exc:
        raise CaptureCommandError(
            str(exc),
            status=503,
            reason='motor_command_id_persist_failed',
        ) from exc

    timing = APP_STATE['capture_timing']
    APP_STATE['motor_command_id'] = command_id
    APP_STATE['motor_command'] = {
        'command_id': command_id,
        'command': command,
        'station_index': station_index,
        'fruit_id': fruit_id if fruit_id is not None else APP_STATE['active_fruit_id'],
        'home_angle': HOME_ANGLE,
        'release_angle': RELEASE_ANGLE,
        'timing_revision': APP_STATE['capture_timing_revision'],
        'first_station_settle_ms': timing['first_station_settle_ms'],
        'servo_settle_ms': timing['servo_settle_ms'],
        'fruit_settle_ms': timing['fruit_settle_ms'],
        'final_gate_return_delay_ms': timing['final_gate_return_delay_ms'],
        'idle_command_poll_interval_ms': timing['idle_command_poll_interval_ms'],
        'created_at': _now_string(),
    }
    if classification_code:
        APP_STATE['motor_command']['classification_code'] = classification_code
    if command == 'feed_one':
        APP_STATE['motor_command'].update({
            'feed_context': feed_context or 'production',
            'feeder_stop_us': timing['feeder_stop_us'],
            'feeder_drive_us': timing['feeder_drive_us'],
            'feeder_max_run_ms': timing['feeder_max_run_ms'],
        })
    _start_wait_timer()
    _record_transition(
        'motor_command_issued',
        station_index=station_index,
        command_id=command_id,
        details={'command': command},
    )
    return APP_STATE['motor_command']


def _handle_station_ready(station_index, command_id=None):
    fruit_id = APP_STATE['active_fruit_id']
    if not fruit_id:
        raise CaptureCommandError('目前沒有進行中的拍攝工作階段。', reason='no_active_session')
    if station_index < 1 or station_index > IMAGE_COUNT:
        raise CaptureCommandError('拍攝站點超出範圍。', status=400, reason='station_out_of_range')

    station_key = str(station_index)
    station_status = APP_STATE.setdefault('station_statuses', {}).get(station_key)
    if station_status in ('ready', 'captured'):
        return _state_payload(extra={
            'ok': True,
            'event': f'station_{station_index}_ready',
            'ignored': True,
            'duplicate': True,
            'reason': 'duplicate_station_ready',
        })

    expected_station = _expected_ready_station()
    if expected_station is None:
        raise CaptureCommandError(
            '目前已完成第 3 站拍攝，ESP32 應回報 capture_sequence_finished。',
            reason='sequence_finish_expected',
        )
    if expected_station != station_index:
        raise CaptureCommandError(
            f'ESP32 回報第 {station_index} 站就緒，但目前預期是第 {expected_station} 站。',
            reason='unexpected_station_ready',
        )

    current_command = APP_STATE.get('motor_command')
    if current_command and command_id != current_command.get('command_id'):
        raise CaptureCommandError('ESP32 回報的 command_id 與目前馬達命令不符。', reason='command_id_mismatch')

    capture_session.request_station_capture(
        APP_STATE,
        fruit_id=fruit_id,
        station_index=station_index,
        now_string=_now_string,
        monotonic=time.monotonic,
        increment_token=True,
    )
    _start_wait_timer()
    _record_transition(
        'station_capture_requested',
        fruit_id=fruit_id,
        station_index=station_index,
        command_id=command_id,
    )
    return _state_payload(extra={'ok': True, 'event': f'station_{station_index}_ready'})


def _expected_ready_station():
    if APP_STATE['status'] == 'waiting_station_ready':
        return 1
    if APP_STATE['status'] == 'waiting_esp32_start':
        return 1
    if APP_STATE['status'] == 'waiting_motor' and APP_STATE.get('motor_command'):
        released_station = APP_STATE['motor_command'].get('station_index')
        if int(released_station or 0) >= IMAGE_COUNT:
            return None
        return min(int(released_station or 0) + 1, IMAGE_COUNT)
    return APP_STATE.get('active_station_index') or 1


def _handle_sequence_finished(command_id=None):
    fruit_id = APP_STATE['active_fruit_id']
    if not fruit_id:
        raise CaptureCommandError('目前沒有進行中的拍攝工作階段。', reason='no_active_session')
    if APP_STATE['status'] == 'uploaded':
        return _state_payload(extra={
            'ok': True,
            'event': 'capture_sequence_finished',
            'ignored': True,
            'duplicate': True,
            'reason': 'sequence_already_finished',
        })
    current_command = APP_STATE.get('motor_command')
    if current_command and command_id != current_command.get('command_id'):
        raise CaptureCommandError('ESP32 完成回報的 command_id 與目前馬達命令不符。', reason='command_id_mismatch')

    fruit_dir = _temp_dir() / fruit_id
    if _temp_image_count(fruit_dir) != IMAGE_COUNT:
        raise CaptureCommandError(f'{fruit_id} 尚未完成 3 張照片，不能結束流程。', reason='capture_incomplete')

    APP_STATE['pending_capture'] = False
    APP_STATE['active_station_index'] = None
    APP_STATE['motor_command'] = None
    APP_STATE['status'] = 'uploaded'
    APP_STATE['message'] = f'{fruit_id} 三站照片已完成，請在 dashboard 確認後分類。'
    _clear_wait_timer()
    _record_transition(
        'capture_sequence_finished',
        fruit_id=fruit_id,
        command_id=command_id,
    )
    return _state_payload(extra={'ok': True, 'event': 'capture_sequence_finished'})


def _station_from_ready_event(event):
    match = re.match(r'^station_(\d+)_ready$', event)
    if not match:
        return None
    return _safe_int(match.group(1))


def _esp32_command_payload():
    auto_trigger_enabled = _auto_trigger_enabled()
    timing = APP_STATE['capture_timing']
    base_payload = {
        'status': 'success',
        'auto_trigger_enabled': 1 if auto_trigger_enabled else 0,
        'server_status': APP_STATE['status'],
    }
    command = None if APP_STATE.get('dataset_operation') else APP_STATE.get('motor_command')
    if command:
        if command['command'] == 'classify_fruit':
            return {
                **base_payload,
                'command': command['command'],
                'command_id': command['command_id'],
                'fruit_id': command['fruit_id'] or '',
                'classification_code': command.get('classification_code') or '',
                'message': APP_STATE['message'],
            }
        if command['command'] == 'feed_one':
            return {
                **base_payload,
                **command,
                'fruit_id': '',
                'message': APP_STATE['message'],
            }
        return {
            **base_payload,
            'command': command['command'],
            'command_id': command['command_id'],
            'station_index': command['station_index'] or 0,
            'fruit_id': command['fruit_id'] or '',
            'home_angle': command['home_angle'],
            'release_angle': command['release_angle'],
            'timing_revision': command.get('timing_revision', APP_STATE['capture_timing_revision']),
            'first_station_settle_ms': command.get('first_station_settle_ms', timing['first_station_settle_ms']),
            'servo_settle_ms': command.get('servo_settle_ms', timing['servo_settle_ms']),
            'fruit_settle_ms': command.get('fruit_settle_ms', timing['fruit_settle_ms']),
            'final_gate_return_delay_ms': command.get(
                'final_gate_return_delay_ms',
                timing['final_gate_return_delay_ms'],
            ),
            'idle_command_poll_interval_ms': command.get(
                'idle_command_poll_interval_ms',
                timing['idle_command_poll_interval_ms'],
            ),
            'message': APP_STATE['message'],
        }
    return {
        **base_payload,
        'command': 'none',
        'command_id': 0,
        'station_index': 0,
        'fruit_id': APP_STATE['active_fruit_id'] or '',
        'home_angle': HOME_ANGLE,
        'release_angle': RELEASE_ANGLE,
        'timing_revision': APP_STATE['capture_timing_revision'],
        'first_station_settle_ms': timing['first_station_settle_ms'],
        'servo_settle_ms': timing['servo_settle_ms'],
        'fruit_settle_ms': timing['fruit_settle_ms'],
        'final_gate_return_delay_ms': timing['final_gate_return_delay_ms'],
        'idle_command_poll_interval_ms': timing['idle_command_poll_interval_ms'],
        'feeder_stop_us': timing['feeder_stop_us'],
        'feeder_drive_us': timing['feeder_drive_us'],
        'feeder_max_run_ms': timing['feeder_max_run_ms'],
        'message': APP_STATE['message'],
    }


def _handle_feed_result(event, data, command_id):
    if command_id and command_id == APP_STATE.get('last_completed_feed_command_id'):
        return _state_payload(extra={
            'ok': True,
            'event': event,
            'ignored': True,
            'duplicate': True,
            'reason': 'duplicate_feed_report',
        })
    command = APP_STATE.get('motor_command') or {}
    if command.get('command') != 'feed_one':
        return _state_payload(extra={
            'ok': True,
            'event': event,
            'ignored': True,
            'reason': 'no_pending_feed_command',
        })
    if command_id != command.get('command_id'):
        raise CaptureCommandError(
            '送料完成回報的 command_id 與目前命令不符。',
            reason='command_id_mismatch',
        )
    APP_STATE['last_completed_feed_command_id'] = command_id
    APP_STATE['motor_command'] = None
    stop_reason = (data.get('feeder_stop_reason') or '').strip() or event
    result = {
        'event': event,
        'timing_revision': command.get('timing_revision'),
        'elapsed_ms': max(_safe_int(data.get('feeder_elapsed_ms')) or 0, 0),
        'max_run_ms': max(
            _safe_int(data.get('feeder_max_run_ms'))
            or command.get('feeder_max_run_ms')
            or 0,
            0,
        ),
        'stop_reason': stop_reason,
        'ok': event == 'feed_cycle_completed' and stop_reason == 'hcsr04',
    }
    APP_STATE['feeder_test_result'] = result
    APP_STATE['status'] = (
        'idle'
        if command.get('feed_context') == 'calibration'
        else ('idle' if result['ok'] else 'error')
    )
    APP_STATE['message'] = (
        'HC-SR04 已偵測百香果，測試送料正常停止。'
        if result['ok']
        else f'測試送料已停止：{result["stop_reason"]}。'
    )
    if result['ok'] and command.get('feed_context') == 'calibration':
        APP_STATE['feeder_test_passed_revision'] = command.get('timing_revision')
    elif not result['ok']:
        APP_STATE['feeder_test_passed_revision'] = None
        APP_STATE['last_error_reason'] = event
        APP_STATE['last_error_command'] = 'feed_one'
        APP_STATE['last_error_command_id'] = command_id
    _clear_wait_timer()
    _record_transition(
        event,
        command_id=command_id,
        details={**result, 'feed_context': command.get('feed_context')},
    )
    return _state_payload(extra={'ok': True, 'event': event})


def _handle_fruit_arrival_delayed(command_id):
    if command_id and command_id == APP_STATE.get('last_delayed_feed_command_id'):
        return _state_payload(extra={
            'ok': True,
            'event': 'fruit_arrival_delayed',
            'ignored': True,
            'duplicate': True,
            'reason': 'duplicate_fruit_arrival_delayed',
        })
    if APP_STATE.get('active_fruit_id'):
        return _state_payload(extra={
            'ok': True,
            'event': 'fruit_arrival_delayed',
            'ignored': True,
            'reason': 'fruit_already_detected',
        })
    if not command_id or command_id != APP_STATE.get('last_completed_feed_command_id'):
        return _state_payload(extra={
            'ok': True,
            'event': 'fruit_arrival_delayed',
            'ignored': True,
            'reason': 'stale_feed_command',
        })
    APP_STATE['last_error_reason'] = 'fruit_arrival_delayed'
    APP_STATE['last_error_command'] = 'feed_one'
    APP_STATE['last_error_command_id'] = command_id
    APP_STATE['last_delayed_feed_command_id'] = command_id
    APP_STATE['auto_run_recovery_reason'] = 'fruit_arrival_delayed'
    APP_STATE['status'] = 'waiting_fruit'
    APP_STATE['message'] = (
        '送料後仍未觸發 HC-SR04；進料未確認。請暫停並檢查送料區域，'
        '不要自動補轉。'
    )
    _record_transition(
        'fruit_arrival_delayed',
        command_id=command_id,
    )
    return _state_payload(extra={'ok': True, 'event': 'fruit_arrival_delayed'})


def _handle_timing_config_applied(data):
    revision = _safe_int(data.get('timing_revision'))
    if revision is None or revision < 1:
        raise CaptureCommandError(
            'ESP32 timing_config_applied 缺少有效 revision。',
            status=400,
            reason='invalid_timing_revision',
        )

    current_revision = APP_STATE['capture_timing_revision']
    if revision != current_revision:
        return _state_payload(extra={
            'ok': True,
            'event': 'timing_config_applied',
            'ignored': True,
            'reason': 'stale_or_unknown_timing_revision',
            'reported_timing_revision': revision,
        })

    was_applied = APP_STATE['capture_timing_applied_revision'] == revision
    APP_STATE['capture_timing_applied_revision'] = revision
    APP_STATE['capture_timing_applied_at'] = _now_string()
    if APP_STATE['status'] == 'idle':
        APP_STATE['message'] = f'ESP32 已套用拍攝停穩設定 revision {revision}。'
    if not was_applied:
        _record_transition(
            'timing_config_applied',
            details={'revision': revision},
        )
    return _state_payload(extra={
        'ok': True,
        'event': 'timing_config_applied',
        'timing_revision': revision,
    })


def _auto_trigger_enabled():
    if APP_STATE.get('dataset_operation'):
        return False
    if _sorter_busy():
        return False
    if APP_STATE.get('active_fruit_id'):
        return False
    if APP_STATE.get('motor_command'):
        return False
    return not _has_unclassified_temp_fruit()


def _auto_trigger_disabled_reason():
    if APP_STATE.get('dataset_operation'):
        return 'dataset_operation_in_progress'
    if _sorter_busy():
        return 'classifier_busy'
    motor_command = APP_STATE.get('motor_command') or {}
    if (
        APP_STATE.get('status') == 'waiting_esp32_start'
        and motor_command.get('command') == 'start_sequence'
    ):
        return 'duplicate_trigger_waiting_start_sequence'
    if APP_STATE.get('active_fruit_id'):
        return 'active_fruit_exists'
    if APP_STATE.get('motor_command'):
        return 'pending_motor_command'
    if _has_unclassified_temp_fruit():
        return 'temp_fruit_exists'
    return 'auto_trigger_disabled'


def _has_unclassified_temp_fruit():
    temp_dir = _temp_dir()
    if not temp_dir.exists():
        return False
    for fruit_dir in temp_dir.glob('fruit_*'):
        if not fruit_dir.is_dir() or not FRUIT_ID_PATTERN.match(fruit_dir.name):
            continue
        if _is_deferred_discard_path(fruit_dir):
            continue
        if _is_ignored_reset_path(fruit_dir):
            continue
        if not _is_reusable_temp_dir(fruit_dir):
            return True
    return False


def _format_command_text(payload):
    lines = []
    keys = [
        'status',
        'server_status',
        'auto_trigger_enabled',
        'command',
        'command_id',
        'fruit_id',
    ]
    if payload.get('command') == 'classify_fruit':
        keys.append('classification_code')
    else:
        keys.extend([
            'station_index',
            'home_angle',
            'release_angle',
            'timing_revision',
            'first_station_settle_ms',
            'servo_settle_ms',
            'fruit_settle_ms',
            'final_gate_return_delay_ms',
            'idle_command_poll_interval_ms',
            'feeder_stop_us',
            'feeder_drive_us',
            'feeder_max_run_ms',
        ])
        if payload.get('command') == 'feed_one':
            keys.append('feed_context')
    for key in keys:
        lines.append(f'{key}={payload.get(key, "")}')
    return '\n'.join(lines) + '\n'


def _esp32_is_online():
    last_poll = APP_STATE.get('last_esp32_poll_monotonic')
    return last_poll is not None and time.monotonic() - last_poll <= ESP32_ONLINE_WINDOW_SECONDS


def _feeder_test_disabled_reason():
    if APP_STATE.get('auto_run_enabled'):
        return 'auto_run_must_be_stopped'
    return _feeder_hardware_disabled_reason()


def _feeder_hardware_disabled_reason(*, allow_recovery=False):
    recovery = bool(APP_STATE.get('auto_run_recovery_reason'))
    if APP_STATE.get('dataset_operation'):
        return 'dataset_operation_in_progress'
    if _sorter_busy():
        return 'classifier_busy'
    if APP_STATE.get('active_fruit_id'):
        return 'active_fruit_exists'
    if APP_STATE.get('motor_command'):
        return 'pending_motor_command'
    if APP_STATE.get('status') != 'idle' and not (
        allow_recovery and recovery and APP_STATE.get('status') in ('waiting_fruit', 'error')
    ):
        return 'system_not_idle'
    if not _esp32_is_online():
        return 'esp32_offline'
    if not APP_STATE.get('esp32_feeder_capable'):
        return 'feeder_capability_missing'
    if APP_STATE.get('esp32_feeder_state') != 'idle' and not (
        allow_recovery and recovery and APP_STATE.get('esp32_feeder_state') == 'awaiting_fruit'
    ):
        return 'feeder_state_not_idle'
    if APP_STATE.get('esp32_feeder_sensor_state') == 'unavailable':
        return 'feeder_sensor_unavailable'
    if APP_STATE.get('esp32_feeder_sensor_state') != 'clear':
        return 'feeder_sensor_not_clear'
    if APP_STATE['capture_timing_applied_revision'] != APP_STATE['capture_timing_revision']:
        return 'feeder_timing_not_applied'
    return None


def _auto_run_disabled_reason(*, allow_recovery=False):
    return 'feeder_hardware_validation_required'


def _camera_is_ready():
    last_frame = APP_STATE.get('camera_last_live_frame_monotonic')
    return (
        last_frame is not None
        and time.monotonic() - last_frame <= CAMERA_READY_WINDOW_SECONDS
    )


def _queue_production_feed():
    command = _set_motor_command('feed_one', feed_context='production')
    APP_STATE['auto_feed_pending'] = False
    APP_STATE['status'] = 'waiting_feeder'
    APP_STATE['message'] = 'ESP32 正在送入下一顆百香果。'
    return command


def _queue_pending_auto_feed():
    if not APP_STATE.get('auto_run_enabled') or not APP_STATE.get('auto_feed_pending'):
        return False
    reason = _auto_run_disabled_reason()
    if reason:
        if reason == 'camera_not_ready':
            APP_STATE['message'] = '等待手機相機恢復有效即時畫面後再送入下一顆。'
        return False
    _queue_production_feed()
    return True


def _disable_auto_run():
    APP_STATE['auto_run_enabled'] = False
    APP_STATE['auto_feed_pending'] = False


def _start_wait_timer():
    APP_STATE['wait_started_at'] = _now_string()
    APP_STATE['wait_started_monotonic'] = time.monotonic()


def _clear_wait_timer():
    APP_STATE['wait_started_at'] = None
    APP_STATE['wait_started_monotonic'] = None


def _apply_session_timeouts():
    if APP_STATE.get('dataset_operation'):
        return
    _apply_sorter_timeout()
    if not APP_STATE['active_fruit_id'] or not APP_STATE.get('wait_started_monotonic'):
        return
    elapsed = time.monotonic() - APP_STATE['wait_started_monotonic']
    status = APP_STATE['status']
    if status == 'waiting_esp32_start' and elapsed > ESP32_START_TIMEOUT_SECONDS:
        _set_error_state('esp32_start_timeout', 'ESP32 超過 10 秒未取得開始命令，請檢查 Wi-Fi、HTTPS 與 firmware 輪詢狀態。')
    elif status in ('waiting_station_ready', 'waiting_motor') and elapsed > HARDWARE_STEP_TIMEOUT_SECONDS:
        _set_error_state('hardware_timeout', '硬體流程逾時，請檢查 SG90 閘門、ESP32 回報與百香果是否卡住。')
    elif status == 'waiting_camera' and elapsed > CAMERA_UPLOAD_TIMEOUT_SECONDS:
        _set_error_state('camera_upload_timeout', '手機超過 45 秒未完成當站照片上傳，請檢查手機相機頁與網路連線。')


def _apply_sorter_timeout():
    if not _sorter_busy():
        return
    deadline = APP_STATE.get('sorter_deadline_monotonic')
    if deadline is None or time.monotonic() <= deadline:
        return

    previous_status = APP_STATE['sorter_status']
    command_id = APP_STATE.get('sorter_command_id')
    current_command = APP_STATE.get('motor_command') or {}
    if current_command.get('command_id') == command_id:
        APP_STATE['motor_command'] = None
    reason = 'esp32_timeout' if previous_status == 'pending' else 'classifier_timeout'
    _disable_auto_run()
    APP_STATE['auto_run_recovery_reason'] = reason
    APP_STATE['last_error_reason'] = reason
    APP_STATE['last_error_command'] = current_command.get('command')
    APP_STATE['last_error_command_id'] = command_id
    APP_STATE['status'] = 'error'
    APP_STATE['sorter_status'] = 'timeout'
    APP_STATE['sorter_error'] = reason
    APP_STATE['sorter_deadline_monotonic'] = None
    APP_STATE['message'] = f'{APP_STATE.get("sorter_fruit_id")} 的硬體分類器逾時：{reason}'
    _clear_wait_timer()
    _record_transition(
        'classification_sorter_timeout',
        fruit_id=APP_STATE.get('sorter_fruit_id'),
        command_id=command_id,
        details={'reason': reason, 'previous_status': previous_status},
    )


def _set_error_state(reason, message):
    command = APP_STATE.get('motor_command') or {}
    APP_STATE['pending_capture'] = False
    APP_STATE['motor_command'] = None
    APP_STATE['last_error_reason'] = reason
    APP_STATE['last_error_command'] = command.get('command')
    APP_STATE['last_error_command_id'] = command.get('command_id')
    APP_STATE['status'] = 'error'
    APP_STATE['message'] = message
    _clear_wait_timer()
    _record_transition('capture_error', details={'reason': reason})


def _capture_started_debug_payload(received_fruit_id, received_capture_token, received_station_index):
    return {
        'current_status': APP_STATE['status'],
        'capture_requested': APP_STATE['pending_capture'],
        'expected_fruit_id': APP_STATE['active_fruit_id'],
        'received_fruit_id': received_fruit_id,
        'expected_capture_token': APP_STATE['capture_token'],
        'received_capture_token': received_capture_token,
        'expected_station_index': APP_STATE.get('active_station_index'),
        'received_station_index': received_station_index,
    }


def _operator_alert_payload():
    reason = (
        APP_STATE.get('last_error_reason')
        or APP_STATE.get('auto_run_recovery_reason')
        or (
            not APP_STATE.get('auto_run_enabled')
            and _auto_run_disabled_reason()
        )
    )
    if not reason:
        return None

    command = APP_STATE.get('motor_command') or {}
    location = {
        'fruit_arrival_delayed': 'upstream_feeder / HC-SR04',
        'feed_arrival_unconfirmed': 'upstream_feeder / HC-SR04',
        'esp32_restarted_during_feed': 'ESP32 / upstream_feeder',
        'esp32_offline': 'ESP32',
        'esp32_start_timeout': 'ESP32 / SG90 gates',
        'hardware_timeout': 'ESP32 / SG90 gates',
        'motor_error': 'ESP32 / actuator',
        'feeder_capability_missing': 'ESP32 / upstream_feeder',
        'feeder_calibration_required': 'upstream_feeder',
        'feeder_sensor_not_clear': 'upstream_feeder / HC-SR04',
        'feeder_sensor_unavailable': 'upstream_feeder / HC-SR04',
        'feeder_max_run_timeout': 'upstream_feeder',
        'feeder_hardware_validation_required': 'upstream_feeder / HC-SR04',
        'feeder_timing_not_applied': 'ESP32 / upstream_feeder',
        'feeder_state_not_idle': 'upstream_feeder',
        'camera_not_ready': 'camera station',
        'camera_upload_timeout': 'camera station',
        'classifier_busy': 'MG996R classifier',
        'classifier_timeout': 'MG996R classifier',
        'esp32_timeout': 'ESP32 / MG996R classifier',
        'active_fruit_exists': 'capture pipeline',
        'pending_motor_command': 'ESP32 motor command',
        'dataset_operation_in_progress': 'dataset storage',
        'system_not_idle': 'Django state machine',
    }.get(reason, APP_STATE.get('status') or 'unknown')
    return {
        'reason': reason,
        'location': location,
        'fruit_id': APP_STATE.get('active_fruit_id') or APP_STATE.get('sorter_fruit_id'),
        'command': APP_STATE.get('last_error_command') or command.get('command'),
        'command_id': APP_STATE.get('last_error_command_id') or command.get('command_id'),
        'instruction': (
            '暫停 → 排除／重新拍攝／刪除 → 開始執行'
            '（請自行暫停、排除狀況後重新開始）'
        ),
    }


def _state_payload(extra=None):
    active_fruit_id = APP_STATE['active_fruit_id']
    latest_images = _build_image_list(active_fruit_id) if active_fruit_id else []
    image_total = len(latest_images)
    is_uploading = APP_STATE['status'] == 'uploading'
    dataset_busy = bool(APP_STATE.get('dataset_operation'))
    can_classify = (
        not dataset_busy
        and bool(active_fruit_id)
        and image_total == IMAGE_COUNT
        and APP_STATE['status'] == 'uploaded'
    )
    can_discard = bool(active_fruit_id) and not is_uploading and not dataset_busy
    can_recapture = not dataset_busy and bool(active_fruit_id) and APP_STATE['status'] in (
        'waiting_esp32_start',
        'waiting_station_ready',
        'waiting_camera',
        'waiting_motor',
        'incomplete',
        'uploaded',
        'error',
    )
    payload = {
        'status': APP_STATE['status'],
        'message': APP_STATE['message'],
        'pending_capture': APP_STATE['pending_capture'],
        'capture_requested': APP_STATE['pending_capture'],
        'capture_token': APP_STATE['capture_token'],
        'revision': APP_STATE.get('state_revision', 0),
        'state_revision': APP_STATE.get('state_revision', 0),
        'active_fruit_id': active_fruit_id,
        'station_index': APP_STATE.get('active_station_index'),
        'active_station_index': APP_STATE.get('active_station_index'),
        'station_statuses': APP_STATE.get('station_statuses') or {},
        'capture_time': APP_STATE['capture_time'],
        'next_fruit_id': _format_fruit_id(_read_counter()),
        'labels': LABELS,
        'image_count': IMAGE_COUNT,
        'image_total': image_total,
        'latest_images': latest_images,
        'can_discard': can_discard,
        'can_classify': can_classify,
        'can_recapture': can_recapture,
        'esp32_online': _esp32_is_online(),
        'auto_run_enabled': APP_STATE.get('auto_run_enabled', False),
        'auto_run_finishing': APP_STATE.get('auto_run_finishing', False),
        'auto_run_recovery_reason': APP_STATE.get('auto_run_recovery_reason'),
        'camera_ready': _camera_is_ready(),
        'camera_last_live_frame_at': APP_STATE.get('camera_last_live_frame_at'),
        'esp32_boot_id': APP_STATE.get('esp32_boot_id'),
        'esp32_feeder_capable': APP_STATE.get('esp32_feeder_capable', False),
        'esp32_feeder_state': APP_STATE.get('esp32_feeder_state'),
        'feeder_sensor_state': APP_STATE.get('esp32_feeder_sensor_state'),
        'feeder_test_result': APP_STATE.get('feeder_test_result'),
        'feeder_test_passed_revision': APP_STATE.get('feeder_test_passed_revision'),
        'can_confirm_feeder_calibration': (
            APP_STATE.get('feeder_test_passed_revision')
            == APP_STATE['capture_timing_revision']
        ),
        'capture_timing_warning': APP_STATE.get('capture_timing_warning'),
        'can_test_feeder': _feeder_test_disabled_reason() is None,
        'feeder_test_disabled_reason': _feeder_test_disabled_reason(),
        'can_start_auto_run': _auto_run_disabled_reason(allow_recovery=True) is None,
        'auto_run_disabled_reason': _auto_run_disabled_reason(),
        'last_esp32_poll_at': APP_STATE.get('last_esp32_poll_at'),
        'last_esp32_report_at': APP_STATE.get('last_esp32_report_at'),
        'motor_command': APP_STATE.get('motor_command') or _esp32_command_payload(),
        'last_error_reason': APP_STATE.get('last_error_reason'),
        'operator_alert': _operator_alert_payload(),
        'home_angle': HOME_ANGLE,
        'release_angle': RELEASE_ANGLE,
        'first_station_settle_ms': APP_STATE['capture_timing']['first_station_settle_ms'],
        'servo_settle_ms': APP_STATE['capture_timing']['servo_settle_ms'],
        'fruit_settle_ms': APP_STATE['capture_timing']['fruit_settle_ms'],
        'final_gate_return_delay_ms': APP_STATE['capture_timing']['final_gate_return_delay_ms'],
        'idle_command_poll_interval_ms': APP_STATE['capture_timing']['idle_command_poll_interval_ms'],
        **_capture_timing_state_payload(),
        'timing': _timing_payload(),
        'transition_trace': list(APP_STATE.get('transition_trace') or []),
        # ``trace`` is a small compatibility alias for early dashboard builds.
        'trace': list(APP_STATE.get('transition_trace') or []),
        'dataset_path': str(_dataset_root()),
        'dataset_operation': APP_STATE.dataset_operation_payload(),
        **_sorter_state_payload(),
    }
    if extra:
        payload.update(extra)
    return payload


def _camera_state_payload():
    """Minimal, cache-safe state contract for the phone camera poller."""
    fruit_id = APP_STATE.get('active_fruit_id')
    station_index = APP_STATE.get('active_station_index')
    requested = bool(APP_STATE.get('pending_capture'))
    capture = {
        'fruit_id': fruit_id,
        'token': APP_STATE.get('capture_token'),
        'capture_token': APP_STATE.get('capture_token'),
        'station_index': station_index,
        'requested': requested,
    }
    return {
        'revision': APP_STATE.get('state_revision', 0),
        'fruit_id': fruit_id,
        'active_fruit_id': fruit_id,
        'capture_token': APP_STATE.get('capture_token'),
        'station_index': station_index,
        'capture_requested': requested,
        'pending_capture': requested,
        'status': APP_STATE.get('status'),
        'camera_ready': _camera_is_ready(),
        'capture': capture,
    }


def _compact_esp32_payload(payload):
    """Keep report responses small while preserving the firmware contract."""
    return api_payloads.compact_esp32_report(payload, APP_STATE.get('status'))


def _sync_active_state_with_filesystem():
    _apply_session_timeouts()
    if APP_STATE.get('dataset_operation'):
        return
    active_fruit_id = APP_STATE['active_fruit_id']
    if active_fruit_id:
        active_dir = _temp_dir() / active_fruit_id
        if not active_dir.exists():
            _clear_active_state('目前沒有暫存資料，可重新拍攝。', status='idle')
            return

        if APP_STATE['status'] in (
            'waiting_esp32_start',
            'waiting_station_ready',
            'waiting_camera',
            'uploading',
            'waiting_motor',
            'error',
        ):
            APP_STATE['pending_capture'] = False
            if APP_STATE['status'] == 'waiting_camera':
                APP_STATE['pending_capture'] = True
            APP_STATE['message'] = APP_STATE['message'] or f'{active_fruit_id} 拍攝流程進行中。'
            return

        image_total = _temp_image_count(active_dir)
        if image_total == IMAGE_COUNT:
            APP_STATE['pending_capture'] = False
            APP_STATE['status'] = 'uploaded'
            if not APP_STATE['message']:
                APP_STATE['message'] = f'{active_fruit_id} 已收到 {IMAGE_COUNT} 張照片，請確認後分類。'
            return

        if image_total == 0 and not APP_STATE['pending_capture']:
            try:
                _safe_rmtree(active_dir)
                _clear_active_state(f'{active_fruit_id} 沒有照片，已自動清除，可重新拍攝。', status='idle')
            except DatasetFileBusyError as exc:
                _clear_active_state(
                    f'{active_fruit_id} 沒有照片，已解除目前狀態；空資料夾暫時刪不掉，之後會自動重用或再清理。{exc}',
                    status='idle',
                )
            return

        if image_total > 0:
            APP_STATE['pending_capture'] = False
            APP_STATE['status'] = 'incomplete'
            APP_STATE['message'] = f'{active_fruit_id} 只有 {image_total}/{IMAGE_COUNT} 張照片，請刪除後重新拍攝。'
            return

        APP_STATE['status'] = 'waiting_camera'
        return

    temp_fruit_dirs = sorted(
        path for path in _temp_dir().glob('fruit_*')
        if (
            path.is_dir()
            and FRUIT_ID_PATTERN.match(path.name)
            and not _is_deferred_discard_path(path)
        )
    )
    non_empty_dirs = []
    removed_empty = False
    for fruit_dir in temp_fruit_dirs:
        image_total = _temp_image_count(fruit_dir)
        if image_total == 0:
            try:
                _safe_rmtree(fruit_dir)
                removed_empty = True
            except DatasetFileBusyError as exc:
                removed_empty = True
                APP_STATE['message'] = f'空的暫存資料夾暫時刪不掉，將在之後自動重用或再清理。{exc}'
        else:
            non_empty_dirs.append(fruit_dir)

    if len(non_empty_dirs) > 1:
        fruit_dir = non_empty_dirs[0]
        APP_STATE['active_fruit_id'] = fruit_dir.name
        APP_STATE['pending_capture'] = False
        APP_STATE['capture_time'] = _now_string()
        APP_STATE['status'] = 'multiple_temp'
        APP_STATE['message'] = 'temp 內有多個未處理資料夾，請逐筆刪除或重置 dataset。'
        return

    if len(non_empty_dirs) != 1:
        if removed_empty and not non_empty_dirs:
            _clear_active_state('已自動清除空的暫存資料夾，可重新拍攝。', status='idle')
        return

    fruit_dir = non_empty_dirs[0]
    image_total = _temp_image_count(fruit_dir)
    completed = image_total == IMAGE_COUNT
    APP_STATE['active_fruit_id'] = fruit_dir.name
    APP_STATE['pending_capture'] = not completed
    APP_STATE['capture_time'] = _now_string()
    APP_STATE['status'] = 'uploaded' if completed else 'incomplete'
    if completed:
        APP_STATE['message'] = f'已從 temp 恢復 {fruit_dir.name}，請繼續分類。'
    else:
        APP_STATE['message'] = f'已從 temp 恢復 {fruit_dir.name}，但只有 {image_total}/{IMAGE_COUNT} 張照片，請刪除後重新拍攝。'
    if not completed and APP_STATE['capture_token'] == 0:
        APP_STATE['capture_token'] = 1


def _clear_active_state(message, status='idle'):
    APP_STATE['pending_capture'] = False
    APP_STATE['capture_token'] = 0
    APP_STATE['active_fruit_id'] = None
    APP_STATE['capture_time'] = None
    APP_STATE['source'] = None
    APP_STATE['fast_path_trigger_id'] = None
    APP_STATE['fast_path_fruit_id'] = None
    APP_STATE['fast_path_capture_token'] = None
    APP_STATE['active_station_index'] = None
    APP_STATE['station_statuses'] = {}
    APP_STATE['motor_command'] = None
    APP_STATE['last_error_reason'] = None
    APP_STATE['status'] = status
    APP_STATE['message'] = message
    _clear_wait_timer()
    _reset_timing_state()


def _temp_image_count(fruit_dir):
    return sum(1 for filename in IMAGE_FILENAMES if (fruit_dir / filename).exists())


def _is_reusable_temp_dir(fruit_dir):
    fruit_dir = Path(fruit_dir)
    if not fruit_dir.exists() or not fruit_dir.is_dir():
        return True
    if _temp_image_count(fruit_dir) > 0:
        return False
    return not any(fruit_dir.iterdir())


def _timing_payload():
    return {
        'command_created_at': APP_STATE['command_created_at'],
        'capture_started_at': APP_STATE['capture_started_at'],
        'upload_received_at': APP_STATE['upload_received_at'],
        'command_to_phone_start_ms': APP_STATE['command_to_phone_start_ms'],
        'client_timing': APP_STATE.get('client_timing') or {},
        'wait_started_at': APP_STATE.get('wait_started_at'),
    }


def _reset_timing_state():
    APP_STATE['command_created_at'] = None
    APP_STATE['command_created_monotonic'] = None
    APP_STATE['capture_started_at'] = None
    APP_STATE['capture_started_monotonic'] = None
    APP_STATE['command_to_phone_start_ms'] = None
    APP_STATE['client_timing'] = {}
    APP_STATE['upload_received_at'] = None


def _apply_capture_meta(raw_meta):
    if not raw_meta:
        return
    try:
        meta = json.loads(raw_meta)
    except (TypeError, json.JSONDecodeError):
        return

    _apply_client_timing(meta.get('client_timing'))


def _apply_client_timing(raw_timing):
    if isinstance(raw_timing, str):
        try:
            raw_timing = json.loads(raw_timing)
        except (TypeError, json.JSONDecodeError):
            return
    if not isinstance(raw_timing, dict):
        return

    timing = {}
    for key in CLIENT_TIMING_KEYS:
        value = raw_timing.get(key)
        if value is None:
            continue
        try:
            timing[key] = round(float(value), 1)
        except (TypeError, ValueError):
            continue
    if not timing:
        return
    APP_STATE['client_timing'] = timing
    _record_transition(
        'client_timing_received',
        station_index=APP_STATE.get('active_station_index'),
        details=timing,
    )


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False


def _normalise_trigger_id(value):
    if value is None:
        return None
    trigger_id = str(value).strip()
    if not trigger_id or len(trigger_id) > 128 or any(char.isspace() for char in trigger_id):
        return None
    return trigger_id


def _ice_from_index(value, total):
    index = _safe_int(value)
    if index is None:
        return None
    return max(0, min(index, total))


def _safe_rmtree(path):
    path = Path(path)
    if not path.exists():
        return
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法刪除 {path}，可能正被瀏覽器預覽、檔案總管或其他程式使用。請關閉相關視窗後再試。') from last_error


def _discard_temp_fruit(fruit_dir, fruit_id):
    fruit_dir = Path(fruit_dir)
    if not fruit_dir.exists():
        return {'mode': 'deleted', 'cleanup_path': None, 'next_fruit_id': None}

    try:
        _safe_rmtree(fruit_dir)
        if not fruit_dir.exists():
            return {'mode': 'deleted', 'cleanup_path': None, 'next_fruit_id': None}
        raise DatasetFileBusyError(f'刪除 {fruit_dir} 後資料夾仍存在。')
    except DatasetFileBusyError as delete_error:
        target = _unique_delete_pending_path(
            fruit_dir,
            reason=datetime.now().strftime('discard_%Y%m%d_%H%M%S'),
        )
        try:
            _safe_move(fruit_dir, target)
        except DatasetFileBusyError:
            _record_discard_cleanup(fruit_dir, fruit_id=fruit_id, blocked_in_temp=True)
            next_fruit_id = _advance_counter_after_deferred_discard(fruit_id)
            return {
                'mode': 'deferred_cleanup',
                'cleanup_path': _dataset_relative_path(fruit_dir),
                'next_fruit_id': next_fruit_id,
                'warning': str(delete_error),
            }

        if fruit_dir.exists():
            _record_discard_cleanup(fruit_dir, fruit_id=fruit_id, blocked_in_temp=True)
            next_fruit_id = _advance_counter_after_deferred_discard(fruit_id)
            return {
                'mode': 'deferred_cleanup',
                'cleanup_path': _dataset_relative_path(fruit_dir),
                'next_fruit_id': next_fruit_id,
                'warning': str(delete_error),
            }

        _record_discard_cleanup(
            fruit_dir,
            fruit_id=fruit_id,
            cleanup_path=target,
            blocked_in_temp=False,
        )
        return {
            'mode': 'quarantined',
            'cleanup_path': _dataset_relative_path(target),
            'next_fruit_id': None,
            'warning': str(delete_error),
        }


def _advance_counter_after_deferred_discard(fruit_id):
    fruit_number = _fruit_number(fruit_id) or _read_counter()
    next_number = _next_available_number(max(_read_counter(), fruit_number + 1))
    _write_counter(next_number)
    return _format_fruit_id(next_number)


def _read_discard_state():
    try:
        with _discard_state_path().open('r', encoding='utf-8') as state_file:
            data = json.load(state_file)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {'pending_paths': []}

    pending_paths = data.get('pending_paths') if isinstance(data, dict) else None
    if not isinstance(pending_paths, list):
        pending_paths = []
    return {
        'pending_paths': [
            entry for entry in pending_paths
            if (
                isinstance(entry, dict)
                and _is_safe_dataset_relative_path(entry.get('source_path'))
                and _is_safe_dataset_relative_path(entry.get('cleanup_path'))
            )
        ],
    }


def _write_discard_state(data):
    _discard_state_path().parent.mkdir(parents=True, exist_ok=True)
    with _discard_state_path().open('w', encoding='utf-8') as state_file:
        json.dump(data, state_file, ensure_ascii=False, indent=2)
        state_file.write('\n')


def _record_discard_cleanup(source_path, *, fruit_id, cleanup_path=None, blocked_in_temp):
    source_relative_path = _dataset_relative_path(source_path)
    cleanup_relative_path = _dataset_relative_path(cleanup_path or source_path)
    state = _read_discard_state()
    entries = [
        entry for entry in state['pending_paths']
        if entry.get('source_path') != source_relative_path
    ]
    entries.append({
        'fruit_id': fruit_id,
        'source_path': source_relative_path,
        'cleanup_path': cleanup_relative_path,
        'blocked_in_temp': bool(blocked_in_temp),
        'recorded_at': _now_string(),
    })
    _write_discard_state({'pending_paths': entries})


def _is_safe_dataset_relative_path(value):
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value)
    return not path.is_absolute() and '..' not in path.parts


def _is_deferred_discard_path(path):
    relative_path = _dataset_relative_path(path)
    return any(
        entry.get('blocked_in_temp') and entry.get('source_path') == relative_path
        for entry in _read_discard_state()['pending_paths']
    )


def _retry_deferred_discards_if_due():
    now = time.monotonic()
    # ponytail: single-process maintenance gate; add a dedicated lock only if
    # concurrent dashboard pollers become a supported deployment mode.
    last_attempt = APP_STATE.get('discard_cleanup_last_monotonic')
    if last_attempt is not None and now - last_attempt < DISCARD_CLEANUP_RETRY_INTERVAL_SECONDS:
        return
    APP_STATE['discard_cleanup_last_monotonic'] = now

    state = _read_discard_state()
    remaining_entries = []
    for entry in state['pending_paths']:
        cleanup_path = _dataset_root() / entry.get('cleanup_path', '')
        if not cleanup_path.exists():
            continue
        try:
            if cleanup_path.is_dir():
                _safe_rmtree(cleanup_path)
            else:
                _safe_unlink(cleanup_path)
        except DatasetFileBusyError:
            remaining_entries.append(entry)

    if remaining_entries != state['pending_paths']:
        _write_discard_state({'pending_paths': remaining_entries})


def _safe_unlink(path):
    path = Path(path)
    if not path.exists():
        return
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            path.unlink()
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法刪除 {path}，可能正被其他程式使用。請關閉相關視窗後再試。') from last_error


def _safe_move(src, dest):
    src = Path(src)
    dest = Path(dest)
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            shutil.move(str(src), str(dest))
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法移動 {src} 到 {dest}，可能正被其他程式使用。請關閉預覽後再試。') from last_error


def _safe_replace(src, dest):
    src = Path(src)
    dest = Path(dest)
    last_error = None
    for _ in range(FILE_OPERATION_RETRIES):
        try:
            src.replace(dest)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(FILE_OPERATION_RETRY_DELAY_SECONDS)
    raise DatasetFileBusyError(f'無法替換 {dest}，可能正被其他程式使用。請關閉預覽後再試。') from last_error


def _clear_temp_images(fruit_dir):
    fruit_dir = Path(fruit_dir)
    try:
        for image_file in fruit_dir.glob('img_*.jpg'):
            _safe_unlink(image_file)
    finally:
        if FRUIT_ID_PATTERN.match(fruit_dir.name):
            _load_image_list.cache_clear()


def _save_station_image(fruit_dir, station_index, image_file):
    result = dataset_store.save_station_image(
        Path(fruit_dir),
        station_index,
        image_file,
        image_filenames=IMAGE_FILENAMES,
        staging_suffix=UPLOAD_STAGING_SUFFIX,
        safe_unlink=_safe_unlink,
        safe_replace=_safe_replace,
    )
    active_fruit_id = APP_STATE.get('active_fruit_id')
    if active_fruit_id:
        _load_image_list.cache_clear()
    return result


def _reset_dataset_contents():
    _ensure_dataset_structure()
    delete_warnings = []
    moved_paths = []
    ignored_paths = []
    reset_id = datetime.now().strftime('reset_%Y%m%d_%H%M%S')
    for parent in [_temp_dir(), *(_dataset_root() / label for label in LABELS)]:
        parent.mkdir(parents=True, exist_ok=True)
        for child in parent.iterdir():
            result = _remove_or_archive_reset_path(child, reset_id)
            if result['warning']:
                delete_warnings.append(result['warning'])
            if result['moved_to']:
                moved_paths.append({
                    'from': result['relative_path'],
                    'to': result['moved_to'],
                })
            if result['ignored']:
                ignored_paths.append(result['relative_path'])
    with _metadata_path().open('w', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
        writer.writeheader()
    _load_image_list.cache_clear()
    _write_counter(1)
    _write_reset_state({
        'reset_at': _now_string(),
        'ignored_paths': ignored_paths,
        'moved_paths': moved_paths,
    })
    return delete_warnings


def _remove_or_archive_reset_path(path, reset_id):
    path = Path(path)
    relative_path = _dataset_relative_path(path)
    result = {
        'relative_path': relative_path,
        'warning': None,
        'moved_to': None,
        'ignored': False,
    }

    try:
        if path.is_dir():
            _safe_rmtree(path)
        else:
            _safe_unlink(path)
        return result
    except DatasetFileBusyError as delete_error:
        moved_warning = _move_reset_leftover_to_pending(path, reason=reset_id)
        if not path.exists():
            result['warning'] = f'{relative_path} 刪除失敗，已移到 {moved_warning}。'
            result['moved_to'] = moved_warning
            return result

        result['warning'] = f'{delete_error}；搬移到 _delete_pending 也失敗，已在 reset_state.json 標記為忽略。'
        result['ignored'] = True
        return result


def _move_reset_leftover_to_pending(path, reason):
    path = Path(path)
    if not path.exists():
        return ''

    target = _unique_delete_pending_path(path, reason)
    try:
        _safe_move(path, target)
    except DatasetFileBusyError as exc:
        return str(exc)
    return _dataset_relative_path(target)


def _unique_delete_pending_path(path, reason):
    relative_path = _dataset_relative_path(path)
    target = _dataset_root() / '_delete_pending' / reason / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return target

    suffix = 1
    while True:
        candidate = target.with_name(f'{target.name}_{suffix}')
        if not candidate.exists():
            return candidate
        suffix += 1


def _dataset_relative_path(path):
    try:
        return Path(path).relative_to(_dataset_root()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _read_reset_state():
    try:
        with _reset_state_path().open('r', encoding='utf-8') as reset_file:
            data = json.load(reset_file)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {'reset_at': None, 'ignored_paths': [], 'moved_paths': []}

    ignored_paths = data.get('ignored_paths')
    moved_paths = data.get('moved_paths')
    return {
        'reset_at': data.get('reset_at'),
        'ignored_paths': ignored_paths if isinstance(ignored_paths, list) else [],
        'moved_paths': moved_paths if isinstance(moved_paths, list) else [],
    }


def _write_reset_state(data):
    _reset_state_path().parent.mkdir(parents=True, exist_ok=True)
    with _reset_state_path().open('w', encoding='utf-8') as reset_file:
        json.dump(data, reset_file, ensure_ascii=False, indent=2)
        reset_file.write('\n')


def _is_ignored_reset_path(path):
    relative_path = _dataset_relative_path(path)
    return relative_path in set(_read_reset_state().get('ignored_paths', []))


def _remove_ignored_reset_path(path):
    relative_path = _dataset_relative_path(path)
    state = _read_reset_state()
    state['ignored_paths'] = [
        ignored_path
        for ignored_path in state.get('ignored_paths', [])
        if ignored_path != relative_path
    ]
    _write_reset_state(state)


def _is_session_description(value, expected_type):
    return (
        isinstance(value, dict)
        and value.get('type') == expected_type
        and isinstance(value.get('sdp'), str)
        and bool(value.get('sdp').strip())
    )


def _now_string():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _json_error(message, status=400, reason=None, extra=None):
    payload = {'status': 'error', 'message': message}
    if reason:
        payload['reason'] = reason
    if extra:
        payload.update(extra)
    return JsonResponse(payload, status=status)
