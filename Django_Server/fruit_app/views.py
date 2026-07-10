import csv
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import capture_session, dataset_store, webrtc_signaling


LABELS = ['上中等', '下等', '廢棄', '加工']
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
DEFAULT_CAPTURE_INTERVAL_MS = 0
MIN_CAPTURE_INTERVAL_MS = 0
MAX_CAPTURE_INTERVAL_MS = 3000
HOME_ANGLE = 0
RELEASE_ANGLE = 90
# Conservative defaults that still preserve the first-station fast path.
FIRST_STATION_SETTLE_MS = 200
SERVO_SETTLE_MS = 200
FRUIT_SETTLE_MS = 200
FINAL_GATE_RETURN_DELAY_MS = 200
CAPTURE_TIMING_RECOMMENDED = {
    'first_station_settle_ms': FIRST_STATION_SETTLE_MS,
    'servo_settle_ms': SERVO_SETTLE_MS,
    'fruit_settle_ms': FRUIT_SETTLE_MS,
    'final_gate_return_delay_ms': FINAL_GATE_RETURN_DELAY_MS,
}
CAPTURE_TIMING_FIELDS = tuple(CAPTURE_TIMING_RECOMMENDED)
CAPTURE_TIMING_STEP_MS = 50
CAPTURE_TIMING_MIN_MS = 50
CAPTURE_TIMING_MAX_MS = 3000
ESP32_ONLINE_WINDOW_SECONDS = 20
ESP32_START_TIMEOUT_SECONDS = 10
HARDWARE_STEP_TIMEOUT_SECONDS = 20
CAMERA_UPLOAD_TIMEOUT_SECONDS = 45
FILE_OPERATION_RETRIES = 8
FILE_OPERATION_RETRY_DELAY_SECONDS = 0.15
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

STATE_LOCK = threading.RLock()
APP_STATE = {
    'pending_capture': False,
    'capture_token': 0,
    'active_fruit_id': None,
    'capture_time': None,
    'capture_interval_ms': DEFAULT_CAPTURE_INTERVAL_MS,
    'source': None,
    'active_station_index': None,
    'station_statuses': {},
    'motor_command': None,
    'motor_command_id': 0,
    'last_error_reason': None,
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
    'phone_capture_timestamps_ms': [],
    'phone_capture_intervals_ms': [],
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
    'discard_cleanup_last_monotonic': None,
    'status': 'idle',
    'message': '等待手機連線與手動拍攝。',
}
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
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        payload = _state_payload()
    return _no_store_response(JsonResponse(payload))


@require_GET
def camera_state_api(request):
    """Return only the fields needed by the high-frequency camera poller."""
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
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
        if APP_STATE['status'] != 'idle' or APP_STATE.get('active_fruit_id') or APP_STATE.get('motor_command'):
            return _json_error(
                '拍攝流程進行中，請等待目前 fruit 完成、分類或跳過後再調整停穩時間。',
                status=409,
                reason='capture_timing_update_requires_idle',
            )

        try:
            timing = _normalise_capture_timing(data, require_all=True)
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status, reason=exc.reason)

        if timing == APP_STATE['capture_timing']:
            return JsonResponse(_state_payload(extra={
                'ok': True,
                'capture_timing_unchanged': True,
            }))

        previous_timing = APP_STATE['capture_timing']
        previous_revision = APP_STATE['capture_timing_revision']
        previous_applied_at = APP_STATE['capture_timing_applied_at']
        APP_STATE['capture_timing'] = timing
        APP_STATE['capture_timing_revision'] = previous_revision + 1
        APP_STATE['capture_timing_applied_at'] = None
        try:
            _write_capture_timing_config(
                timing,
                APP_STATE['capture_timing_revision'],
            )
        except OSError as exc:
            APP_STATE['capture_timing'] = previous_timing
            APP_STATE['capture_timing_revision'] = previous_revision
            APP_STATE['capture_timing_applied_at'] = previous_applied_at
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
def manual_capture_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if not _esp32_is_online():
            return _json_error(
                'ESP32 尚未連線或已超過 20 秒未輪詢，請確認正式三閘門 firmware 已啟動。',
                status=503,
                reason='esp32_offline',
            )
        try:
            payload = _create_capture_session(source='manual')
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status, reason=exc.reason)
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def esp32_trigger_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _mark_esp32_report()
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
        _mark_esp32_poll()
        _sync_active_state_with_filesystem()
        payload = _esp32_command_payload()

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
        _mark_esp32_report()
        if event == 'timing_config_applied':
            try:
                payload = _handle_timing_config_applied(data)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(payload)

        if event == 'hcsr04_station_1_ready':
            payload = _handle_hcsr04_station_1_ready(data)
            return JsonResponse(payload)

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
                return JsonResponse(payload)
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
                    return JsonResponse(payload)
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            payload.update({'ok': True, 'event': event})
            return JsonResponse(payload)

        if event.startswith('station_') and event.endswith('_ready'):
            station_index = _station_from_ready_event(event)
            if station_index is None:
                return _json_error('station ready 事件格式不正確。', status=400, reason='invalid_event')
            try:
                payload = _handle_station_ready(station_index, command_id=command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(payload)

        if event == 'capture_sequence_finished':
            try:
                payload = _handle_sequence_finished(command_id=command_id)
            except CaptureCommandError as exc:
                return _json_error(exc.message, status=exc.status, reason=exc.reason, extra={'ok': False})
            return JsonResponse(payload)

        if event == 'motor_error':
            message = data.get('message') or 'ESP32 回報馬達動作失敗。'
            _set_error_state('motor_error', message)
            return JsonResponse(_state_payload(extra={'ok': True, 'event': event}))

        return _json_error('未知的 ESP32 回報事件。', status=400, reason='unknown_event', extra={'ok': False})


@csrf_exempt
@require_POST
def recapture_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重新拍攝。', status=409)
        if not _esp32_is_online():
            return _json_error(
                'ESP32 尚未連線或已超過 20 秒未輪詢，無法重新啟動三站流程。',
                status=503,
                reason='esp32_offline',
            )
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可重新拍攝的資料。', status=409)

        fruit_dir = _temp_dir() / fruit_id
        if not fruit_dir.exists():
            return _json_error('暫存資料夾不存在，請重新建立拍攝。', status=404)

        try:
            _clear_temp_images(fruit_dir)
        except DatasetFileBusyError as exc:
            return _json_error(str(exc), status=409)

        _start_existing_capture_session(
            fruit_id,
            source='manual_recapture',
            message=f'已重新啟動 {fruit_id}，等待 ESP32 開始三站閘門流程。',
        )
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
        _mark_station_captured(station_index)
        _set_release_command(station_index)
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
        return _json_error('分類必須是上中等、下等、廢棄或加工。', status=400)

    with STATE_LOCK:
        _sync_active_state_with_filesystem()
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

        try:
            _safe_move(src_dir, dest_dir)
        except DatasetFileBusyError as exc:
            return _json_error(str(exc), status=409)
        relative_path = f'{label}/{fruit_id}'
        capture_time = APP_STATE['capture_time'] or _now_string()
        _append_metadata(fruit_id, label, capture_time, relative_path, note, dest_dir)

        fruit_number = _fruit_number(fruit_id)
        if fruit_number is not None:
            _write_counter(max(_read_counter(), fruit_number + 1))

        APP_STATE['pending_capture'] = False
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
        APP_STATE['status'] = 'classified'
        APP_STATE['message'] = f'{fruit_id} 已分類為「{label}」。'
        _reset_timing_state(keep_interval=True)
        _record_transition('fruit_classified', fruit_id=fruit_id, details={'label': label})
        payload = {
            'status': 'success',
            'fruit_id': fruit_id,
            'label': label,
            'path': relative_path,
            'next_fruit_id': _format_fruit_id(_read_counter()),
        }
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def discard_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再刪除。', status=409)
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可刪除的暫存資料。', status=409)

        fruit_dir = _temp_dir() / fruit_id
        discard_result = _discard_temp_fruit(fruit_dir, fruit_id)
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
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重置 dataset。', status=409)
        delete_warnings = _reset_dataset_contents()
        _clear_active_state('dataset 已重置，下一筆資料將從 fruit_001 開始。', status='idle')
        APP_STATE['capture_token'] = 0
        APP_STATE['capture_interval_ms'] = DEFAULT_CAPTURE_INTERVAL_MS
        APP_STATE['motor_command_id'] = 0
        APP_STATE['transition_trace'] = []
        APP_STATE['trace_sequence'] = 0
        _reset_timing_state(keep_interval=True)
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
    return HttpResponse(image_path.read_bytes(), content_type='image/jpeg')


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
            'capture_interval_ms': DEFAULT_CAPTURE_INTERVAL_MS,
            'source': None,
            'active_station_index': None,
            'station_statuses': {},
            'motor_command': None,
            'motor_command_id': 0,
            'last_error_reason': None,
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
            'phone_capture_timestamps_ms': [],
            'phone_capture_intervals_ms': [],
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
            'discard_cleanup_last_monotonic': None,
            'status': 'idle',
            'message': '等待手機連線與手動拍攝。',
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


def _delete_pending_dir():
    return _dataset_root() / '_delete_pending'


def _discard_state_path():
    return _dataset_root() / 'discard_state.json'


def _ensure_dataset_structure():
    root = _dataset_root()
    root.mkdir(parents=True, exist_ok=True)
    _temp_dir().mkdir(parents=True, exist_ok=True)
    for label in LABELS:
        (root / label).mkdir(parents=True, exist_ok=True)
    if not _counter_path().exists():
        _write_counter(1)
    _ensure_capture_timing_config()
    _ensure_metadata_header()
    _retry_deferred_discards_if_due()


def _request_data(request):
    content_type = request.META.get('CONTENT_TYPE', '')
    if 'application/json' in content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


def _read_counter():
    try:
        with _counter_path().open('r', encoding='utf-8') as counter_file:
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


def _normalise_capture_timing(raw_timing, *, require_all):
    raw_timing = raw_timing if isinstance(raw_timing, dict) else {}
    timing = {}
    for field in CAPTURE_TIMING_FIELDS:
        raw_value = raw_timing.get(field)
        if raw_value is None:
            if require_all:
                raise CaptureCommandError(
                    f'缺少停穩時間欄位：{field}。',
                    status=400,
                    reason='capture_timing_field_missing',
                )
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise CaptureCommandError(
                f'{field} 必須是整數毫秒。',
                status=400,
                reason='capture_timing_invalid_value',
            ) from None

        minimum = 0 if field == 'final_gate_return_delay_ms' else CAPTURE_TIMING_MIN_MS
        if value < minimum or value > CAPTURE_TIMING_MAX_MS:
            raise CaptureCommandError(
                f'{field} 必須介於 {minimum} 到 {CAPTURE_TIMING_MAX_MS} ms。',
                status=400,
                reason='capture_timing_out_of_range',
            )
        if value % CAPTURE_TIMING_STEP_MS != 0:
            raise CaptureCommandError(
                f'{field} 必須以 {CAPTURE_TIMING_STEP_MS} ms 為間距。',
                status=400,
                reason='capture_timing_invalid_step',
            )
        timing[field] = value
    return timing


def _read_capture_timing_config(path):
    path = Path(path)
    try:
        with path.open('r', encoding='utf-8') as timing_file:
            data = json.load(timing_file)
        timing = _normalise_capture_timing(data.get('capture_timing'), require_all=True)
        revision = _safe_int(data.get('revision'))
        if revision is None or revision < 1:
            raise ValueError('invalid timing revision')
        return timing, revision
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError, CaptureCommandError):
        return None


def _write_capture_timing_config(timing, revision):
    path = _capture_timing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = path.with_suffix('.tmp')
    payload = {
        'revision': int(revision),
        'capture_timing': dict(timing),
    }
    with staging_path.open('w', encoding='utf-8') as timing_file:
        json.dump(payload, timing_file, ensure_ascii=False, indent=2)
        timing_file.write('\n')
    os.replace(staging_path, path)


def _ensure_capture_timing_config():
    path = _capture_timing_path()
    path_key = str(path)
    if APP_STATE.get('capture_timing_loaded_path') == path_key:
        return

    loaded_config = _read_capture_timing_config(path)
    if loaded_config is None:
        loaded_config = _read_capture_timing_config(_legacy_capture_timing_path())
        if loaded_config is None:
            loaded_config = (dict(CAPTURE_TIMING_RECOMMENDED), 1)
        _write_capture_timing_config(*loaded_config)

    timing, revision = loaded_config
    APP_STATE['capture_timing'] = timing
    APP_STATE['capture_timing_revision'] = revision
    APP_STATE['capture_timing_applied_revision'] = 0
    APP_STATE['capture_timing_applied_at'] = None
    APP_STATE['capture_timing_loaded_path'] = path_key
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
    images = []
    for filename in IMAGE_FILENAMES:
        image_path = _find_fruit_image(fruit_id, filename)
        if image_path:
            images.append({
                'filename': filename,
                'url': f'/api/image/{fruit_id}/{filename}/?v={int(image_path.stat().st_mtime)}',
            })
    return images


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
    elif source.startswith('manual'):
        _record_transition('manual_capture_accepted', fruit_id=fruit_id)

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
    APP_STATE['capture_interval_ms'] = DEFAULT_CAPTURE_INTERVAL_MS
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
    _reset_timing_state(keep_interval=True)
    _start_wait_timer()
    _record_transition(
        'capture_session_created',
        fruit_id=fruit_id,
        details={'source': source, 'status': status},
    )


def _start_existing_capture_session(fruit_id, source, message):
    _start_new_capture_session(fruit_id, source, 'waiting_esp32_start', message)
    _set_motor_command('start_sequence', station_index=1)


def _set_motor_command(command, station_index=None):
    timing = APP_STATE['capture_timing']
    APP_STATE['motor_command_id'] += 1
    APP_STATE['motor_command'] = {
        'command_id': APP_STATE['motor_command_id'],
        'command': command,
        'station_index': station_index,
        'fruit_id': APP_STATE['active_fruit_id'],
        'home_angle': HOME_ANGLE,
        'release_angle': RELEASE_ANGLE,
        'timing_revision': APP_STATE['capture_timing_revision'],
        'first_station_settle_ms': timing['first_station_settle_ms'],
        'servo_settle_ms': timing['servo_settle_ms'],
        'fruit_settle_ms': timing['fruit_settle_ms'],
        'final_gate_return_delay_ms': timing['final_gate_return_delay_ms'],
        'created_at': _now_string(),
    }
    _start_wait_timer()
    _record_transition(
        'motor_command_issued',
        station_index=station_index,
        command_id=APP_STATE['motor_command_id'],
        details={'command': command},
    )


def _set_release_command(station_index):
    _set_motor_command('release_gate', station_index=station_index)
    APP_STATE['status'] = 'waiting_motor'
    APP_STATE['message'] = f'{APP_STATE["active_fruit_id"]} 第 {station_index} 站照片已保存，等待 ESP32 放行第 {station_index} 閘門。'


def _mark_station_captured(station_index):
    station_statuses = APP_STATE.setdefault('station_statuses', {})
    station_statuses[str(station_index)] = 'captured'


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
    command = APP_STATE.get('motor_command')
    if command:
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
        'message': APP_STATE['message'],
    }


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
    if APP_STATE.get('active_fruit_id'):
        return False
    if APP_STATE.get('motor_command'):
        return False
    return not _has_unclassified_temp_fruit()


def _auto_trigger_disabled_reason():
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
    for key in [
        'status',
        'server_status',
        'auto_trigger_enabled',
        'command',
        'command_id',
        'station_index',
        'fruit_id',
        'home_angle',
        'release_angle',
        'timing_revision',
        'first_station_settle_ms',
        'servo_settle_ms',
        'fruit_settle_ms',
        'final_gate_return_delay_ms',
    ]:
        lines.append(f'{key}={payload.get(key, "")}')
    return '\n'.join(lines) + '\n'


def _mark_esp32_poll():
    APP_STATE['last_esp32_poll_at'] = _now_string()
    APP_STATE['last_esp32_poll_monotonic'] = time.monotonic()


def _mark_esp32_report():
    APP_STATE['last_esp32_report_at'] = _now_string()


def _esp32_is_online():
    last_poll = APP_STATE.get('last_esp32_poll_monotonic')
    return last_poll is not None and time.monotonic() - last_poll <= ESP32_ONLINE_WINDOW_SECONDS


def _start_wait_timer():
    APP_STATE['wait_started_at'] = _now_string()
    APP_STATE['wait_started_monotonic'] = time.monotonic()


def _clear_wait_timer():
    APP_STATE['wait_started_at'] = None
    APP_STATE['wait_started_monotonic'] = None


def _apply_session_timeouts():
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


def _set_error_state(reason, message):
    APP_STATE['pending_capture'] = False
    APP_STATE['motor_command'] = None
    APP_STATE['last_error_reason'] = reason
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


def _state_payload(extra=None):
    active_fruit_id = APP_STATE['active_fruit_id']
    latest_images = _build_image_list(active_fruit_id) if active_fruit_id else []
    image_total = len(latest_images)
    is_uploading = APP_STATE['status'] == 'uploading'
    can_classify = bool(active_fruit_id) and image_total == IMAGE_COUNT and APP_STATE['status'] == 'uploaded'
    can_discard = bool(active_fruit_id) and not is_uploading
    can_manual_capture = _auto_trigger_enabled()
    can_recapture = bool(active_fruit_id) and APP_STATE['status'] in (
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
        'capture_interval_ms': APP_STATE['capture_interval_ms'],
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
        'can_manual_capture': can_manual_capture,
        'can_discard': can_discard,
        'can_classify': can_classify,
        'can_recapture': can_recapture,
        'esp32_online': _esp32_is_online(),
        'last_esp32_poll_at': APP_STATE.get('last_esp32_poll_at'),
        'last_esp32_report_at': APP_STATE.get('last_esp32_report_at'),
        'motor_command': APP_STATE.get('motor_command') or _esp32_command_payload(),
        'last_error_reason': APP_STATE.get('last_error_reason'),
        'home_angle': HOME_ANGLE,
        'release_angle': RELEASE_ANGLE,
        'first_station_settle_ms': APP_STATE['capture_timing']['first_station_settle_ms'],
        'servo_settle_ms': APP_STATE['capture_timing']['servo_settle_ms'],
        'fruit_settle_ms': APP_STATE['capture_timing']['fruit_settle_ms'],
        'final_gate_return_delay_ms': APP_STATE['capture_timing']['final_gate_return_delay_ms'],
        **_capture_timing_state_payload(),
        'timing': _timing_payload(),
        'transition_trace': list(APP_STATE.get('transition_trace') or []),
        # ``trace`` is a small compatibility alias for early dashboard builds.
        'trace': list(APP_STATE.get('transition_trace') or []),
        'dataset_path': str(_dataset_root()),
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
        'capture': capture,
    }


def _sync_active_state_with_filesystem():
    _apply_session_timeouts()
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
    _reset_timing_state(keep_interval=True)


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
        'capture_interval_ms': APP_STATE['capture_interval_ms'],
        'phone_capture_timestamps_ms': APP_STATE['phone_capture_timestamps_ms'],
        'phone_capture_intervals_ms': APP_STATE['phone_capture_intervals_ms'],
        'client_timing': APP_STATE.get('client_timing') or {},
        'wait_started_at': APP_STATE.get('wait_started_at'),
    }


def _reset_timing_state(keep_interval=False):
    interval_ms = APP_STATE.get('capture_interval_ms', DEFAULT_CAPTURE_INTERVAL_MS)
    APP_STATE['command_created_at'] = None
    APP_STATE['command_created_monotonic'] = None
    APP_STATE['capture_started_at'] = None
    APP_STATE['capture_started_monotonic'] = None
    APP_STATE['command_to_phone_start_ms'] = None
    APP_STATE['phone_capture_timestamps_ms'] = []
    APP_STATE['phone_capture_intervals_ms'] = []
    APP_STATE['client_timing'] = {}
    APP_STATE['upload_received_at'] = None
    if keep_interval:
        APP_STATE['capture_interval_ms'] = interval_ms
    else:
        APP_STATE['capture_interval_ms'] = DEFAULT_CAPTURE_INTERVAL_MS


def _apply_capture_meta(raw_meta):
    if not raw_meta:
        return
    try:
        meta = json.loads(raw_meta)
    except (TypeError, json.JSONDecodeError):
        return

    timestamps = _number_list(meta.get('timestamps_ms'), limit=IMAGE_COUNT)
    intervals = _number_list(meta.get('intervals_ms'), limit=IMAGE_COUNT - 1)
    if timestamps:
        APP_STATE['phone_capture_timestamps_ms'] = timestamps
    if intervals:
        APP_STATE['phone_capture_intervals_ms'] = intervals
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


def _number_list(value, limit):
    if not isinstance(value, list):
        return []
    numbers = []
    for item in value[:limit]:
        try:
            numbers.append(round(float(item), 1))
        except (TypeError, ValueError):
            continue
    return numbers


def _parse_capture_interval_ms(value):
    interval_ms = _safe_int(value)
    if interval_ms is None:
        return DEFAULT_CAPTURE_INTERVAL_MS
    return max(MIN_CAPTURE_INTERVAL_MS, min(MAX_CAPTURE_INTERVAL_MS, interval_ms))


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
    last_attempt = APP_STATE.get('discard_cleanup_last_monotonic')
    now = time.monotonic()
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
    for image_file in Path(fruit_dir).glob('img_*.jpg'):
        _safe_unlink(image_file)


def _clear_staging_images(fruit_dir):
    for staging_file in Path(fruit_dir).glob(f'*{UPLOAD_STAGING_SUFFIX}'):
        _safe_unlink(staging_file)


def _replace_temp_images(fruit_dir, image_files):
    fruit_dir = Path(fruit_dir)
    staging_pairs = []
    try:
        _clear_staging_images(fruit_dir)
        for filename, image_file in zip(IMAGE_FILENAMES, image_files):
            target = fruit_dir / filename
            staging = fruit_dir / f'{filename}{UPLOAD_STAGING_SUFFIX}'
            with staging.open('wb') as output:
                for chunk in image_file.chunks():
                    output.write(chunk)
            staging_pairs.append((staging, target))

        _clear_temp_images(fruit_dir)
        for staging, target in staging_pairs:
            _safe_replace(staging, target)
    except Exception:
        _clear_staging_images(fruit_dir)
        raise


def _save_station_image(fruit_dir, station_index, image_file):
    return dataset_store.save_station_image(
        Path(fruit_dir),
        station_index,
        image_file,
        image_filenames=IMAGE_FILENAMES,
        staging_suffix=UPLOAD_STAGING_SUFFIX,
        safe_unlink=_safe_unlink,
        safe_replace=_safe_replace,
    )


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
    _write_counter(1)
    _write_reset_state({
        'reset_at': _now_string(),
        'ignored_paths': ignored_paths,
        'moved_paths': moved_paths,
    })
    return delete_warnings


def _try_remove_path(path):
    return _remove_or_archive_reset_path(path, datetime.now().strftime('reset_%Y%m%d_%H%M%S'))['warning']


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
    target = _delete_pending_dir() / reason / relative_path
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
