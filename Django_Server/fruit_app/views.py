import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from functools import cache, wraps
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import render
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import (
    api_payloads,
    assistant,
    capture_session,
    capture_timing,
    dataset_store,
    detection,
    detection_report,
    detection_stats,
    grading,
    motor_commands,
    notification_delivery,
    network_recovery,
    trend_alerts,
    webrtc_signaling,
)
from .dataset_store import DatasetFileBusyError, write_recovery_payload as _write_recovery_payload
from .runtime_state import RuntimeState
from .reason_text import REASON_TEXT, STATUS_TEXT, describe as describe_reason
from . import local_network
from .local_network import network_required


logger = logging.getLogger(__name__)
LABELS = ['上等', '中等', '下等', '加工']
CLASSIFICATION_CODES = grading.CLASSIFICATION_CODES
IMAGE_COUNT = dataset_store.IMAGE_COUNT
IMAGE_FILENAMES = dataset_store.IMAGE_FILENAMES
FRUIT_ID_PATTERN = dataset_store.FRUIT_ID_PATTERN
ESP32_SETUP_FAILURES = ('setup_ap_failed',)
ESP32_SETUP_BLOCK_REASON_PATTERN = re.compile(r'[a-z_]{1,48}')
ESP32_SETUP_URL = 'http://192.168.4.1/'  # Firmware WifiSetup page on the ESP32 softAP default address.
ESP32_SETUP_REQUEST_TTL_S = 15  # The idle ESP32 polls every 250 ms; an unclaimed request must not linger.
CAMERA_DIAGNOSTICS_ENUMS = {
    'event': ('camera_started', 'camera_failed', 'track_ended', 'rtc_state_changed'),
    'permission': ('granted', 'NotAllowedError', 'NotFoundError', 'NotReadableError',
                   'OverconstrainedError', 'SecurityError', 'AbortError', 'TypeError', 'unknown'),
    'video_track_state': ('live', 'ended', 'none'),
    'rtc_connection_state': ('new', 'connecting', 'connected', 'disconnected', 'failed', 'closed'),
    'rtc_ice_connection_state': ('new', 'checking', 'connected', 'completed', 'disconnected', 'failed', 'closed'),
}
CAMERA_DIAGNOSTICS_BOOLEANS = ('secure_context', 'get_user_media_available', 'track_muted')
CAMERA_DIAGNOSTICS_LIMITS = {'video_width': 10000, 'video_height': 10000, 'last_live_frame_age_ms': 86400000}
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
FEEDER_MAX_RUN_MS = 5000
CAPTURE_TIMING_RECOMMENDED = {
    'first_station_settle_ms': FIRST_STATION_SETTLE_MS,
    'servo_settle_ms': SERVO_SETTLE_MS,
    'fruit_settle_ms': FRUIT_SETTLE_MS,
    'final_gate_return_delay_ms': FINAL_GATE_RETURN_DELAY_MS,
    'idle_command_poll_interval_ms': IDLE_COMMAND_POLL_INTERVAL_MS,
    'feeder_stop_us': FEEDER_STOP_US,
    'feeder_drive_us': FEEDER_DRIVE_US,
    'feeder_max_run_ms': FEEDER_MAX_RUN_MS,
}
CAPTURE_TIMING_FIELDS = tuple(CAPTURE_TIMING_RECOMMENDED)
CAPTURE_TIMING_STEP_MS = 50
CAPTURE_TIMING_MIN_MS = 50
CAPTURE_TIMING_MAX_MS = 3000
CAPTURE_TIMING_FIELD_LIMITS = {
    'idle_command_poll_interval_ms': (100, 5000),
    'feeder_stop_us': (1400, 1600),
    'feeder_drive_us': (1000, 2000),
    'feeder_max_run_ms': (1000, 20000),
}
CAPTURE_TIMING_FIELD_STEPS = {
    'feeder_stop_us': 5,
    'feeder_drive_us': 10,
    'feeder_max_run_ms': 500,
}
CAPTURE_TIMING_FIELD_UNITS = {
    'feeder_stop_us': 'us',
    'feeder_drive_us': 'us',
}
ESP32_ONLINE_WINDOW_SECONDS = 6
ESP32_START_TIMEOUT_SECONDS = 10
HARDWARE_STEP_TIMEOUT_SECONDS = 20
CAMERA_UPLOAD_TIMEOUT_SECONDS = 45
# Photo-only detection pauses after each saved photo so operators can see it.
PHOTO_DETECTION_INTERVAL_SECONDS = 1.0
SORTER_PENDING_TIMEOUT_SECONDS = 30
SORTER_RUNNING_TIMEOUT_SECONDS = 30
CAMERA_READY_WINDOW_SECONDS = 5
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
    'work_mode': 'collection',
    'dataset_options_error': None,
    'session_work_mode': 'collection',
    'hardware_mode': 'hardware',
    'session_hardware_mode': 'hardware',
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
    'auto_run_enabled': False,
    'auto_feed_pending': False,
    'auto_run_finishing': False,
    'auto_run_recovery_reason': None,
    'camera_last_live_frame_at': None,
    'camera_last_live_frame_monotonic': None,
    'camera_diagnostics': None,
    'esp32_boot_id': None,
    'esp32_feeder_capable': False,
    'esp32_sorter_capable': False,
    'esp32_feeder_state': None,
    'esp32_feeder_sensor_state': None,
    'esp32_last_feed_command_id': 0,
    'gate3_waiting_boot_id': None,
    'last_completed_feed_command_id': 0,
    'feeder_test_result': None,
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
    'sorter_recovery_required': False,
    'photo_next_capture': None,
    'detection_status': 'idle',
    'detection_result': None,
    'detection_progress': None,
    'detection_error': None,
    'status': 'idle',
    'message': '等待手機相機與 ESP32 就緒。',
})
STATE_LOCK = APP_STATE.lock

NETWORK_RECOVERY = network_recovery.Policy(sys.modules[__name__])
_network_recovery_path = NETWORK_RECOVERY.path
_network_recovery_reason = NETWORK_RECOVERY.reason
_persist_network_recovery = NETWORK_RECOVERY.persist
_lock_network = NETWORK_RECOVERY.lock
_check_network_safety = NETWORK_RECOVERY.check_safety
_observe_device = NETWORK_RECOVERY.observe_device
_recovery_confirm_disabled_reason = NETWORK_RECOVERY.confirm_disabled_reason
_network_recovery_ui_reason = NETWORK_RECOVERY.ui_reason
_confirm_network_recovery = NETWORK_RECOVERY.confirm
_finish_network_recovery = NETWORK_RECOVERY.finish
_network_recovery_report = NETWORK_RECOVERY.report
WEBRTC_STATE = {
    'offer': None,
    'offer_id': 0,
    'answer': None,
    'answer_id': 0,
    'dashboard_ice': [],
    'camera_ice': [],
    'updated_at': None,
}


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


@csrf_exempt
def network_api(request):
    if request.method == 'GET':
        _ensure_dataset_structure()
        with STATE_LOCK:
            _check_network_safety()
            payload = local_network.NETWORK.snapshot()
            payload['setup'] = _network_setup_payload()
        return _no_store_response(JsonResponse(payload))
    if request.method != 'POST':
        return local_network.network_error('network_method_not_allowed', status=405)
    reason = local_network.NETWORK.request_reason(request, local_only=True)
    if reason:
        return local_network.network_error(reason)
    data = _request_data(request)
    if not isinstance(data.get('interface_id'), str) or not isinstance(data.get('ipv4'), str):
        return local_network.network_error('network_selection_invalid', status=400)
    _ensure_dataset_structure()
    with STATE_LOCK:
        _check_network_safety()
        _sync_active_state_with_filesystem()
        reason = NETWORK_RECOVERY.disabled_reason('network')
        if reason:
            return local_network.network_error(reason)
        try:
            payload = local_network.NETWORK.select(data['interface_id'], data['ipv4'])
        except local_network.NetworkError as exc:
            return local_network.network_error(exc.reason)
        APP_STATE['camera_last_live_frame_at'] = None
        APP_STATE['camera_last_live_frame_monotonic'] = None
        APP_STATE['camera_diagnostics'] = None
        APP_STATE['network_identity'] = (payload['server_run_id'], payload['network_revision'])
        APP_STATE['esp32_network_capable'] = False
        APP_STATE['last_esp32_poll_monotonic'] = None
        return _no_store_response(JsonResponse(payload))


@require_GET
def network_info_api(request):
    payload = local_network.NETWORK.snapshot()
    if payload['selected']:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
    if request.GET.get('format') == 'text':
        keys = ('api_version', 'scheme', 'selected', 'server_run_id', 'network_revision', 'server_url', 'camera_url')
        return _network_text_response({key: payload[key] for key in keys})
    return _no_store_response(JsonResponse(payload))


def _network_text_response(payload):
    body = '\n'.join(f'{key}={int(value) if isinstance(value, bool) else value}'
                     for key, value in payload.items()) + '\n'
    return _no_store_response(HttpResponse(body, content_type='text/plain'))


def _network_diagnostic_response(data, *, report=False, observation=False):
    try:
        payload = local_network.NETWORK.diagnostic(data, report=report, observation=observation)
    except local_network.NetworkError as exc:
        return local_network.network_error(exc.reason)
    return _network_text_response(payload)


@require_GET
def network_qr_api(request):
    payload = local_network.NETWORK.snapshot()
    if not payload['selected']:
        return local_network.network_error(payload['reason'])
    return _qr_svg_response(payload['camera_url'])


@require_GET
def network_setup_qr_api(request):
    # Fixed softAP address; it does not depend on the selected LAN, so no network selection is required.
    return _qr_svg_response(ESP32_SETUP_URL)


@csrf_exempt
def network_setup_request_api(request):
    # Remote BOOT press: the ESP32 still runs its own safe-idle check and the motion=setup grant handshake.
    if request.method != 'POST':
        return local_network.network_error('network_method_not_allowed', status=405)
    reason = local_network.NETWORK.request_reason(request, local_only=True)
    if reason:
        return local_network.network_error(reason)
    with STATE_LOCK:
        _check_network_safety()
        setup = _network_setup_payload()
        if setup['status'] != 'allowed':
            return local_network.network_error(setup['reason'] or 'esp32_setup_unavailable')
        APP_STATE['esp32_setup_request_deadline'] = time.monotonic() + ESP32_SETUP_REQUEST_TTL_S
    return _no_store_response(JsonResponse({'ok': True, 'setup_requested': True}))


def _esp32_setup_requested():
    deadline = APP_STATE.get('esp32_setup_request_deadline')
    return deadline is not None and time.monotonic() < deadline


def _qr_svg_response(text):
    import qrcode
    from qrcode.image.svg import SvgPathFillImage
    image = qrcode.make(text, image_factory=SvgPathFillImage)
    return _no_store_response(HttpResponse(image.to_string(), content_type='image/svg+xml'))


def _network_hardware_disabled_reason():
    if APP_STATE.get('esp32_setup_active'):
        return 'esp32_setup_active'
    if not APP_STATE.get('esp32_network_capable'):
        return 'network_capability_missing'
    return None


def _network_operation_active():
    return bool(APP_STATE.get('active_fruit_id') or APP_STATE.get('motor_command')
                or APP_STATE.get('auto_run_enabled') or APP_STATE.get('auto_run_finishing')
                or _sorter_busy())


def monitor_network_safety(stop):
    _ensure_dataset_structure()
    while not stop.is_set():
        with STATE_LOCK:
            _check_network_safety()
        stop.wait(1)


def _network_setup_reason():
    reason = NETWORK_RECOVERY.disabled_reason('setup')
    if reason:
        return reason
    if APP_STATE.get('auto_run_recovery_reason'):
        return APP_STATE['auto_run_recovery_reason']
    if APP_STATE.get('sorter_recovery_required'):
        return 'sorter_recovery_required'
    if APP_STATE.get('auto_run_enabled') or APP_STATE.get('auto_feed_pending'):
        return 'auto_run_active'
    if APP_STATE.get('gate3_waiting_fruit_id'):
        return 'gate3_waiting_for_classification'
    if _sorter_busy():
        return 'sorter_busy'
    if APP_STATE.get('motor_command'):
        return 'motor_command_pending'
    if APP_STATE.get('active_fruit_id') or _has_unclassified_temp_fruit():
        return 'active_fruit_exists'
    if APP_STATE.get('dataset_operation') or APP_STATE.get('pending_capture') or APP_STATE['status'] != 'idle':
        return 'capture_busy'
    return None


def _network_setup_payload():
    if not _esp32_is_online():
        return {'status': 'unknown', 'reason': 'esp32_offline'}
    if not APP_STATE.get('esp32_network_capable'):
        return {'status': 'unknown', 'reason': 'network_capability_missing'}
    reason = ('esp32_setup_active' if APP_STATE.get('esp32_setup_active')
              else _network_setup_reason() or APP_STATE.get('esp32_setup_failure'))
    if reason is None and not APP_STATE.get('esp32_setup_allowed'):
        detail = APP_STATE.get('esp32_setup_block_reason')
        reason = f'esp32_not_idle:{detail}' if detail else 'esp32_not_idle'
    return {'status': 'blocked' if reason else 'allowed', 'reason': reason}


def _control_fields(data=None):
    network = local_network.NETWORK.snapshot()
    allowed = network['selected'] and NETWORK_RECOVERY.control_allowed()
    if data is not None:
        allowed = allowed and _device_identity_matches(data)
    return {
        'server_run_id': network['server_run_id'], 'network_revision': network['network_revision'],
        'recovery_generation': APP_STATE.get('recovery_generation', 0),
        'boot_id': data.get('boot_id', '') if data is not None else APP_STATE.get('esp32_boot_id', ''),
        'control_allowed': bool(allowed), 'control_lease_ms': 6000 if allowed else 0,
        'setup_allowed': bool(allowed and _network_setup_reason() is None),
        'setup_requested': bool(allowed and data is not None and _network_setup_reason() is None
                                and _esp32_setup_requested()),
        'recovery_allowed': bool(NETWORK_RECOVERY.handshake_active
                                 and data is not None and _device_identity_matches(data)),
    }


def _device_identity_matches(data):
    network = local_network.NETWORK.snapshot()
    return (data.get('network_capability') == 'local_network_v1'
            and isinstance(data.get('boot_id'), str) and 1 <= len(data['boot_id']) <= 96
            and data.get('server_run_id') == network['server_run_id']
            and str(data.get('network_revision')) == str(network['network_revision'])
            and str(data.get('recovery_generation')) == str(APP_STATE.get('recovery_generation', 0)))


def control_response(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if request.GET.get('diagnostic') == '1':
            return response
        data = request.GET if request.method == 'GET' else _request_data(request)
        if data.get('event') in ('network_probe', 'network_discovery_status'):
            return response
        with STATE_LOCK:
            fields = _control_fields(data)
            if response.status_code >= 400:
                fields.update(control_allowed=False, control_lease_ms=0)
            if response.get('Content-Type', '').startswith('text/plain'):
                response.content += ('\n' + '\n'.join(f'{key}={int(value) if isinstance(value, bool) else value}'
                                                      for key, value in fields.items()) + '\n').encode()
            else:
                payload = json.loads(response.content)
                payload.update(fields)
                response = JsonResponse(payload, status=response.status_code)
        return _no_store_response(response)
    return wrapped


@require_GET
@control_response
@network_required(operation='device')
def esp32_heartbeat_api(request):
    with STATE_LOCK:
        reason = _observe_device(request.GET)
        if reason:
            return local_network.network_error(reason)
        return JsonResponse({'ok': True, 'command': 'none', 'command_id': 0})


def _recovery_can_confirm():
    return _recovery_confirm_disabled_reason() is None


# Recovery only homes the motors and clears the lock; HC-SR04, timing sync and feeder
# calibration stay required by the next production start, not by the confirmation.


STATIC_ROOT_DIR = Path(__file__).resolve().parent / 'static' / 'fruit_app'
CAMERA_ASSETS = (
    STATIC_ROOT_DIR / 'js' / 'camera.js',
    STATIC_ROOT_DIR / 'css' / 'camera.css',
)
DASHBOARD_ASSETS = (
    STATIC_ROOT_DIR / 'css' / 'dashboard.css',
    STATIC_ROOT_DIR / 'js' / 'dashboard.js',
    STATIC_ROOT_DIR / 'js' / 'assistant_render.js',
    STATIC_ROOT_DIR / 'js' / 'assistant.js',
)


def _asset_version(assets):
    # 頁面不快取但靜態檔會被瀏覽器沿用；以修改時間當版本，更新後強制重新下載。
    return str(max(int(path.stat().st_mtime) for path in assets))


@network_required(cleanup=True, operation='observation')
def camera_view(request):
    _ensure_dataset_structure()
    context = {'asset_version': _asset_version(CAMERA_ASSETS)}
    return _no_store_response(render(request, 'camera.html', context))


def dashboard_view(request):
    _ensure_dataset_structure()
    # Cached old scripts against a newer /api/state/ break the whole page.
    context = {'asset_version': _asset_version(DASHBOARD_ASSETS), 'reason_text': REASON_TEXT,
               'status_text': STATUS_TEXT}
    return _no_store_response(render(request, 'dashboard.html', context))


@require_GET
def state_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _retry_deferred_discards_if_due()
        _check_network_safety()
        _sync_active_state_with_filesystem()
        payload = _state_payload()
    return _no_store_response(JsonResponse(payload))


@require_GET
def camera_state_api(request):
    """Return only the fields needed by the high-frequency camera poller."""
    _ensure_dataset_structure()
    with STATE_LOCK:
        _check_network_safety()
        reason = local_network.NETWORK.request_reason(request)
        if reason and request.GET.get('camera_ready') == '1':
            return local_network.network_error(reason)
        if reason:
            payload = _camera_state_payload()
            payload['capture_requested'] = False
            payload['pending_capture'] = False
            payload['capture']['requested'] = False
            payload['reason'] = reason
            return _no_store_response(JsonResponse(payload))
        if request.GET.get('camera_ready') == '1':
            APP_STATE['camera_last_live_frame_at'] = _now_string()
            APP_STATE['camera_last_live_frame_monotonic'] = time.monotonic()
            _queue_pending_auto_feed()
        _apply_session_timeouts()
        payload = _camera_state_payload()
    return _no_store_response(JsonResponse(payload))


@csrf_exempt
@require_POST
def camera_diagnostics_api(request):
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
    try:
        # Bound the read itself; never hold the capture lock while reading the phone body.
        body = request.read(4097)
        if request.content_type != 'application/json' or len(body) > 4096:
            raise ValueError
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('event') is None:
            raise ValueError
        snapshot = {}
        for key, choices in CAMERA_DIAGNOSTICS_ENUMS.items():
            value = data.get(key)
            if value is not None and (not isinstance(value, str) or value not in choices):
                raise ValueError
            snapshot[key] = value
        for key in CAMERA_DIAGNOSTICS_BOOLEANS:
            value = data.get(key)
            if value is not None and type(value) is not bool:
                raise ValueError
            snapshot[key] = value
        for key, maximum in CAMERA_DIAGNOSTICS_LIMITS.items():
            value = data.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                raise ValueError
            snapshot[key] = value
    except (ValueError, UnicodeError, RecursionError):
        return _no_store_response(JsonResponse(
            {'ok': False, 'reason': 'camera_diagnostics_invalid'}, status=400))
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        snapshot['received_at'] = _now_string()
        APP_STATE['camera_diagnostics'] = snapshot
    return _no_store_response(JsonResponse({'ok': True}))


def get_camera_diagnostics():
    """Return an independent snapshot containing only validated scalar values."""
    with STATE_LOCK:
        snapshot = APP_STATE.get('camera_diagnostics')
        return None if snapshot is None else snapshot.copy()


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='counter')
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
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request, cleanup=True)
        if reason:
            return local_network.network_error(reason)
        fruit_id = dataset_store.format_fruit_id(next_id)
        if _fruit_id_exists(fruit_id):
            return _json_error(f'{fruit_id} 已有有效資料，請先重置 dataset 或改用其他起始 ID。', status=409)
        dataset_store.write_counter(_dataset_root(), next_id)
        APP_STATE['message'] = f'下一筆資料將從 {fruit_id} 開始。'
    return JsonResponse({'status': 'success', 'next_fruit_id': fruit_id})


@csrf_exempt
@require_POST
@network_required
def feeder_test_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        reason = _network_hardware_disabled_reason()
        if reason:
            return local_network.network_error(reason)
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
        APP_STATE['last_error_reason'] = None
        APP_STATE['last_error_command'] = None
        APP_STATE['last_error_command_id'] = None
        APP_STATE['status'] = 'waiting_feeder'
        APP_STATE['message'] = 'ESP32 正在測試送料一顆。'
        payload = _state_payload(extra={'ok': True, 'feeder_test_started': True})
    return JsonResponse(payload)


def _photo_only_session():
    return APP_STATE.get('session_hardware_mode') == 'photo_only'


def _options_disabled_reason(*, target_mode=None):
    reason = NETWORK_RECOVERY.disabled_reason('options', target_mode=target_mode)
    if reason:
        return reason
    if APP_STATE.get('detection_status') in ('pending', 'running'):
        return 'detection_in_progress'
    if APP_STATE.get('dataset_operation'):
        return 'dataset_operation_in_progress'
    ignore_motor = NETWORK_RECOVERY.ignores_motor_lock('options', target_mode)
    if APP_STATE.get('sorter_recovery_required') and not ignore_motor:
        return 'sorter_recovery_required'
    if APP_STATE.get('auto_run_recovery_reason') and not ignore_motor:
        return APP_STATE['auto_run_recovery_reason']
    if APP_STATE.get('auto_run_enabled') or APP_STATE.get('auto_run_finishing') or APP_STATE.get('auto_feed_pending'):
        return 'auto_run_active'
    if APP_STATE.get('active_fruit_id'):
        return 'active_fruit_exists'
    if APP_STATE.get('motor_command'):
        return 'pending_motor_command'
    if _sorter_busy():
        return 'classifier_busy'
    if not ignore_motor and APP_STATE.get('status') not in (
        'idle', 'classified', 'detection_completed', 'detection_failed',
    ):
        return 'system_not_idle'
    if not ignore_motor and APP_STATE.get('hardware_mode') == 'hardware' and APP_STATE.get('esp32_feeder_state') == 'awaiting_fruit':
        return 'feed_arrival_unconfirmed'
    if _has_unclassified_temp_fruit():
        return 'temp_fruit_exists'
    return None


def _photo_capture_disabled_reason():
    if APP_STATE.get('detection_status') in ('pending', 'running'):
        return 'detection_in_progress'
    if APP_STATE.get('hardware_mode') != 'photo_only':
        return 'photo_only_required'
    if APP_STATE.get('status') == 'uploading':
        return 'camera_busy'
    return _options_disabled_reason() or (None if _camera_is_ready() else 'camera_not_ready')


@csrf_exempt
@require_POST
@network_required(operation='options_request')
def collection_options_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    if data.get('work_mode') not in ('collection', 'detection'):
        return _json_error('工作模式無效。', reason='invalid_work_mode')
    if data.get('hardware_mode') not in ('hardware', 'photo_only'):
        return _json_error('硬體選項無效。', reason='invalid_hardware_mode')
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        reason = _options_disabled_reason(target_mode=data['hardware_mode'])
        if reason:
            return _json_error('目前流程尚未結束，無法切換選項。', status=409, reason=reason)
        try:
            dataset_store.write_recovery_payload(_dataset_options_path(), {
                'work_mode': data['work_mode'], 'hardware_mode': data['hardware_mode'],
            })
        except OSError as exc:
            return _json_error(f'無法保存模式：{exc}', status=503, reason='dataset_options_persist_failed')
        APP_STATE['work_mode'] = data['work_mode']
        APP_STATE['hardware_mode'] = data['hardware_mode']
        APP_STATE['session_work_mode'] = data['work_mode']
        APP_STATE['session_hardware_mode'] = data['hardware_mode']
        APP_STATE['detection_status'] = 'idle'
        APP_STATE['detection_result'] = None
        APP_STATE['detection_progress'] = None
        APP_STATE['detection_error'] = None
        APP_STATE['discard_cleanup_last_monotonic'] = None
        APP_STATE['status'] = 'idle'
        _ensure_dataset_structure()
        _sync_active_state_with_filesystem()
        APP_STATE['message'] = (
            (
                '已選擇自動檢測＋純拍攝，等待手機相機就緒。'
                if data['hardware_mode'] == 'photo_only'
                else '已選擇自動檢測＋使用硬體；本輪完成後 Gate 3 保持關閉。'
            )
            if data['work_mode'] == 'detection'
            else (
                '已選擇純拍攝，等待手機相機就緒。'
                if data['hardware_mode'] == 'photo_only'
                else '已選擇使用硬體。'
            )
        )
        _record_transition('collection_options_changed')
        return JsonResponse(_state_payload())


def _request_photo_capture(station_index):
    capture_session.request_station_capture(
        APP_STATE, fruit_id=APP_STATE['active_fruit_id'], station_index=station_index,
        now_string=_now_string, monotonic=time.monotonic, increment_token=True,
    )
    APP_STATE['message'] = f'純拍攝：等待第 {station_index}/{IMAGE_COUNT} 張照片。'
    _start_wait_timer()
    _record_transition('photo_capture_requested', station_index=station_index)


def _schedule_photo_capture(station_index):
    """Request the next detection photo only after the visible interval."""
    saved_index = station_index - 1
    APP_STATE['active_station_index'] = None
    APP_STATE['status'] = 'photo_interval'
    APP_STATE['photo_next_capture'] = {
        'station_index': station_index,
        'due_monotonic': time.monotonic() + PHOTO_DETECTION_INTERVAL_SECONDS,
    }
    APP_STATE['message'] = (
        f'純拍攝：第 {saved_index}/{IMAGE_COUNT} 張已保存，'
        f'等待 {PHOTO_DETECTION_INTERVAL_SECONDS:g} 秒後拍攝下一張。'
    )
    _clear_wait_timer()
    _record_transition('photo_interval_started', station_index=saved_index)
    _release_due_photo_capture()


def _release_due_photo_capture():
    scheduled = APP_STATE.get('photo_next_capture')
    if not scheduled or APP_STATE.get('status') != 'photo_interval':
        return
    if time.monotonic() < scheduled['due_monotonic']:
        return
    APP_STATE['photo_next_capture'] = None
    _request_photo_capture(scheduled['station_index'])


@csrf_exempt
@require_POST
@network_required(operation='start')
def photo_capture_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        if APP_STATE.get('hardware_mode') != 'photo_only':
            return _json_error('目前無法開始純拍攝。', status=409, reason='photo_only_required')
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        _sync_active_state_with_filesystem()
        if NETWORK_RECOVERY.needs_confirmation:
            response = _confirm_network_recovery(_request_data(request))
            if response is not None:
                return response
        reason = _photo_capture_disabled_reason()
        if reason:
            return _json_error('目前無法開始純拍攝。', status=409, reason=reason)
        fruit_id = dataset_store.format_fruit_id(dataset_store.next_available_number(
            dataset_store.load_counter(_dataset_root()), _fruit_id_exists,
        ))
        try:
            fruit_dir = _prepare_fruit_dir(fruit_id, 'photo_only')
            capture_time = _now_string()
            dataset_store.save_capture_session(
                fruit_dir, capture_time, APP_STATE['work_mode']
            )
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status, reason=exc.reason)
        except OSError as exc:
            try:
                dataset_store.safe_rmtree(fruit_dir)
            except DatasetFileBusyError:
                pass  # The existing temp cleanup will retry an empty folder.
            return _json_error(f'無法保存本輪選項：{exc}', status=503, reason='capture_options_persist_failed')
        _start_new_capture_session(fruit_id, 'photo_only', 'waiting_camera', '純拍攝開始。')
        APP_STATE['detection_status'] = (
            'capturing' if APP_STATE['work_mode'] == 'detection' else 'idle'
        )
        APP_STATE['detection_result'] = None
        APP_STATE['detection_progress'] = None
        APP_STATE['detection_error'] = None
        APP_STATE['capture_time'] = capture_time
        # Stay within JavaScript's exact integer range and across hardware resets.
        APP_STATE['capture_token'] = secrets.randbits(48)
        _request_photo_capture(1)
        return JsonResponse(_state_payload(extra={'ok': True}))


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='start')
def auto_run_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    enabled = data.get('enabled')
    if not isinstance(enabled, bool):
        return _json_error(
            'enabled 必須是布林值。',
            status=400,
            reason='invalid_auto_run_enabled',
        )

    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        _check_network_safety()
        reason = local_network.NETWORK.request_reason(request, cleanup=True)
        if reason:
            return local_network.network_error(reason)
        if enabled:
            reason = local_network.NETWORK.request_reason(request) or _network_hardware_disabled_reason()
            if reason:
                return local_network.network_error(reason)
        _sync_active_state_with_filesystem()
        if enabled and APP_STATE.get('hardware_mode') == 'photo_only':
            return _json_error('純拍攝不啟用自動送料。', status=409, reason='hardware_mode_required')
        if enabled and NETWORK_RECOVERY.needs_confirmation:
            return _confirm_network_recovery(data)
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

        if APP_STATE.get('sorter_recovery_required') and data.get('recovery_confirmed') is not True:
            return _json_error(
                'Django 在分類器結果確認前重新啟動；請檢查 Gate 3 與分類器，確認安全後再復原。',
                status=409,
                reason='django_restarted_during_sorter',
            )
        recovery_reason = APP_STATE.get('auto_run_recovery_reason')
        if (
            recovery_reason == 'gate3_manual_removal_required'
            and data.get('recovery_confirmed') is not True
        ):
            return _json_error(
                '請先依主畫面提示完成人工安全復原，再明確確認開始下一輪。',
                status=409,
                reason=recovery_reason,
            )

        reason = _auto_run_disabled_reason(allow_recovery=True)
        if reason:
            return _json_error(
                '目前無法開始自動運轉，請確認相機、ESP32 與硬體參數同步皆已就緒。',
                status=409,
                reason=reason,
            )

        recovery_confirmed = bool(APP_STATE.get('auto_run_recovery_reason'))
        if APP_STATE.get('sorter_recovery_required'):
            try:
                _clear_sorter_recovery_marker()
            except OSError as exc:
                return _json_error(
                    f'無法清除分類器復原鎖：{exc}',
                    status=503,
                    reason='sorter_recovery_clear_failed',
                )
            _reset_sorter_state()
        if recovery_reason == 'gate3_manual_removal_required':
            try:
                dataset_store.clear_recovery_marker(_gate3_recovery_path())
            except OSError as exc:
                return _json_error(
                    f'無法清除 Gate 3 人工復原鎖：{exc}',
                    status=503,
                    reason='gate3_recovery_clear_failed',
                )
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
@control_response
@network_required(operation='device')
def esp32_trigger_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        reason = _observe_device(_request_data(request))
        if reason:
            return local_network.network_error(reason)
        if NETWORK_RECOVERY.locked:
            return local_network.network_error(_network_recovery_reason())
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
@control_response
@network_required(operation='device')
def esp32_command_api(request):
    if request.GET.get('diagnostic') == '1':
        return _network_diagnostic_response(request.GET)
    _ensure_dataset_structure()
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        reason = _observe_device(request.GET)
        if reason:
            return local_network.network_error(reason)
        if not NETWORK_RECOVERY.control_allowed():
            payload = NETWORK_RECOVERY.command_payload()
            return _network_text_response(payload) if request.GET.get('format') == 'text' else JsonResponse(payload)
        APP_STATE['last_esp32_poll_at'] = _now_string()
        APP_STATE['last_esp32_poll_monotonic'] = time.monotonic()
        previous_boot_id = APP_STATE.get('esp32_boot_id')
        boot_id = (request.GET.get('boot_id') or '').strip() or None
        feeder_state = (request.GET.get('feeder_state') or '').strip()
        current_command = APP_STATE.get('motor_command') or {}
        command_name = current_command.get('command')
        command_id = current_command.get('command_id')
        restarted = previous_boot_id and boot_id and boot_id != previous_boot_id
        restart_details = {'previous_boot_id': previous_boot_id, 'boot_id': boot_id}
        if restarted and command_name == 'feed_one':
            APP_STATE['motor_command'] = None
            APP_STATE['auto_run_recovery_reason'] = 'esp32_restarted_during_feed'
            APP_STATE['status'] = 'error'
            _stop_for_esp32_restart(
                'esp32_restarted_during_feed', command_name, command_id,
                'ESP32 在送料完成確認前重新啟動；已停止自動運轉且不重送，'
                '請暫停並檢查送料區域。',
                details=restart_details,
            )
        elif (restarted and APP_STATE.get('active_fruit_id')
              and command_name in ('start_sequence', 'release_gate')):
            APP_STATE['motor_command'] = None
            APP_STATE['pending_capture'] = False
            APP_STATE['gate3_waiting_boot_id'] = None
            APP_STATE['auto_run_recovery_reason'] = 'esp32_restarted_during_capture'
            APP_STATE['status'] = 'error'
            _clear_wait_timer()
            _stop_for_esp32_restart(
                'esp32_restarted_during_capture', command_name, command_id,
                'ESP32 在三站拍攝動作完成確認前重新啟動；已停止且不重送命令，'
                '請檢查 Gate 與果實位置後重新拍攝或刪除。',
                details=restart_details,
            )
        elif restarted and command_name == 'classify_fruit':
            APP_STATE['motor_command'] = None
            APP_STATE['auto_run_recovery_reason'] = 'esp32_restarted_during_sorter'
            APP_STATE['sorter_status'] = 'failed'
            APP_STATE['sorter_error'] = 'esp32_restarted_during_sorter'
            APP_STATE['sorter_deadline_monotonic'] = None
            APP_STATE['sorter_recovery_required'] = True
            APP_STATE['status'] = 'error'
            _stop_for_esp32_restart(
                'esp32_restarted_during_sorter', command_name, command_id,
                'ESP32 在複合分類動作完成確認前重新啟動；已停止自動運轉且不重送，'
                'Dataset 保持已提交，請檢查 Gate 3 與分類器。',
                fruit_id=APP_STATE.get('sorter_fruit_id'),
                details=restart_details,
            )
            try:
                _write_sorter_recovery_marker(
                    phase='rebooted',
                    reason='esp32_restarted_during_sorter',
                )
            except OSError:
                pass
        elif (
            restarted
            and APP_STATE.get('active_fruit_id')
            and APP_STATE.get('status') in (
                'uploaded', 'detection_pending', 'detection_running',
                'detection_completed', 'detection_failed',
            )
            and not _photo_only_session()
        ):
            APP_STATE['gate3_waiting_boot_id'] = None
            _stop_for_esp32_restart(
                'esp32_restarted_while_gate3_waiting', 'classify_fruit', None,
                'ESP32 在 Gate 3 等待分類時重新啟動；已保留暫存照片與果實，'
                '請依斷電程序檢查並重新拍攝或刪除。',
                details=restart_details,
            )
        APP_STATE['esp32_boot_id'] = boot_id
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
            APP_STATE.get('hardware_mode') != 'photo_only'
            and feeder_state == 'awaiting_fruit'
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
@control_response
@network_required(operation='device')
def esp32_report_api(request):
    data = _request_data(request)
    event = (data.get('event') or '').strip()
    if event == 'network_probe':
        return _network_diagnostic_response(data, report=True)
    if event == 'network_discovery_status':
        return _network_diagnostic_response(data, observation=True)
    _ensure_dataset_structure()
    command_id = _safe_int(data.get('command_id'))

    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        reason = _observe_device(data)
        if reason:
            return local_network.network_error(reason)
        if event in ('network_recovery_completed', 'network_recovery_committed'):
            return _network_recovery_report(data)
        if NETWORK_RECOVERY.locked:
            if event == 'network_recovery_persist_failed':
                _lock_network('recovery_persist_failed', force=True)
            return JsonResponse({'ok': True, 'ignored': True, 'reason': _network_recovery_reason()})
        APP_STATE['last_esp32_report_at'] = _now_string()
        if APP_STATE.get('hardware_mode') == 'photo_only':
            return JsonResponse(_compact_esp32_payload({
                'ok': True, 'event': event, 'ignored': True, 'reason': 'photo_only_mode',
            }))
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
@network_required
def recapture_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        if not _photo_only_session() and _network_hardware_disabled_reason():
            return local_network.network_error(_network_hardware_disabled_reason())
        _sync_active_state_with_filesystem()
        if _sorter_busy():
            return _json_error(
                '硬體分類器正在執行，完成或逾時前不可重新拍攝。',
                status=409,
                reason='classifier_busy',
            )
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重新拍攝。', status=409)
        if _gate3_manual_removal_required():
            _disable_auto_run()
            return _json_error(
                '果實仍在 Gate 3；請依斷電程序安全移除並重新放回起點後，再建立新流程。',
                status=409,
                reason='gate3_manual_removal_required',
            )
        if APP_STATE.get('sorter_recovery_required'):
            return _json_error(
                '分類器結果仍需人工安全復原，不可重新拍攝。',
                status=409,
                reason='django_restarted_during_sorter',
            )
        fruit_id = APP_STATE['active_fruit_id']
        if not fruit_id:
            return _json_error('目前沒有可重新拍攝的資料。', status=409)

        if _photo_only_session():
            return _json_error('請刪除本輪後重新開始純拍攝。', status=409, reason='photo_only_discard_required')
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
            dataset_store.clear_temp_images(fruit_dir, clear_image_cache=_load_image_list.cache_clear)
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
@network_required
def capture_started_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        if not _photo_only_session() and _network_hardware_disabled_reason():
            return local_network.network_error(_network_hardware_disabled_reason())
        options_error = _dataset_options_error_response()
        if options_error is not None:
            return options_error
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


def _set_capture_restart_recovery(fruit_id, *, photo_saved):
    APP_STATE['pending_capture'] = False
    APP_STATE['motor_command'] = None
    APP_STATE['gate3_waiting_boot_id'] = None
    _disable_auto_run()
    APP_STATE['auto_run_finishing'] = False
    APP_STATE['auto_run_recovery_reason'] = 'esp32_restarted_during_capture'
    APP_STATE['last_error_reason'] = 'esp32_restarted_during_capture'
    APP_STATE['last_error_command'] = None
    APP_STATE['last_error_command_id'] = None
    APP_STATE['status'] = 'error'
    photo_result = '照片已保留' if photo_saved else '照片寫入失敗'
    APP_STATE['message'] = (
        f'ESP32 在照片寫入期間重新啟動；{photo_result}，不會確認或放行 Gate 3，'
        '請檢查果實與閘門位置後重新拍攝或刪除。'
    )
    _clear_wait_timer()


def _detection_session(fruit_dir):
    """Result identity and capture metadata persisted at session start."""
    try:
        options = dataset_store.read_capture_options(Path(fruit_dir))
    except (OSError, ValueError):
        options = {}
    result_id = options.get('result_id')
    if not result_id:
        # Legacy or unreadable session: derive an id that the job and the
        # restart path agree on for this temp folder.
        try:
            created = Path(fruit_dir).stat().st_ctime_ns
        except OSError:
            created = options.get('capture_time') or ''
        result_id = uuid.uuid5(uuid.NAMESPACE_URL, f'{Path(fruit_dir).resolve()}|{created}').hex
    return {
        'result_id': result_id,
        'capture_time': options.get('capture_time') or APP_STATE.get('capture_time'),
        'hardware_mode': options.get('hardware_mode') or APP_STATE.get('session_hardware_mode'),
    }


def _detection_message(fruit_id, result):
    if result is None:
        return f'{fruit_id} 檢測未完整完成，待人工處理；已保留原圖與可用產物。'
    storage = result.get('storage') or {}
    if storage.get('status') == 'failed':
        return (
            f'{fruit_id} 檢測結果保存失敗（{describe_reason(storage.get("reason"))}），不視為完成；'
            '請檢查磁碟後刪除本輪重拍。'
        )
    classification = result.get('classification') or {}
    if result.get('complete'):
        return (
            f'{fruit_id} 檢測完成：整顆等級 {classification.get("label")}'
            '（三張取最低級），結果已保存。'
        )
    reason = classification.get('reason') or result.get('reason')
    return (
        f'{fruit_id} 檢測未完整完成，待人工處理（{describe_reason(reason)}）；'
        '已保留原圖與可用產物。'
    )


def _publish_detection_progress(operation_token, fruit_id, stage, images):
    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return
        APP_STATE['detection_progress'] = {
            'fruit_id': fruit_id, 'stage': stage, 'images': images,
        }
        _record_transition(
            'detection_stage_saved', fruit_id=fruit_id, details={'stage': stage},
        )


def _run_detection_job(operation_token, fruit_id, fruit_dir, session):
    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return
        APP_STATE['detection_status'] = 'running'
        APP_STATE['status'] = 'detection_running'
        APP_STATE['message'] = f'{fruit_id} 正在執行四模型檢測與 XGBoost 判級。'

    try:
        result = detection.run_and_save(
            fruit_id=fruit_id,
            image_paths=[
                Path(fruit_dir) / filename for filename in IMAGE_FILENAMES
            ],
            output_root=_detection_output_root(),
            summary_path=_detection_summary_path(),
            model_paths=dict(settings.DETECTION_MODEL_PATHS),
            grade_model_paths=dict(settings.GRADE_MODEL_PATHS),
            model_options=dict(
                getattr(settings, 'DETECTION_MODEL_OPTIONS', {})
            ),
            progress=lambda stage, images: _publish_detection_progress(
                operation_token, fruit_id, stage, images,
            ),
            **session,
        )
        error = None
    except Exception as exc:
        result = None
        error = f'{type(exc).__name__}: {exc}'
    notice = _queue_notification(result) + _check_trend_alerts(result)

    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            return
        APP_STATE.finish_dataset_operation(operation_token)
        complete = bool(result and result.get('complete'))
        APP_STATE['detection_status'] = (
            'completed' if complete else 'failed'
        )
        APP_STATE['detection_result'] = result
        APP_STATE['detection_progress'] = None
        APP_STATE['detection_error'] = error or (
            None if complete else (result or {}).get('reason')
        )
        terminal_status = (
            'detection_completed' if complete else 'detection_failed'
        )
        message = _detection_message(fruit_id, result) + notice
        if _photo_only_session():
            if _saved_detection_result(fruit_id) is not None:
                message += _clear_saved_photo_input(fruit_dir, fruit_id)
            _clear_active_state(message, status=terminal_status)
            return
        if complete:
            _start_saved_detection_sorting(result, notice)
            return
        APP_STATE['status'] = terminal_status
        APP_STATE['message'] = (
            f'{message} Gate 3 仍保持關閉，等待人工判級或安全移除。'
        )
        if not result or (result.get('storage') or {}).get('status') != 'saved':
            _disable_auto_run()
            APP_STATE['auto_run_finishing'] = False
        _check_network_safety()
        _clear_wait_timer()
        _record_transition(
            terminal_status,
            fruit_id=fruit_id,
            details={'complete': complete, 'gate3_locked': True},
        )


def _sorter_start_disabled_reason():
    """Shared hardware conditions for collection and detection classification."""
    _check_network_safety()
    reason = NETWORK_RECOVERY.disabled_reason('capture')
    if reason:
        return reason
    if APP_STATE.get('sorter_recovery_required'):
        return 'sorter_recovery_required'
    reason = _network_hardware_disabled_reason()
    if reason:
        return reason
    if not _esp32_is_online():
        return 'esp32_offline'
    if not APP_STATE.get('esp32_sorter_capable'):
        return 'sorter_capability_missing'
    boot_id = APP_STATE.get('esp32_boot_id')
    if not boot_id or APP_STATE.get('gate3_waiting_boot_id') != boot_id:
        return 'gate3_boot_unconfirmed'
    return None


def _start_saved_detection_sorting(result, notice=''):
    """Consume a saved grade once; never retry physical sorting from disk.

    ``notice`` (the LINE queue outcome) is appended to whatever sorting message
    results, so hardware rounds report it as photo-only rounds do.
    """
    _start_sorting_saved_grade(result)
    APP_STATE['message'] += notice


def _start_sorting_saved_grade(result):
    fruit_id = result['fruit_id']
    classification = result.get('classification') or {}
    label = classification.get('label')
    code = classification.get('code')
    reason = None
    if (result.get('storage') or {}).get('status') != 'saved':
        reason = 'result_not_saved'
    elif classification.get('status') != 'graded' or label not in LABELS or CLASSIFICATION_CODES[label] != code:
        reason = 'invalid_classification'
    elif APP_STATE.get('active_fruit_id') != fruit_id:
        reason = 'stale_detection_result'
    else:
        reason = _sorter_start_disabled_reason()
    if reason:
        _set_sorter_failed(fruit_id, label, code, reason)
        return
    try:
        # Intent survives a crash between committed data and command publication.
        dataset_store.write_sorter_recovery_intent(
            _sorter_recovery_path(), fruit_id, label, code,
        )
        APP_STATE['sorter_recovery_required'] = True
        dataset_store.write_counter(_dataset_root(), max(dataset_store.load_counter(_dataset_root()), dataset_store.parse_fruit_number(fruit_id) + 1))
        dataset_store.discard_temp_fruit(
            _dataset_root(), _temp_dir() / fruit_id, fruit_id,
            now_string=_now_string, fruit_id_exists=_fruit_id_exists,
        )
    except (OSError, DatasetFileBusyError) as exc:
        _set_sorter_failed(fruit_id, label, code, 'detection_sorter_prepare_failed')
        APP_STATE['message'] += f'：{exc}'
        return
    _clear_classified_dataset_state(fruit_id, label)
    _record_transition('fruit_classified', fruit_id=fruit_id, details={'label': label})
    _queue_sorter_command(fruit_id, label)


MANUAL_GRADE_MESSAGES = {
    'not_detection_mode': '目前不是自動檢測模式。',
    'no_detection_result': '目前沒有待人工處理的檢測結果。',
    'dataset_busy': '已有資料操作進行中，請稍後再試。',
    'network_recovery_required': '請先完成網路安全復原。',
    'sorter_recovery_required': '分類器結果不確定，請先完成安全復原。',
    'esp32_offline': 'ESP32 已離線，Gate 3 維持關閉。',
    'sorter_capability_missing': 'Firmware 缺少 gate3_sorter_v1，不能放行。',
    'gate3_boot_unconfirmed': 'Gate 3 等待狀態無法與目前 ESP32 boot 確認，請安全復原。',
    'hardware_abandon_requires_discard': '使用硬體時請用跳過／刪除本輪，並依斷電程序安全移除果實。',
    'result_not_saved': '本輪檢測結果尚未保存成功，無法人工接手；請刪除本輪重拍。',
    'already_manual_graded': '本輪已由人工處理（判級或放棄）。',
    'not_needs_review': '本輪已由模型判級，不需人工接手。',
    'stale_detection_result': '畫面上的檢測結果已變更，請重新確認本輪照片。',
    'result_not_found': '找不到本輪檢測結果檔，無法人工接手。',
}


def _manual_grade_disabled_reason():
    """Why the current detection result cannot take a manual grade, if any."""
    if APP_STATE['work_mode'] != 'detection':
        return 'not_detection_mode'
    result = APP_STATE.get('detection_result')
    if not result:
        return 'no_detection_result'
    if APP_STATE.get('dataset_operation'):
        return 'dataset_busy'
    reason = NETWORK_RECOVERY.disabled_reason('capture')
    if reason:
        return reason
    if (result.get('storage') or {}).get('status') != 'saved':
        return 'result_not_saved'
    classification = result.get('classification') or {}
    if classification.get('source') == detection.MANUAL_SOURCE:
        return 'already_manual_graded'
    if classification.get('status') != 'needs_review':
        return 'not_needs_review'
    if result.get('hardware_mode') != 'photo_only':
        return _sorter_start_disabled_reason()
    return None


@csrf_exempt
@require_POST
@network_required
def manual_grade_api(request):
    """Save an operator decision before entering the shared sorter path."""
    data = _request_data(request)
    abandon = data.get('abandon') is True
    label = None if abandon else data.get('label')
    if not abandon and label not in LABELS:
        return _json_error(
            '分類必須是上等、中等、下等或加工，或明確放棄本輪。',
            status=400, reason='invalid_label',
        )
    grade = None if abandon else grading.grade_for_label(label)
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        current = APP_STATE.get('detection_result') or {}
        if (
            current
            and (data.get('fruit_id') != current.get('fruit_id')
                 or data.get('result_id') != current.get('result_id'))
        ):
            reason = 'stale_detection_result'
        else:
            reason = _manual_grade_disabled_reason()
        duplicate = reason == 'already_manual_graded' and (
            current.get('classification') or {}
        ).get('grade') == grade
        if reason and not duplicate:
            return _json_error(
                MANUAL_GRADE_MESSAGES.get(reason, f'無法人工判級：{describe_reason(reason)}'), status=409, reason=reason,
            )
        if abandon and current.get('hardware_mode') != 'photo_only':
            return _json_error(MANUAL_GRADE_MESSAGES['hardware_abandon_requires_discard'],
                               status=409, reason='hardware_abandon_requires_discard')
        fruit_id = current['fruit_id']
        operation_token = APP_STATE.begin_dataset_operation(
            'manual_grade', fruit_id,
        )

    try:
        outcome = detection.apply_manual_decision(
            output_root=_detection_output_root(),
            summary_path=_detection_summary_path(),
            fruit_id=fruit_id,
            result_id=data['result_id'],
            grade=grade,
        )
    except Exception:
        with STATE_LOCK:
            APP_STATE.finish_dataset_operation(operation_token)
        raise

    notice = _queue_notification(outcome.get('result')) + _check_trend_alerts(outcome.get('result'))
    with STATE_LOCK:
        APP_STATE.finish_dataset_operation(operation_token)
        if not outcome['ok']:
            return _manual_grade_failure_response(outcome)
        if (APP_STATE.get('detection_result') or {}).get('result_id') == data['result_id']:
            APP_STATE['detection_result'] = outcome['result']
            APP_STATE['detection_error'] = None
            decision = '已放棄本輪、不分級' if abandon else f'已人工判為「{label}」'
            if current.get('hardware_mode') == 'photo_only':
                APP_STATE['message'] = (
                    f'{fruit_id} {decision}並更新檢測紀錄；'
                    '原檢測失敗資訊仍保留。純拍攝不啟動硬體或下一輪。' + notice
                )
            elif not outcome['duplicate']:
                _start_saved_detection_sorting(outcome['result'], notice)
            elif APP_STATE.get('active_fruit_id') == fruit_id:
                _set_sorter_failed(fruit_id, label, CLASSIFICATION_CODES[label],
                                   'manual_decision_uncertain')
        _record_transition(
            'detection_manual_decided', fruit_id=fruit_id,
            details={'label': label, 'abandoned': abandon,
                     'duplicate': outcome['duplicate']},
        )
        return JsonResponse(_state_payload(extra={
            'manual_grade_saved': True,
            'duplicate': outcome['duplicate'],
            'fruit_id': fruit_id,
        }))


def _manual_grade_failure_response(outcome):
    reason = outcome['reason']
    if reason not in ('result_json_save_failed', 'summary_save_failed'):
        return _json_error(MANUAL_GRADE_MESSAGES[reason], status=409, reason=reason)
    message = f'人工判級保存失敗，本輪維持待人工處理：{outcome["detail"]}'
    if outcome.get('rollback_error'):
        # result.json already holds the grade; only the same grade resyncs it.
        message = (
            f'人工判級已寫入 result.json，但總表保存與還原都失敗：'
            f'{outcome["detail"]}；{outcome["rollback_error"]}。'
            '請排除磁碟問題後以同一等級重送，以同步總表。'
        )
    return _json_error(message, status=503, reason=reason)


def notification_outbox():
    runtime_root = Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).parent
    return notification_delivery.Outbox(
        runtime_root / 'notification_outbox.json',
        runtime_root / 'notification_credentials.json',
    )


def _queue_notification(result):
    """Queue a saved processing result for LINE; return a message suffix.

    Runs outside ``STATE_LOCK`` and never raises: notifications must not block
    detection, manual takeover or sorting.
    """
    if not result or not result.get('fruit_id'):
        return ''
    try:
        queued = notification_outbox().enqueue(
            result, _detection_output_root() / result['fruit_id'] / 'result.json',
        )
    except (OSError, ValueError) as exc:
        return f' LINE 通知未能排入待送：{exc}'
    return ' 已排入 LINE 加工果通知待送。' if queued['queued'] else ''


def trend_alert_monitor():
    runtime_root = Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).parent
    return trend_alerts.TrendAlerts(
        runtime_root / 'trend_alerts.json', _dataset_root('detection'), notification_outbox(),
    )


def _check_trend_alerts(result):
    """Start the trend rules after any saved grade; never raises.

    Statistics run on their own daemon thread, so detection, manual
    takeover and sorting never wait for the check. New
    alerts show on the Dashboard and in the LINE outbox.
    """
    if (not result
            or (result.get('storage') or {}).get('status') != 'saved'
            or (result.get('classification') or {}).get('status') != 'graded'):
        return ''
    try:
        threading.Thread(target=_run_trend_check, name='trend-alert-check', daemon=True).start()
    except RuntimeError as exc:
        return f' 趨勢提醒檢查未啟動：{exc}'
    return ''


def _run_trend_check():
    try:
        outcome = trend_alert_monitor().check()
    except Exception:
        logger.exception('Trend alert check failed')
        return
    for error in outcome['errors']:
        logger.warning('Trend alert could not be queued for LINE: %s', error)


def _trend_alert_payload():
    try:
        return trend_alert_monitor().status()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'reason': 'trend_alerts_unreadable',
                'error': f'趨勢提醒設定無法讀取：{exc}'}


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='notification')  # Alert settings never move hardware.
def trend_alerts_api(request):
    """Save the trend-alert switch, windows and thresholds from the Dashboard."""
    try:
        changes = _request_data(request)
        changes.pop('dataset_mode', None)  # Added by every Dashboard POST; not a setting.
        trend_alert_monitor().update_settings(changes)
    except ValueError as exc:
        return _json_error(str(exc), reason='invalid_trend_alert_settings')
    except OSError as exc:
        return _json_error(f'無法保存趨勢提醒設定：{exc}', status=503,
                           reason='trend_alert_settings_persist_failed')
    with STATE_LOCK:
        return JsonResponse(_state_payload())


def get_system_status():
    """Read the assistant whitelist through Dashboard helpers, without dataset synchronization."""
    with STATE_LOCK:
        alert = _operator_alert_payload()
        payload = {key: APP_STATE.get(key) for key in (
            'work_mode', 'hardware_mode', 'status', 'message',
            'esp32_feeder_capable', 'esp32_sorter_capable',
            'auto_run_recovery_reason', 'last_error_reason', 'sorter_recovery_required',
        )}
        last_poll = APP_STATE.get('last_esp32_poll_monotonic')
        last_frame = APP_STATE.get('camera_last_live_frame_monotonic')
        recovery = APP_STATE.get('network_recovery')
        payload['network_recovery'] = {'reason': recovery.get('reason')} if recovery else None
        payload['operator_alert'] = (
            {key: alert.get(key) for key in ('reason', 'location', 'instruction')} if alert else None
        )
        payload.update(
            esp32_online=_esp32_is_online(), camera_ready=_camera_is_ready(),
            gate3_manual_removal_required=_gate3_manual_removal_required(),
            auto_run_disabled_reason=_auto_run_disabled_reason(),
            photo_capture_disabled_reason=_photo_capture_disabled_reason(),
            feeder_test_disabled_reason=_feeder_test_disabled_reason(),
            network_recovery_required=NETWORK_RECOVERY.needs_confirmation,
        )
        setup = _network_setup_payload()
    now = time.monotonic()
    payload['message'] = assistant.redact_secret_files(payload['message'] or '')
    payload.update(
        ok=True, checked_at=_now_string(),
        last_esp32_poll_seconds_ago=None if last_poll is None else max(0, int(now - last_poll)),
        camera_last_live_frame_seconds_ago=None if last_frame is None else max(0, int(now - last_frame)),
        camera_diagnostics=get_camera_diagnostics(),
    )
    network = local_network.NETWORK.snapshot()
    payload['network'] = {key: network.get(key) for key in ('selected', 'reason')}
    payload['network']['setup'] = {key: setup.get(key) for key in ('status', 'reason')}
    payload['photo_capture_disabled_reason'] = (
        payload['photo_capture_disabled_reason'] if network['selected'] else network['reason']
    )
    payload['esp32_diagnostics'] = {
        key: network['esp32'].get(key) for key in ('status', 'source', 'reason', 'online')
    }
    line = notification_outbox().status(payload['hardware_mode'])
    payload['line'] = {key: line[key] for key in (
        'enabled', 'configured', 'config_reason', 'config_missing_fields',
    )}
    payload['models'] = {}
    for role, model_path in {**settings.DETECTION_MODEL_PATHS, **settings.GRADE_MODEL_PATHS}.items():
        path = Path(model_path)
        try:
            with path.open('rb') as model_file:
                header = model_file.read(64)
            status = 'lfs_pointer' if header.startswith(b'version https://git-lfs.github.com/spec/') else 'ok'
        except FileNotFoundError:
            status = 'missing'
        payload['models'][role] = {'name': path.name, 'status': status}
    values = []

    def collect_codes(value):
        if isinstance(value, dict):
            for item in value.values():
                collect_codes(item)
        elif isinstance(value, list):
            for item in value:
                collect_codes(item)
        elif isinstance(value, str) and value in REASON_TEXT:
            values.append(value)

    collect_codes(payload)
    payload['reason_text'] = {code: REASON_TEXT[code] for code in values}
    return payload


def dashboard_assistant():
    runtime_root = Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).parent
    return assistant.Assistant(
        runtime_root / 'assistant_credentials.json', runtime_root / 'assistant_usage.json',
        _dataset_root('detection'), Path(settings.BASE_DIR).parent / 'docs',
        trend_status=lambda: trend_alert_monitor().status(include_details=True),
        system_status=get_system_status,
    )


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='assistant')  # Status takes STATE_LOCK only before Gemini requests.
def assistant_api(request):
    """Answer one Dashboard chat turn; expected failures return a reason."""
    try:
        payload = dashboard_assistant().ask(_request_data(request).get('messages'))
    except ValueError as exc:
        return _json_error(str(exc), reason='invalid_assistant_request')
    except OSError as exc:
        return _json_error(f'助手用量紀錄無法保存：{type(exc).__name__}', status=503,
                           reason='assistant_usage_persist_failed')
    return _no_store_response(JsonResponse(payload))


ASSISTANT_ICON_MESSAGES = {
    'icon_empty': '請選擇一張圖片。',
    'icon_too_large': '圖片太大，請使用 10 MB、4000 萬像素以內的圖片。',
    'icon_unreadable': '無法讀取這張圖片，請改用 JPG 或 PNG。',
}


def _assistant_icon_path():
    return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).with_name('assistant_icon.png')


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='assistant')  # Only changes the chat button picture.
def assistant_icon_api(request):
    """Upload a launcher picture (auto-cropped and resized) or reset to text."""
    if request.POST.get('reset') == 'true':
        _assistant_icon_path().unlink(missing_ok=True)
        return JsonResponse({'ok': True, 'reason': None, 'has_icon': False})
    upload = request.FILES.get('icon')
    try:
        saved = assistant.save_launcher_icon(upload.read() if upload else b'', _assistant_icon_path())
    except OSError as exc:
        return _json_error(f'無法保存按鈕圖片：{type(exc).__name__}', status=503,
                           reason='icon_persist_failed')
    if not saved['ok']:
        return _json_error(ASSISTANT_ICON_MESSAGES[saved['reason']], reason=saved['reason'])
    return JsonResponse({'ok': True, 'reason': None, 'has_icon': True})


@require_GET
def assistant_icon_image(request):
    path = _assistant_icon_path()
    if not path.is_file():
        raise Http404('尚未設定助手按鈕圖片')
    return _no_store_response(FileResponse(path.open('rb'), content_type='image/png'))


@require_GET
def detection_stats_api(request):
    """Read-only grade and defect statistics over the detection dataset."""
    params = request.GET
    try:
        payload = detection_stats.query(
            _dataset_root('detection'),
            start_date=params.get('start_date'), end_date=params.get('end_date'),
            bucket=params.get('bucket', 'none'), group_by=params.get('group_by', 'grade'),
            # ``grade=加工&grade=下等``; absent means every grade.
            grades=params.getlist('grade') or None,
            compare_grades=params.get('compare_grades', 'false') == 'true',
        )
    except ValueError as exc:
        return _json_error(str(exc), reason='invalid_stats_query')
    return _no_store_response(JsonResponse(payload))


def _notification_payload():
    mode = APP_STATE.get('hardware_mode')
    result_id = (APP_STATE.get('detection_result') or {}).get('result_id')
    try:
        return notification_outbox().status(mode, result_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'reason': 'outbox_unreadable', 'mode': mode, 'enabled': False,
                'error': f'通知待送檔無法讀取：{exc}'}


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='notification')  # A notification preference never moves hardware.
def notification_api(request):
    """Switch LINE notifications for the current capture mode on or off."""
    enabled = _request_data(request).get('enabled')
    if type(enabled) is not bool:
        return _json_error('通知開關必須是 true 或 false。', reason='invalid_notification_switch')
    with STATE_LOCK:
        mode = APP_STATE.get('hardware_mode')
    try:
        notification_outbox().set_enabled(mode, enabled)
    except (OSError, ValueError) as exc:
        return _json_error(f'無法保存通知開關：{exc}', status=503,
                           reason='notification_switch_persist_failed')
    with STATE_LOCK:
        return JsonResponse(_state_payload())


def _saved_detection_result(fruit_id):
    """Return the durable result only when its JSON and summary were saved."""
    result = detection.load_result(_detection_output_root(), fruit_id)
    if not result or (result.get('storage') or {}).get('status') != 'saved':
        return None
    return result


def _clear_saved_photo_input(fruit_dir, fruit_id):
    """Drop a photo-only round's temp originals once results hold a copy.

    Returns a message suffix; a busy folder falls back to deferred cleanup.
    """
    try:
        cleared = dataset_store.discard_temp_fruit(
            _dataset_root(), fruit_dir, fruit_id, now_string=_now_string, fruit_id_exists=_fruit_id_exists,
        )
    except (DatasetFileBusyError, OSError) as exc:
        return f' 暫存原圖暫時無法清除，結果已保存：{exc}'
    if cleared['mode'] == 'deleted':
        return ''
    return ' 暫存原圖被占用，已改為背景清理；結果已保存。'


def _start_detection_job(fruit_id, fruit_dir):
    operation_token = APP_STATE.begin_dataset_operation(
        'detection', fruit_id
    )
    if operation_token is None:
        raise CaptureCommandError(
            '已有資料操作進行中。',
            reason='dataset_operation_in_progress',
        )
    APP_STATE['detection_status'] = 'pending'
    APP_STATE['status'] = 'detection_pending'
    APP_STATE['message'] = f'{fruit_id} 三張原圖已保存，等待批次檢測。'
    threading.Thread(
        target=_run_detection_job,
        args=(operation_token, fruit_id, Path(fruit_dir),
              _detection_session(fruit_dir)),
        name=f'detection-{fruit_id}',
        daemon=True,
    ).start()


@csrf_exempt
@require_POST
@network_required
def upload_images_api(request):
    _ensure_dataset_structure()
    image_file = request.FILES.get('image')
    image_files = request.FILES.getlist('images')
    if image_file is None and len(image_files) == 1:
        image_file = image_files[0]
    if image_file is None:
        return _json_error('每次只能上傳當站的單張照片，欄位名稱請使用 image。', status=400, reason='missing_image')

    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        if not _photo_only_session() and _network_hardware_disabled_reason():
            return local_network.network_error(_network_hardware_disabled_reason())
        options_error = _dataset_options_error_response()
        if options_error is not None:
            return options_error
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
        upload_boot_id = APP_STATE.get('esp32_boot_id')
        photo_only = _photo_only_session()

    try:
        _save_station_image(fruit_dir, station_index, image_file)
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            boot_changed = not photo_only and APP_STATE.get('esp32_boot_id') != upload_boot_id
            if boot_changed:
                _set_capture_restart_recovery(active_fruit_id, photo_saved=False)
            upload_state_changed = (
                APP_STATE['active_fruit_id'] != active_fruit_id
                or APP_STATE['capture_token'] != request_capture_token
                or APP_STATE.get('status') != 'uploading'
                or boot_changed
            )
            if not upload_state_changed:
                APP_STATE['pending_capture'] = True
                APP_STATE['status'] = 'waiting_camera'
                _start_wait_timer()
                APP_STATE['message'] = f'{active_fruit_id} 第 {station_index} 站上傳失敗，手機端可重試。{exc}'
        if upload_state_changed:
            return _json_error(
                '照片寫入失敗，且上傳期間硬體狀態已變更；不會重新開放上傳。',
                status=409,
                reason='capture_state_changed_during_upload',
            )
        return _json_error(f'照片上傳失敗：{exc}', status=409, reason='upload_failed')

    with STATE_LOCK:
        if active_fruit_id != APP_STATE['active_fruit_id'] or request_capture_token != APP_STATE['capture_token']:
            return _json_error('上傳完成時拍攝命令已過期，請重新拍攝。', status=409)
        if APP_STATE.get('status') != 'uploading' or (not photo_only and APP_STATE.get('esp32_boot_id') != upload_boot_id):
            if not photo_only and APP_STATE.get('esp32_boot_id') != upload_boot_id:
                _set_capture_restart_recovery(active_fruit_id, photo_saved=True)
            return _json_error(
                '照片已保存，但上傳期間硬體狀態已變更；不會放行或確認 Gate 3，請依提示復原。',
                status=409,
                reason='capture_state_changed_during_upload',
            )
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
        if photo_only:
            detection_round = APP_STATE.get('session_work_mode') == 'detection'
            if station_index < IMAGE_COUNT:
                next_capture = _schedule_photo_capture if detection_round else _request_photo_capture
                next_capture(station_index + 1)
            elif detection_round:
                APP_STATE['active_station_index'] = None
                _clear_wait_timer()
                _start_detection_job(active_fruit_id, fruit_dir)
            else:
                APP_STATE['active_station_index'] = None
                APP_STATE['status'] = 'uploaded'
                APP_STATE['message'] = f'{active_fruit_id} 三張照片已完成，請檢查後人工分類保存。'
                _clear_wait_timer()
            return JsonResponse(_state_payload(extra={'uploaded_fruit_id': active_fruit_id, 'station_index': station_index}))
        if station_index == IMAGE_COUNT:
            APP_STATE['motor_command'] = None
            APP_STATE['active_station_index'] = None
            APP_STATE['gate3_waiting_boot_id'] = APP_STATE.get('esp32_boot_id')
            if APP_STATE.get('session_work_mode') == 'detection':
                _clear_wait_timer()
                _record_transition(
                    'gate3_waiting_detection',
                    fruit_id=active_fruit_id,
                    station_index=station_index,
                )
                _start_detection_job(active_fruit_id, fruit_dir)
                return JsonResponse(_state_payload(extra={
                    'uploaded_fruit_id': active_fruit_id,
                    'station_index': station_index,
                }))
            APP_STATE['status'] = 'uploaded'
            APP_STATE['message'] = (
                f'{active_fruit_id} 三站照片已完成；Gate 3 保持關閉，請確認後分類。'
            )
            _clear_wait_timer()
            _record_transition(
                'gate3_waiting_classification',
                fruit_id=active_fruit_id,
                station_index=station_index,
            )
            payload = _state_payload(extra={
                'uploaded_fruit_id': active_fruit_id,
                'station_index': station_index,
            })
            return JsonResponse(payload)

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
@network_required
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
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        if not _photo_only_session() and _network_hardware_disabled_reason():
            return local_network.network_error(_network_hardware_disabled_reason())
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        fruit_id = APP_STATE['active_fruit_id']
        if fruit_id and _photo_only_session():
            data = _request_data(request)
            if data.get('fruit_id') != fruit_id or _safe_int(data.get('capture_token')) != APP_STATE['capture_token']:
                return _json_error('本輪資料已變更，請重新確認照片。', status=409, reason='stale_capture')
        if not fruit_id:
            return _json_error('目前沒有可分類的暫存資料。', status=409)

        src_dir = _temp_dir() / fruit_id
        if not src_dir.exists():
            return _json_error('暫存資料夾不存在，請重新拍攝。', status=404)
        if APP_STATE['status'] != 'uploaded' or dataset_store.temp_image_count(src_dir) != IMAGE_COUNT:
            return _json_error(f'{fruit_id} 尚未完成 {IMAGE_COUNT} 張照片上傳。', status=409)
        photo_only = _photo_only_session()
        if not photo_only:
            reason = _sorter_start_disabled_reason()
            if reason:
                return _json_error(
                    f'無法安全分類（{describe_reason(reason)}）；已保留暫存照片與 Gate 3 上的果實。',
                    status=409, reason=reason,
                )

        dest_dir = _dataset_root() / label / fruit_id
        if dest_dir.exists():
            if dataset_store.is_ignored_reset_path(_dataset_root(), dest_dir):
                moved_warning = dataset_store.move_reset_leftover_to_pending(_dataset_root(), dest_dir, reason='classify')
                if not dest_dir.exists():
                    dataset_store.remove_ignored_reset_path(_dataset_root(), dest_dir)
                else:
                    return _json_error(moved_warning, status=409)
            else:
                return _json_error(f'{label}/{fruit_id} 已存在，請調整 counter 或先整理資料夾。', status=409)
        if dest_dir.exists():
            return _json_error(f'{label}/{fruit_id} 已存在，請調整 counter 或先整理資料夾。', status=409)
        relative_path = f'{label}/{fruit_id}'
        capture_time = APP_STATE['capture_time'] or _now_string()
        classification_code = CLASSIFICATION_CODES[label]
        if not photo_only:
            try:
                dataset_store.write_sorter_recovery_intent(
                    _sorter_recovery_path(),
                    fruit_id, label, classification_code,
                )
            except OSError as exc:
                return _json_error(
                    f'無法建立分類器安全復原紀錄：{exc}',
                    status=503,
                    reason='sorter_recovery_persist_failed',
                )
            APP_STATE['sorter_recovery_required'] = True
            APP_STATE['sorter_fruit_id'] = fruit_id
            APP_STATE['sorter_label'] = label
            APP_STATE['sorter_classification_code'] = classification_code
        operation_token = APP_STATE.begin_dataset_operation('classify', fruit_id)
        APP_STATE['message'] = f'{fruit_id} 正在分類為「{label}」。'

    try:
        dataset_store.safe_move(src_dir, dest_dir)
        dataset_store.append_metadata(_dataset_root(), fruit_id, label, capture_time, relative_path, note, dest_dir)
        fruit_number = dataset_store.parse_fruit_number(fruit_id)
        if fruit_number is not None:
            dataset_store.write_counter(_dataset_root(), max(dataset_store.load_counter(_dataset_root()), fruit_number + 1))
        if photo_only:
            try:
                (dest_dir / '.capture-session.json').unlink(missing_ok=True)
            except OSError:
                pass  # Metadata and photos are already committed; cleanup is best effort.
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            APP_STATE.finish_dataset_operation(operation_token)
            dataset_untouched = src_dir.exists() and not dest_dir.exists()
            if photo_only:
                APP_STATE['message'] = f'{fruit_id} 分類保存失敗：{exc}'
                APP_STATE['status'] = 'uploaded' if dataset_untouched else 'error'
                APP_STATE['last_error_reason'] = 'dataset_commit_failed'
            elif dataset_untouched:
                try:
                    _clear_sorter_recovery_marker()
                    _reset_sorter_state()
                except OSError:
                    _set_sorter_failed(
                        fruit_id,
                        label,
                        classification_code,
                        'sorter_recovery_clear_failed',
                        phase='dataset_failed',
                    )
                else:
                    APP_STATE['message'] = f'{fruit_id} 分類失敗，可稍後重試。'
            else:
                _set_sorter_failed(
                    fruit_id,
                    label,
                    classification_code,
                    'dataset_commit_uncertain',
                    phase='dataset_failed',
                )
        return _json_error(str(exc), status=409, reason='dataset_file_busy')

    with STATE_LOCK:
        if not APP_STATE.operation_matches(operation_token):
            _set_sorter_failed(
                fruit_id,
                label,
                classification_code,
                'dataset_commit_state_changed',
                phase='dataset_committed',
            )
            return _json_error('分類完成時狀態已變更，已保留資料供人工確認。', status=409, reason='stale_operation')
        if APP_STATE.get('active_fruit_id') != fruit_id:
            APP_STATE.finish_dataset_operation(operation_token)
            _set_sorter_failed(
                fruit_id,
                label,
                classification_code,
                'dataset_commit_state_changed',
                phase='dataset_committed',
            )
            return _json_error('分類完成時 fruit 已變更，已保留資料供人工確認。', status=409, reason='stale_operation')
        APP_STATE.finish_dataset_operation(operation_token)
        _clear_classified_dataset_state(fruit_id, label)
        _record_transition('fruit_classified', fruit_id=fruit_id, details={'label': label})
        if photo_only:
            APP_STATE['status'] = 'idle'
            sorter_result = {'sorter_command_queued': False, 'hardware_skipped': True}
        else:
            sorter_result = _queue_sorter_command(fruit_id, label)
        payload = {
            'status': 'success',
            'data_classified': True,
            'fruit_id': fruit_id,
            'label': label,
            'path': relative_path,
            'next_fruit_id': dataset_store.format_fruit_id(dataset_store.load_counter(_dataset_root())),
            **sorter_result,
        }
    return JsonResponse(payload)


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='discard')
def discard_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request, cleanup=True)
        if reason:
            return local_network.network_error(reason)
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再刪除。', status=409)
        active_fruit_id = APP_STATE['active_fruit_id']
        fruit_id = _discardable_fruit_id()
        if fruit_id and _photo_only_session():
            data = _request_data(request)
            stale_capture = (
                active_fruit_id
                and _safe_int(data.get('capture_token')) != APP_STATE['capture_token']
            )
            if data.get('fruit_id') != fruit_id or stale_capture:
                return _json_error('本輪資料已變更，請重新確認照片。', status=409, reason='stale_capture')
        if not fruit_id:
            return _json_error('目前沒有可刪除的暫存資料。', status=409)

        gate3_waiting = _gate3_manual_removal_required()
        if gate3_waiting:
            try:
                dataset_store.write_gate3_recovery_marker(_gate3_recovery_path(), fruit_id)
            except OSError as exc:
                return _json_error(
                    f'無法保存 Gate 3 人工復原鎖：{exc}',
                    status=503,
                    reason='gate3_recovery_persist_failed',
                )
        _disable_auto_run()
        APP_STATE['auto_run_finishing'] = False
        fruit_dir = _temp_dir() / fruit_id
        # Saved results stay; only an unfinished round's partial output goes.
        result_dir = (
            _detection_output_root() / fruit_id
            if APP_STATE['session_work_mode'] == 'detection'
            and _saved_detection_result(fruit_id) is None
            else None
        )
        operation_token = APP_STATE.begin_dataset_operation('discard', fruit_id)
        APP_STATE['message'] = f'{fruit_id} 正在刪除或隔離。'

    try:
        if result_dir is not None:
            detection.remove_summary_rows(_detection_summary_path(), fruit_id)
        result_discard = (
            dataset_store.discard_temp_fruit(
                _dataset_root(), result_dir, fruit_id, now_string=_now_string, fruit_id_exists=_fruit_id_exists,
            ) if result_dir is not None else None
        )
        discard_result = dataset_store.discard_temp_fruit(
            _dataset_root(), fruit_dir, fruit_id, now_string=_now_string, fruit_id_exists=_fruit_id_exists,
        )
        if result_discard and result_discard['mode'] != 'deleted':
            discard_result = result_discard
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
        APP_STATE['detection_status'] = 'idle'
        APP_STATE['detection_result'] = None
        APP_STATE['detection_error'] = None
        if gate3_waiting:
            APP_STATE['status'] = 'error'
            APP_STATE['auto_run_recovery_reason'] = 'gate3_manual_removal_required'
            APP_STATE['last_error_reason'] = 'gate3_manual_removal_required'
            APP_STATE['last_error_command'] = 'classify_fruit'
            APP_STATE['last_error_command_id'] = None
            APP_STATE['message'] = (
                f'{fruit_id} 暫存資料已處理，但果實仍可能位於 Gate 3。'
                '系統不會自動開啟 Gate 3；請依斷電程序切斷伺服電源，'
                '確認安全後人工移除。'
            )
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
            'next_fruit_id': discard_result.get('next_fruit_id') or dataset_store.format_fruit_id(dataset_store.load_counter(_dataset_root())),
        })
    return JsonResponse(payload)


@csrf_exempt
@require_POST
@network_required(cleanup=True, operation='reset')
def reset_dataset_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        reason = local_network.NETWORK.request_reason(request, cleanup=True)
        if reason:
            return local_network.network_error(reason)
        _sync_active_state_with_filesystem()
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409, reason='dataset_busy')
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重置 dataset。', status=409)
        if _gate3_manual_removal_required():
            _disable_auto_run()
            return _json_error(
                '果實仍在 Gate 3；不可重置 dataset，請先依斷電程序安全移除。',
                status=409,
                reason='gate3_manual_removal_required',
            )
        if APP_STATE.get('sorter_recovery_required'):
            return _json_error(
                '分類器結果仍需人工安全復原，不可重置 dataset。',
                status=409,
                reason='django_restarted_during_sorter',
            )
        if _sorter_busy():
            return _json_error(
                '硬體分類器正在執行，完成或逾時前不可重置 dataset。',
                status=409,
                reason='classifier_busy',
            )
        operation_token = APP_STATE.begin_dataset_operation('reset', APP_STATE.get('active_fruit_id'))
        APP_STATE['message'] = '正在重置 dataset。'

    try:
        _ensure_dataset_structure()
        delete_warnings = dataset_store.reset_dataset_contents(
            _dataset_root(), collects_by_label=_collects_by_label(), labels=LABELS,
            now_string=_now_string, reset_summary=detection.reset_summary,
            clear_image_cache=_load_image_list.cache_clear,
        )
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
        APP_STATE['detection_status'] = 'idle'
        APP_STATE['detection_result'] = None
        APP_STATE['detection_error'] = None
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
    _ensure_dataset_structure()
    with STATE_LOCK:
        if (not FRUIT_ID_PATTERN.match(fruit_id) or filename not in IMAGE_FILENAMES
                or request.GET.get('work_mode') != APP_STATE['work_mode']):
            raise Http404('image not found')
        image_path = _find_fruit_image(fruit_id, filename)
        if not image_path:
            raise Http404('image not found')
        stat = image_path.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if request.headers.get('If-None-Match') == etag:
            response = HttpResponseNotModified()
        else:
            response = FileResponse(image_path.open('rb'), content_type='image/jpeg')
        response['ETag'] = etag
        response['Last-Modified'] = http_date(stat.st_mtime)
        response['Cache-Control'] = 'private, no-cache'
        return response


@require_GET
def detection_image_api(request, fruit_id, filename):
    _ensure_dataset_structure()
    with STATE_LOCK:
        if (APP_STATE['work_mode'] != 'detection'
                or request.GET.get('work_mode') != 'detection'
                or not FRUIT_ID_PATTERN.match(fruit_id)
                or Path(filename).name != filename
                or not re.fullmatch(r'[A-Za-z0-9_.-]+', filename)):
            raise Http404('image not found')
        image_path = _detection_output_root() / fruit_id / filename
        if (not image_path.is_file()
                or dataset_store.is_ignored_reset_path(_dataset_root(), image_path.parent)
                or dataset_store.is_deferred_discard_path(_dataset_root(), image_path.parent)
                or image_path.suffix.lower() not in ('.jpg', '.jpeg', '.png')):
            raise Http404('image not found')
        stat = image_path.stat()
        response = FileResponse(image_path.open('rb'), content_type='image/jpeg')
        response['ETag'] = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        response['Last-Modified'] = http_date(stat.st_mtime)
        response['Cache-Control'] = 'private, no-cache'
        return response


@require_GET
def open_dataset_folder_api(request):
    reason = local_network.NETWORK.request_reason(request, local_only=True)
    if reason:  # Opens a window on this PC, so only the local Dashboard may ask.
        return local_network.network_error(reason)
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        dataset_root = _dataset_root()
        if os.name == 'nt' and hasattr(os, 'startfile'):
            os.startfile(dataset_root)
            return JsonResponse({'status': 'success', 'path': str(dataset_root), 'opened': True})
        return JsonResponse({'status': 'success', 'path': str(dataset_root), 'opened': False})



@require_GET
def export_detection_api(request):
    """Download the detection partition as a ZIP (statistics image + summary)."""
    reason = local_network.NETWORK.request_reason(request, cleanup=True)
    if reason:
        return local_network.network_error(reason)
    include_results = request.GET.get('include_results')
    if include_results not in ('0', '1'):
        return _json_error('include_results 必須是 0 或 1。', reason='export_options_invalid')
    _ensure_dataset_structure()
    with STATE_LOCK:
        mode_error = _request_dataset_mode_error(request)
        if mode_error is not None:
            return mode_error
        if APP_STATE['work_mode'] != 'detection':
            return _json_error('只有自動檢測模式可匯出檢測報告。', status=409,
                               reason='export_detection_only')
        if APP_STATE.get('dataset_operation'):
            return _json_error('已有 dataset 檔案操作進行中，請稍後再試。', status=409,
                               reason='dataset_busy')
        detection_root = _dataset_root()
    stream = tempfile.TemporaryFile()  # Deleted when FileResponse closes it.
    try:
        exported = detection_report.write_export(
            detection_root, stream, include_results=include_results == '1')
    except (OSError, ValueError) as exc:
        stream.close()
        logger.warning('Detection export failed: %s', exc)
        return _json_error(f'匯出失敗：{exc}', status=500, reason='export_failed')
    stream.seek(0)
    return FileResponse(stream, as_attachment=True, filename=exported['filename'],
                        content_type='application/zip')

@csrf_exempt
@require_POST
@network_required
def webrtc_offer_api(request):
    data = _request_data(request)
    offer = data.get('offer') or data.get('description') or data
    if not _is_session_description(offer, 'offer'):
        return _json_error('WebRTC offer 格式不正確。', status=400)

    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        offer_id = webrtc_signaling.accept_offer(WEBRTC_STATE, offer, _now_string)
    return JsonResponse({'status': 'success', 'offer_id': offer_id})


@csrf_exempt
@require_POST
@network_required
def webrtc_answer_api(request):
    data = _request_data(request)
    answer = data.get('answer') or data.get('description') or data
    if not _is_session_description(answer, 'answer'):
        return _json_error('WebRTC answer 格式不正確。', status=400)

    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        answer_id = webrtc_signaling.accept_answer(WEBRTC_STATE, answer, _now_string)
    return JsonResponse({'status': 'success', 'answer_id': answer_id})


@csrf_exempt
@require_POST
@network_required
def webrtc_ice_api(request):
    data = _request_data(request)
    role = data.get('role')
    candidate = data.get('candidate')
    if role not in ('dashboard', 'camera'):
        return _json_error('role 必須是 dashboard 或 camera。', status=400)
    if not candidate:
        return JsonResponse({'status': 'success', 'ignored': True})

    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
        webrtc_signaling.add_ice_candidate(
            WEBRTC_STATE,
            role=role,
            candidate=candidate,
            now_string=_now_string,
        )
    return JsonResponse({'status': 'success'})


@require_GET
@network_required
def webrtc_state_api(request):
    with STATE_LOCK:
        reason = local_network.NETWORK.request_reason(request)
        if reason:
            return local_network.network_error(reason)
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
        for key in ('network_recovery', 'network_identity', 'network_persist_failed',
                    'esp32_network_capable', 'esp32_interlocked', 'esp32_motion',
                    'esp32_setup_allowed', 'esp32_setup_active', 'esp32_setup_failure', 'esp32_setup_block_reason',
                    'esp32_setup_request_deadline',
                    'recovery_generation',
                    'network_recovery_receipt'):
            APP_STATE.pop(key, None)
        APP_STATE.update({
            'work_mode': 'collection',
            'dataset_options_error': None,
            'session_work_mode': 'collection',
            'hardware_mode': 'hardware',
            'session_hardware_mode': 'hardware',
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
            'auto_run_enabled': False,
            'auto_feed_pending': False,
            'auto_run_finishing': False,
            'auto_run_recovery_reason': None,
            'camera_last_live_frame_at': None,
            'camera_last_live_frame_monotonic': None,
            'camera_diagnostics': None,
            'esp32_boot_id': None,
            'esp32_feeder_capable': False,
            'esp32_sorter_capable': False,
            'esp32_feeder_state': None,
            'esp32_feeder_sensor_state': None,
            'esp32_last_feed_command_id': 0,
            'gate3_waiting_boot_id': None,
            'last_completed_feed_command_id': 0,
            'feeder_test_result': None,
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
            'sorter_recovery_required': False,
            'photo_next_capture': None,
            'detection_status': 'idle',
            'detection_result': None,
            'detection_progress': None,
            'detection_error': None,
            'status': 'idle',
            'message': '等待手機相機與 ESP32 就緒。',
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


def _dataset_root(work_mode=None):
    mode = work_mode or APP_STATE['work_mode']
    if mode not in ('collection', 'detection'):
        raise ValueError(f'Invalid dataset work mode: {mode}')
    return Path(settings.DATASET_ROOT) / mode


def _detection_output_root():
    return _dataset_root('detection') / dataset_store.DETECTION_OUTPUT_DIRNAME


def _detection_summary_path():
    return _dataset_root('detection') / dataset_store.DETECTION_SUMMARY_FILENAME


def _dataset_options_path():
    return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).with_name('dataset_options.json')


def _temp_dir():
    return _dataset_root() / dataset_store.TEMP_DIRNAME


def _sorter_recovery_path():
    return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).with_name('sorter_recovery.json')


def _gate3_recovery_path():
    return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).with_name('gate3_recovery.json')


@cache
def _restore_dataset_options(path):
    try:
        options = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(options, dict)
                or options.get('work_mode') not in ('collection', 'detection')
                or options.get('hardware_mode') not in ('hardware', 'photo_only')):
            raise ValueError('Invalid persisted dataset options')
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        message = f'無法確認資料模式，請修復 {path}：{exc}'
        APP_STATE['dataset_options_error'] = message
        APP_STATE['status'] = 'error'
        if not APP_STATE.get('auto_run_recovery_reason'):
            APP_STATE['auto_run_recovery_reason'] = 'dataset_options_invalid'
        APP_STATE['message'] = message
        return
    APP_STATE['work_mode'] = APP_STATE['session_work_mode'] = options['work_mode']
    APP_STATE['hardware_mode'] = APP_STATE['session_hardware_mode'] = options['hardware_mode']


def _dataset_options_error_response():
    if not APP_STATE.get('dataset_options_error'):
        return None
    return _json_error(APP_STATE['dataset_options_error'], status=409, reason='dataset_options_invalid')


def _request_dataset_mode_error(request):
    options_error = _dataset_options_error_response()
    if options_error is not None:
        return options_error
    mode = (request.GET if request.method == 'GET' else _request_data(request)).get('dataset_mode')
    if mode != APP_STATE['work_mode']:
        return _json_error('資料模式已變更，請重新確認目前資料區。', status=409, reason='stale_dataset_mode')
    return None


def _no_store_response(response):
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def _capture_timing_path():
    return Path(settings.HARDWARE_PARAMS_PATH)


def _collects_by_label():
    """Only collection files photos into label folders with metadata.csv.

    Detection keeps every round, including manual takeover, in ``results/``
    and ``detection_summary.csv``.
    """
    return APP_STATE['work_mode'] == 'collection'


def _ensure_dataset_structure():
    with STATE_LOCK:
        _restore_dataset_options(_dataset_options_path())
        _initialise_dataset_structure(_dataset_root())


@cache
def _initialise_dataset_structure(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / dataset_store.TEMP_DIRNAME).mkdir(parents=True, exist_ok=True)
    if _collects_by_label():
        for label in LABELS:
            (root / label).mkdir(parents=True, exist_ok=True)
    else:
        dataset_store.remove_empty_label_scaffold(root, labels=LABELS)
    if not (root / dataset_store.COUNTER_FILENAME).exists():
        dataset_store.write_counter(_dataset_root(), 1)
    _ensure_capture_timing_config()
    if _collects_by_label():
        dataset_store.ensure_metadata_header(_dataset_root())
    _restore_gate3_recovery_marker()
    _restore_sorter_recovery_marker()
    NETWORK_RECOVERY.restore()


def _write_sorter_recovery_marker(*, phase, reason=None):
    dataset_store.write_recovery_payload(_sorter_recovery_path(), {
        'command_id': APP_STATE.get('sorter_command_id'),
        'fruit_id': APP_STATE.get('sorter_fruit_id'),
        'label': APP_STATE.get('sorter_label'),
        'classification_code': APP_STATE.get('sorter_classification_code'),
        'phase': phase,
        'reason': reason,
    })


def _restore_sorter_recovery_marker():
    path = _sorter_recovery_path()
    if not path.exists() or APP_STATE.get('sorter_recovery_required'):
        return
    payload, command_id, classification_code = dataset_store.read_sorter_recovery_marker(
        path, labels=LABELS, classification_codes=CLASSIFICATION_CODES.values(),
    )

    _disable_auto_run()
    APP_STATE['sorter_recovery_required'] = True
    APP_STATE['sorter_status'] = 'failed'
    APP_STATE['sorter_command_id'] = command_id
    APP_STATE['sorter_fruit_id'] = payload.get('fruit_id')
    APP_STATE['sorter_label'] = payload.get('label')
    APP_STATE['sorter_classification_code'] = classification_code
    APP_STATE['sorter_error'] = 'django_restarted_during_sorter'
    APP_STATE['auto_run_recovery_reason'] = 'django_restarted_during_sorter'
    APP_STATE['last_error_reason'] = 'django_restarted_during_sorter'
    APP_STATE['last_error_command'] = 'classify_fruit'
    APP_STATE['last_error_command_id'] = command_id
    APP_STATE['status'] = 'error'
    APP_STATE['message'] = (
        'Django 在分類器 terminal report 確認前重新啟動；不會重送實體命令，'
        '請檢查 Gate 3 與分類器後明確確認復原。'
    )
    if APP_STATE.get('work_mode') == 'detection' and payload.get('fruit_id'):
        result = detection.load_result(_detection_output_root(), payload['fruit_id'])
        APP_STATE['detection_result'] = result
        APP_STATE['detection_status'] = 'completed' if result and result.get('complete') else 'failed'


def _clear_sorter_recovery_marker():
    dataset_store.clear_recovery_marker(_sorter_recovery_path())
    APP_STATE['sorter_recovery_required'] = False


def _restore_gate3_recovery_marker():
    if not _gate3_recovery_path().exists():
        return
    _disable_auto_run()
    APP_STATE['auto_run_recovery_reason'] = 'gate3_manual_removal_required'
    APP_STATE['last_error_reason'] = 'gate3_manual_removal_required'
    APP_STATE['last_error_command'] = 'classify_fruit'
    APP_STATE['last_error_command_id'] = None
    APP_STATE['status'] = 'error'
    APP_STATE['message'] = (
        'Django 在 Gate 3 人工安全復原確認前重新啟動；不會重送實體命令，'
        '請依斷電程序確認安全並移除果實後再明確確認復原。'
    )


def _clear_gate3_recovery_marker():
    dataset_store.clear_recovery_marker(
        _gate3_recovery_path()
    )


def _request_data(request):
    content_type = request.META.get('CONTENT_TYPE', '')
    if 'application/json' in content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


def _normalise_capture_timing(raw_timing, *, require_all):
    raw_timing = raw_timing if isinstance(raw_timing, dict) else {}
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
    return timing


def _ensure_capture_timing_config():
    path = _capture_timing_path()
    path_key = str(path)
    if APP_STATE.get('capture_timing_loaded_path') == path_key:
        return
    try:
        raw, revision = capture_timing.load(path, CAPTURE_TIMING_FIELDS)
        timing = _normalise_capture_timing(raw, require_all=True)
    except (capture_timing.TimingValidationError, CaptureCommandError) as exc:
        raise ImproperlyConfigured(
            f'硬體參數檔 {path} 無效（{exc.reason}）：{exc.message} 修正後重新啟動 Django。'
        ) from None
    APP_STATE['capture_timing'] = timing
    APP_STATE['capture_timing_revision'] = revision
    APP_STATE['capture_timing_applied_revision'] = 0
    APP_STATE['capture_timing_applied_at'] = None
    APP_STATE['capture_timing_loaded_path'] = path_key


def _capture_timing_status():
    if APP_STATE['capture_timing_applied_revision'] == APP_STATE['capture_timing_revision']:
        return 'applied'
    if not _esp32_is_online():
        return 'waiting_esp32'
    return 'pending_esp32_apply'


def _capture_timing_state_payload():
    return {
        'capture_timing': dict(APP_STATE['capture_timing']),
        'capture_timing_revision': APP_STATE['capture_timing_revision'],
        'capture_timing_applied_revision': APP_STATE['capture_timing_applied_revision'],
        'capture_timing_applied_at': APP_STATE['capture_timing_applied_at'],
        'capture_timing_status': _capture_timing_status(),
    }


def _fruit_id_exists(fruit_id):
    if APP_STATE['work_mode'] == 'detection' and (
        (_detection_output_root() / fruit_id).exists()
        and not dataset_store.is_ignored_reset_path(_dataset_root(), _detection_output_root() / fruit_id)
    ):
        return True
    temp_fruit_dir = _temp_dir() / fruit_id
    if temp_fruit_dir.exists() and dataset_store.is_deferred_discard_path(_dataset_root(), temp_fruit_dir):
        return True
    if (
        temp_fruit_dir.exists()
        and not dataset_store.is_ignored_reset_path(_dataset_root(), temp_fruit_dir)
        and not dataset_store.is_reusable_temp_dir(temp_fruit_dir)
    ):
        return True
    return any(
        (_dataset_root() / label / fruit_id).exists()
        and not dataset_store.is_ignored_reset_path(_dataset_root(), _dataset_root() / label / fruit_id)
        for label in LABELS
    )


def _find_fruit_image(fruit_id, filename):
    candidates = [_temp_dir() / fruit_id / filename]
    candidates.extend(_dataset_root() / label / fruit_id / filename for label in LABELS)
    for candidate in candidates:
        if (candidate.is_file() and not dataset_store.is_ignored_reset_path(_dataset_root(), candidate.parent)
                and not dataset_store.is_deferred_discard_path(_dataset_root(), candidate.parent)):
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
                'url': f'/api/image/{fruit_id}/{filename}/?work_mode={APP_STATE["work_mode"]}&v={image_path.stat().st_mtime_ns}',
            })
    return tuple(images)


def _reset_dataset_caches():
    _initialise_dataset_structure.cache_clear()
    _restore_dataset_options.cache_clear()
    dataset_store.load_counter.cache_clear()
    _load_image_list.cache_clear()


def _create_capture_session(source):
    if APP_STATE.get('dataset_options_error'):
        raise CaptureCommandError(APP_STATE['dataset_options_error'], reason='dataset_options_invalid')
    if not _camera_is_ready():
        raise CaptureCommandError('相機尚未就緒。', reason='camera_not_ready')
    if APP_STATE.get('hardware_mode') == 'photo_only':
        raise CaptureCommandError('純拍攝不接受硬體觸發。', reason='hardware_mode_required')
    _sync_active_state_with_filesystem()
    if APP_STATE.get('sorter_recovery_required'):
        raise CaptureCommandError(
            '分類器結果尚未完成人工安全復原，不可開始下一次拍攝。',
            reason='django_restarted_during_sorter',
        )
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

    fruit_number = dataset_store.next_available_number(dataset_store.load_counter(_dataset_root()), _fruit_id_exists)
    fruit_id = dataset_store.format_fruit_id(fruit_number)
    fruit_dir = _prepare_fruit_dir(fruit_id, source)
    if APP_STATE['work_mode'] == 'detection':
        try:
            dataset_store.save_capture_session(
                fruit_dir,
                _now_string(),
                APP_STATE['work_mode'],
                APP_STATE['hardware_mode'],
            )
        except OSError as exc:
            try:
                dataset_store.safe_rmtree(fruit_dir)
            except DatasetFileBusyError:
                pass
            raise CaptureCommandError(
                f'無法保存本輪選項：{exc}',
                status=503,
                reason='capture_options_persist_failed',
            ) from exc

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
        fruit_number = dataset_store.next_available_number(dataset_store.load_counter(_dataset_root()), _fruit_id_exists)
        fruit_id = dataset_store.format_fruit_id(fruit_number)
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
    if APP_STATE['work_mode'] == 'detection':
        result_dir = _detection_output_root() / fruit_id
        if result_dir.exists() and dataset_store.is_ignored_reset_path(_dataset_root(), result_dir):
            warning = dataset_store.move_reset_leftover_to_pending(_dataset_root(), result_dir, reason=source)
            if result_dir.exists():
                raise CaptureCommandError(warning, reason='reset_leftover_busy')
            dataset_store.remove_ignored_reset_path(_dataset_root(), result_dir)
    fruit_dir = _temp_dir() / fruit_id
    if fruit_dir.exists() and dataset_store.is_ignored_reset_path(_dataset_root(), fruit_dir):
        moved_warning = dataset_store.move_reset_leftover_to_pending(_dataset_root(), fruit_dir, reason=source)
        if fruit_dir.exists():
            raise CaptureCommandError(
                moved_warning,
                reason='reset_leftover_busy',
            )
    if fruit_dir.exists() and not dataset_store.is_reusable_temp_dir(fruit_dir):
        raise CaptureCommandError(
            f'{fruit_id} temp 資料夾已有內容，請先分類、刪除或 reset dataset。',
            reason='temp_folder_not_reusable',
        )
    fruit_dir.mkdir(parents=True, exist_ok=True)
    try:
        dataset_store.clear_temp_images(fruit_dir, clear_image_cache=_load_image_list.cache_clear)
    except DatasetFileBusyError as exc:
        raise CaptureCommandError(str(exc), reason='file_busy') from exc
    return fruit_dir


def _start_new_capture_session(fruit_id, source, status, message):
    APP_STATE['pending_capture'] = False
    APP_STATE['capture_token'] += 1
    APP_STATE['active_fruit_id'] = fruit_id
    APP_STATE['capture_time'] = _now_string()
    APP_STATE['source'] = source
    APP_STATE['session_work_mode'] = APP_STATE['work_mode']
    APP_STATE['session_hardware_mode'] = APP_STATE['hardware_mode']
    if APP_STATE['session_work_mode'] == 'detection':
        APP_STATE['detection_status'] = 'idle'
        APP_STATE['detection_result'] = None
        APP_STATE['detection_progress'] = None
        APP_STATE['detection_error'] = None
    APP_STATE['active_station_index'] = None
    APP_STATE['station_statuses'] = {str(index): 'pending' for index in range(1, IMAGE_COUNT + 1)}
    APP_STATE['motor_command'] = None
    APP_STATE['gate3_waiting_boot_id'] = None
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
    APP_STATE['gate3_waiting_boot_id'] = None
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
    try:
        _write_sorter_recovery_marker(phase='pending')
    except OSError:
        if (APP_STATE.get('motor_command') or {}).get('command_id') == command['command_id']:
            APP_STATE['motor_command'] = None
        _set_sorter_failed(
            fruit_id,
            label,
            classification_code,
            'sorter_recovery_persist_failed',
        )
        return _sorter_queue_result(False)
    APP_STATE['sorter_recovery_required'] = False
    APP_STATE['message'] = f'{fruit_id} 資料已分類，等待 ESP32 執行硬體分類器。'
    _record_transition(
        'classification_sorter_queued',
        fruit_id=fruit_id,
        command_id=command['command_id'],
        details={'label': label, 'classification_code': classification_code},
    )
    return _sorter_queue_result(True)


def _set_sorter_failed(fruit_id, label, classification_code, reason, *, phase='queue_failed'):
    _disable_auto_run()
    APP_STATE['auto_run_finishing'] = False
    APP_STATE['auto_run_recovery_reason'] = reason
    APP_STATE['last_error_reason'] = reason
    APP_STATE['last_error_command'] = 'classify_fruit'
    APP_STATE['last_error_command_id'] = None
    APP_STATE['status'] = 'error'
    APP_STATE['sorter_status'] = 'failed'
    APP_STATE['sorter_command_id'] = None
    APP_STATE['sorter_fruit_id'] = fruit_id
    APP_STATE['sorter_label'] = label
    APP_STATE['sorter_classification_code'] = classification_code
    APP_STATE['sorter_error'] = reason
    APP_STATE['sorter_started_monotonic'] = None
    APP_STATE['sorter_deadline_monotonic'] = None
    APP_STATE['sorter_recovery_required'] = True
    APP_STATE['message'] = f'{fruit_id} 資料已分類，但硬體分類命令建立失敗：{describe_reason(reason)}'
    try:
        _write_sorter_recovery_marker(phase=phase, reason=reason)
    except OSError:
        pass
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
    APP_STATE['sorter_recovery_required'] = False


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
    try:
        _write_sorter_recovery_marker(phase='running')
    except OSError:
        APP_STATE['sorter_recovery_required'] = True
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
    if not expected_command_id:
        return _state_payload(extra={
            'ok': True,
            'event': event,
            'ignored': True,
            'reason': 'no_pending_sorter_command',
        })
    terminal = APP_STATE.get('sorter_status') in ('completed', 'failed', 'timeout')
    if terminal and command_id == expected_command_id:
        return _state_payload(extra={
            'ok': True,
            'event': event,
            'ignored': True,
            'duplicate': True,
            'reason': 'sorter_terminal_state',
        })
    if command_id != expected_command_id:
        raise CaptureCommandError(
            '分類器回報的 command_id 與目前 sorter 命令不符。',
            reason='command_id_mismatch',
        )
    if classification_code != expected_code:
        raise CaptureCommandError(
            '分類器回報的 classification_code 與目前命令不符。',
            reason='classification_code_mismatch',
        )
    if data.get('fruit_id') and data['fruit_id'] != APP_STATE.get('sorter_fruit_id'):
        raise CaptureCommandError('分類器回報的 fruit_id 與目前命令不符。',
                                  reason='fruit_id_mismatch')

    current_command = APP_STATE.get('motor_command') or {}
    if (
        current_command.get('command') != 'classify_fruit'
        or current_command.get('command_id') != command_id
        or current_command.get('fruit_id') != APP_STATE.get('sorter_fruit_id')
    ):
        raise CaptureCommandError(
            '目前 motor command slot 不是這筆分類命令。',
            reason='sorter_command_slot_mismatch',
        )

    fruit_id = APP_STATE.get('sorter_fruit_id')
    APP_STATE['motor_command'] = None
    APP_STATE['sorter_deadline_monotonic'] = None
    if event == 'classification_sorter_completed':
        try:
            _clear_sorter_recovery_marker()
        except OSError:
            _disable_auto_run()
            APP_STATE['sorter_recovery_required'] = True
            APP_STATE['auto_run_recovery_reason'] = 'sorter_recovery_clear_failed'
        APP_STATE['sorter_status'] = 'completed'
        APP_STATE['sorter_error'] = None
        APP_STATE['status'] = 'idle'
        APP_STATE['auto_feed_pending'] = APP_STATE.get('auto_run_enabled', False)
        APP_STATE['auto_run_finishing'] = False
        APP_STATE['message'] = f'{fruit_id} 的硬體分類器控制流程已完成。'
    else:
        reason = (data.get('message') or 'classifier_failed').strip()
        _disable_auto_run()
        APP_STATE['auto_run_finishing'] = False
        APP_STATE['auto_run_recovery_reason'] = reason
        APP_STATE['last_error_reason'] = reason
        APP_STATE['last_error_command'] = 'classify_fruit'
        APP_STATE['last_error_command_id'] = command_id
        APP_STATE['sorter_status'] = 'failed'
        APP_STATE['sorter_error'] = reason
        APP_STATE['status'] = 'error'
        APP_STATE['message'] = f'{fruit_id} 的硬體分類器失敗：{describe_reason(reason)}'
        APP_STATE['sorter_recovery_required'] = True
        try:
            _write_sorter_recovery_marker(phase='failed', reason=reason)
        except OSError:
            pass
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
    _check_network_safety()
    reason = NETWORK_RECOVERY.disabled_reason('motor')
    if reason and command != 'recover_network':
        raise CaptureCommandError('網路安全鎖尚未解除。', reason=reason)
    if APP_STATE.get('hardware_mode') == 'photo_only':
        raise CaptureCommandError('純拍攝禁止馬達命令。', reason='hardware_mode_required')
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
    if (
        APP_STATE.get('session_work_mode') == 'detection'
        and APP_STATE.get('status') in (
            'detection_pending', 'detection_running',
            'detection_completed', 'detection_failed',
        )
    ):
        return _state_payload(extra={
            'ok': True,
            'event': 'capture_sequence_finished',
            'ignored': True,
            'duplicate': True,
            'reason': 'detection_already_started',
        })
    current_command = APP_STATE.get('motor_command')
    if current_command and command_id != current_command.get('command_id'):
        raise CaptureCommandError('ESP32 完成回報的 command_id 與目前馬達命令不符。', reason='command_id_mismatch')

    fruit_dir = _temp_dir() / fruit_id
    if dataset_store.temp_image_count(fruit_dir) != IMAGE_COUNT:
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
    if command and command['command'] in ('start_sequence', 'feed_one') and not _camera_is_ready():
        if command.get('feed_context') != 'calibration':
            command = None
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
    feed_context = command.get('feed_context')
    if feed_context == 'calibration':
        APP_STATE['status'] = 'idle'
        APP_STATE['message'] = (
            'HC-SR04 已偵測百香果，測試送料正常停止。'
            if result['ok']
            else f'測試送料已停止：{describe_reason(result["stop_reason"])}。'
        )
    elif result['ok']:
        APP_STATE['status'] = 'waiting_fruit'
        APP_STATE['message'] = 'HC-SR04 已在本機停止送料，等待同一筆觸發啟動第一站。'
    else:
        _disable_auto_run()
        APP_STATE['auto_run_finishing'] = False
        APP_STATE['auto_run_recovery_reason'] = event
        APP_STATE['status'] = 'waiting_fruit' if event == 'feeder_max_run_timeout' else 'error'
        APP_STATE['message'] = {
            'feeder_max_run_timeout': (
                '送料已達最大運轉時間；進料未確認。已停止自動送料，'
                '請檢查送料筒、出口與感測區域，不會自動補轉。'
            ),
            'feeder_sensor_not_clear': (
                '送料前感測區未清空；已停止自動送料，請移除感測區內果實後再開始。'
            ),
            'feeder_sensor_unavailable': (
                'HC-SR04 無有效回音；已停止自動送料，請檢查線材、供電、'
                'Echo 分壓與感測方向。'
            ),
        }.get(event, f'正式送料已停止：{describe_reason(result["stop_reason"])}。')
    if not result['ok']:
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
    if APP_STATE.get('hardware_mode') == 'photo_only':
        return False
    if APP_STATE.get('dataset_operation'):
        return False
    if APP_STATE.get('auto_run_recovery_reason') in (
        'feeder_sensor_not_clear',
        'feeder_sensor_unavailable',
    ):
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
        if dataset_store.is_deferred_discard_path(_dataset_root(), fruit_dir):
            continue
        if dataset_store.is_ignored_reset_path(_dataset_root(), fruit_dir):
            continue
        if _completed_photo_only_detection_input(fruit_dir):
            continue
        if not dataset_store.is_reusable_temp_dir(fruit_dir):
            return True
    return False


def _completed_photo_only_detection_input(fruit_dir):
    completed = (
        _detection_output_root()
        / Path(fruit_dir).name
        / 'result.json'
    ).is_file() or (Path(fruit_dir) / '.detection-terminal').is_file()
    if not completed:
        return False
    try:
        options = dataset_store.read_capture_options(Path(fruit_dir))
    except (OSError, ValueError, TypeError):
        return False
    return (
        options.get('work_mode') == 'detection'
        and options.get('hardware_mode') == 'photo_only'
    )


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
    reason = NETWORK_RECOVERY.disabled_reason('motor')
    if reason:
        return reason
    reason = _network_hardware_disabled_reason()
    if reason:
        return reason
    if APP_STATE.get('hardware_mode') == 'photo_only':
        return 'hardware_mode_required'
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
    if APP_STATE.get('sorter_recovery_required') and not allow_recovery:
        return 'sorter_recovery_required'
    reason = _feeder_hardware_disabled_reason(allow_recovery=allow_recovery)
    if reason:
        return reason
    if not APP_STATE.get('esp32_sorter_capable'):
        return 'sorter_capability_missing'
    recovery_reason = APP_STATE.get('auto_run_recovery_reason')
    if recovery_reason and not allow_recovery:
        return recovery_reason
    if _has_unclassified_temp_fruit():
        return 'temp_fruit_exists'
    if not _camera_is_ready():
        return 'camera_not_ready'
    return None


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


def _stop_for_esp32_restart(reason, command, command_id, message, *, fruit_id=None, details=None):
    """Shared stop after an ESP32 reboot; callers set their branch-only fields first."""
    _disable_auto_run()
    APP_STATE['auto_run_finishing'] = False
    APP_STATE['last_error_reason'] = reason
    APP_STATE['last_error_command'] = command
    APP_STATE['last_error_command_id'] = command_id
    APP_STATE['message'] = message
    _record_transition(reason, fruit_id=fruit_id, command_id=command_id, details=details)


def _start_wait_timer():
    APP_STATE['wait_started_at'] = _now_string()
    APP_STATE['wait_started_monotonic'] = time.monotonic()


def _clear_wait_timer():
    APP_STATE['wait_started_at'] = None
    APP_STATE['wait_started_monotonic'] = None


def _apply_session_timeouts():
    if APP_STATE.get('dataset_operation'):
        return
    _release_due_photo_capture()
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
    APP_STATE['message'] = f'{APP_STATE.get("sorter_fruit_id")} 的硬體分類器逾時：{describe_reason(reason)}'
    APP_STATE['sorter_recovery_required'] = True
    try:
        _write_sorter_recovery_marker(phase='timeout', reason=reason)
    except OSError:
        pass
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
    if APP_STATE.get('hardware_mode') == 'photo_only' and not (
        APP_STATE.get('last_error_reason') or APP_STATE.get('auto_run_recovery_reason')
    ):
        return None
    reason = (
        ('gate3_manual_removal_required' if _gate3_manual_removal_required()
         and _manual_grade_disabled_reason() is not None else None)
        or APP_STATE.get('last_error_reason')
        or APP_STATE.get('auto_run_recovery_reason')
        or (
            not APP_STATE.get('auto_run_enabled')
            and _auto_run_disabled_reason()
        )
    )
    if not reason:
        return None

    command = APP_STATE.get('motor_command') or {}
    feeder_result = APP_STATE.get('feeder_test_result') or {}
    if (
        reason not in (
            'feeder_max_run_timeout',
            'feeder_sensor_not_clear',
            'feeder_sensor_unavailable',
        )
        or feeder_result.get('event') != reason
    ):
        feeder_result = {}
    location = {
        'network_capability_missing': 'ESP32 / local network',
        'feed_arrival_unconfirmed': 'upstream_feeder / HC-SR04',
        'esp32_restarted_during_feed': 'ESP32 / upstream_feeder',
        'esp32_restarted_during_capture': 'ESP32 / SG90 gates',
        'esp32_restarted_during_sorter': 'ESP32 / Gate 3 / MG996R classifier',
        'django_restarted_during_sorter': 'Django / Gate 3 / MG996R classifier',
        'esp32_restarted_while_gate3_waiting': 'ESP32 / Gate 3',
        'esp32_offline': 'ESP32',
        'esp32_start_timeout': 'ESP32 / SG90 gates',
        'hardware_timeout': 'ESP32 / SG90 gates',
        'motor_error': 'ESP32 / actuator',
        'feeder_capability_missing': 'ESP32 / upstream_feeder',
        'feeder_sensor_not_clear': 'upstream_feeder / HC-SR04',
        'feeder_sensor_unavailable': 'upstream_feeder / HC-SR04',
        'feeder_max_run_timeout': 'upstream_feeder',
        'feeder_timing_not_applied': 'ESP32 / upstream_feeder',
        'feeder_state_not_idle': 'upstream_feeder',
        'camera_not_ready': 'camera station',
        'camera_upload_timeout': 'camera station',
        'capture_options_invalid': 'Django / temp',
        'dataset_commit_failed': 'dataset storage',
        'classifier_busy': 'MG996R classifier',
        'classifier_attach_failed': 'MG996R classifier',
        'gate3_release_failed': 'Gate 3 / MG996R classifier',
        'invalid_classification_code': 'MG996R classifier',
        'sorter_command_slot_busy': 'Django motor command slot',
        'classifier_timeout': 'MG996R classifier',
        'esp32_timeout': 'ESP32 / MG996R classifier',
        'gate3_manual_removal_required': 'Gate 3 / MG996R classifier',
        'active_fruit_exists': 'capture pipeline',
        'pending_motor_command': 'ESP32 motor command',
        'dataset_operation_in_progress': 'dataset storage',
        'system_not_idle': 'Django state machine',
    }.get(reason, APP_STATE.get('status') or 'unknown')
    instruction = {
        'network_capability_missing': 'ESP32 尚未支援目前的網路協定。請先選擇純拍攝模式；硬體操作需等待 Firmware 完成支援。',
        'camera_upload_timeout': '手機上傳逾時。檢查相機後刪除本輪，再按一次開始新一輪純拍攝。' if _photo_only_session() else '檢查手機相機頁與網路連線後重新拍攝。',
        'dataset_commit_failed': '照片保存狀態不確定，請檢查暫存資料與 Dataset 後再操作。',
        'capture_options_invalid': '請保留照片並檢查暫存資料的復原紀錄。',
        'feeder_max_run_timeout': (
            '已自動停止拍攝；請檢查送料筒、出口、卡料與感測區域。'
            '若果實延遲抵達，讓它完成三站流程，不要補轉。'
        ),
        'feeder_sensor_not_clear': (
            '請移除 HC-SR04 感測區內的果實，確認有效距離大於 8.0 cm 後再開始拍攝。'
        ),
        'feeder_sensor_unavailable': (
            '請檢查 HC-SR04 線材、供電、Echo 分壓與感測方向，取得有效距離後再開始拍攝。'
        ),
        'gate3_manual_removal_required': (
            '請切斷伺服電源，確認 Gate 3 與分類器不會動作後人工移除果實；'
            '手離開機構並恢復安全狀態後，再開始拍攝。'
        ),
    }.get(
        reason,
        '暫停 → 排除／重新拍攝／刪除 → 開始拍攝'
        '（請自行暫停、排除狀況後重新開始）',
    )
    return {
        'reason': reason,
        'location': location,
        'fruit_id': APP_STATE.get('active_fruit_id') or APP_STATE.get('sorter_fruit_id'),
        'command': APP_STATE.get('last_error_command') or command.get('command'),
        'command_id': APP_STATE.get('last_error_command_id') or command.get('command_id'),
        'instruction': instruction,
        'feeder_elapsed_ms': feeder_result.get('elapsed_ms'),
        'feeder_max_run_ms': feeder_result.get('max_run_ms'),
        'feeder_stop_reason': feeder_result.get('stop_reason'),
    }


def _classification_disabled_reason(active_fruit_id, image_total):
    reason = NETWORK_RECOVERY.disabled_reason('capture')
    if reason:
        return reason
    if APP_STATE.get('session_work_mode') == 'detection':
        return 'detection_mode'
    if APP_STATE.get('dataset_operation'):
        return 'dataset_operation_in_progress'
    if not active_fruit_id:
        return 'no_active_fruit'
    if image_total != IMAGE_COUNT or APP_STATE.get('status') != 'uploaded':
        return 'capture_incomplete'
    if _photo_only_session():
        return None
    return _sorter_start_disabled_reason()


def _gate3_manual_removal_required():
    if _photo_only_session():
        return False
    fruit_id = APP_STATE.get('active_fruit_id')
    if not fruit_id or APP_STATE.get('status') not in (
        'uploaded', 'error', 'detection_completed', 'detection_failed',
    ):
        return False
    return dataset_store.temp_image_count(_temp_dir() / fruit_id) == IMAGE_COUNT


def _discardable_fruit_id():
    if APP_STATE.get('active_fruit_id'):
        return APP_STATE['active_fruit_id']
    if not _photo_only_session() or APP_STATE.get('detection_status') not in (
        'completed', 'failed',
    ):
        return None
    fruit_id = (APP_STATE.get('detection_result') or {}).get('fruit_id')
    if not fruit_id or not FRUIT_ID_PATTERN.match(str(fruit_id)):
        return None
    return fruit_id if (_temp_dir() / fruit_id).is_dir() else None


def _state_payload(extra=None):
    active_fruit_id = APP_STATE['active_fruit_id']
    latest_images = _build_image_list(active_fruit_id) if active_fruit_id else []
    image_total = len(latest_images)
    is_uploading = APP_STATE['status'] == 'uploading'
    dataset_busy = bool(APP_STATE.get('dataset_operation'))
    classify_disabled_reason = _classification_disabled_reason(active_fruit_id, image_total)
    can_classify = (
        APP_STATE.get('work_mode') == 'collection'
        and classify_disabled_reason is None
    )
    discardable_fruit_id = _discardable_fruit_id()
    manual_grade_disabled_reason = _manual_grade_disabled_reason()
    can_discard = bool(discardable_fruit_id) and not is_uploading and not dataset_busy
    gate3_manual_removal_required = _gate3_manual_removal_required()
    can_recapture = not _photo_only_session() and not gate3_manual_removal_required and not dataset_busy and bool(active_fruit_id) and APP_STATE['status'] in (
        'waiting_esp32_start',
        'waiting_station_ready',
        'waiting_camera',
        'waiting_motor',
        'incomplete',
        'error',
    )
    can_reset_dataset = (
        not dataset_busy
        and not gate3_manual_removal_required
        and not APP_STATE.get('sorter_recovery_required')
        and not _sorter_busy()
    )
    payload = {
        'work_mode': APP_STATE['work_mode'],
        'session_work_mode': APP_STATE['session_work_mode'],
        'hardware_mode': APP_STATE['hardware_mode'],
        'session_hardware_mode': APP_STATE['session_hardware_mode'],
        'can_change_options': _options_disabled_reason(target_mode='photo_only') is None,
        'options_disabled_reason': _options_disabled_reason(target_mode='photo_only'),
        'can_start_photo_capture': _photo_capture_disabled_reason() is None,
        'photo_capture_disabled_reason': _photo_capture_disabled_reason(),
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
        'next_fruit_id': dataset_store.format_fruit_id(dataset_store.next_available_number(dataset_store.load_counter(_dataset_root()), _fruit_id_exists)),
        'labels': LABELS,
        'image_count': IMAGE_COUNT,
        'image_total': image_total,
        'latest_images': latest_images,
        'can_discard': can_discard,
        'discardable_fruit_id': discardable_fruit_id,
        'can_classify': can_classify,
        'classify_disabled_reason': classify_disabled_reason,
        'can_recapture': can_recapture,
        'can_reset_dataset': can_reset_dataset,
        'gate3_manual_removal_required': gate3_manual_removal_required,
        'esp32_online': _esp32_is_online(),
        'auto_run_enabled': APP_STATE.get('auto_run_enabled', False),
        'auto_run_finishing': APP_STATE.get('auto_run_finishing', False),
        'auto_run_recovery_reason': APP_STATE.get('auto_run_recovery_reason'),
        'camera_ready': _camera_is_ready(),
        'camera_last_live_frame_at': APP_STATE.get('camera_last_live_frame_at'),
        'esp32_boot_id': APP_STATE.get('esp32_boot_id'),
        'esp32_feeder_capable': APP_STATE.get('esp32_feeder_capable', False),
        'esp32_sorter_capable': APP_STATE.get('esp32_sorter_capable', False),
        'sorter_recovery_required': APP_STATE.get('sorter_recovery_required', False),
        'esp32_feeder_state': APP_STATE.get('esp32_feeder_state'),
        'feeder_sensor_state': APP_STATE.get('esp32_feeder_sensor_state'),
        'feeder_test_result': APP_STATE.get('feeder_test_result'),
        'can_test_feeder': _feeder_test_disabled_reason() is None,
        'feeder_test_disabled_reason': _feeder_test_disabled_reason(),
        'can_start_auto_run': _auto_run_disabled_reason(allow_recovery=True) is None,
        'network_recovery_confirm_disabled_reason': _network_recovery_ui_reason(),
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
        'legacy_dataset_path': str(Path(settings.DATASET_ROOT)),
        'legacy_detection_path': str(Path(settings.BASE_DIR) / 'detection_results'),
        'dataset_operation': APP_STATE.dataset_operation_payload(),
        'detection_status': APP_STATE.get('detection_status', 'idle'),
        'detection_result': _detection_result_payload(),
        'detection_progress': _with_artifact_urls(APP_STATE.get('detection_progress')),
        'photo_detection_interval_ms': round(PHOTO_DETECTION_INTERVAL_SECONDS * 1000),
        'detection_error': APP_STATE.get('detection_error'),
        'notification': _notification_payload(),
        'trend_alerts': _trend_alert_payload(),
        'can_manual_grade': manual_grade_disabled_reason is None,
        'can_manual_abandon': manual_grade_disabled_reason is None and _photo_only_session(),
        'manual_grade_disabled_reason': manual_grade_disabled_reason,
        **_sorter_state_payload(),
    }
    if extra:
        payload.update(extra)
    network = local_network.NETWORK.snapshot()
    network['setup'] = _network_setup_payload()
    payload['network'] = network
    recovery = APP_STATE.get('network_recovery')
    payload['network_recovery'] = recovery
    payload['recovery_generation'] = APP_STATE.get('recovery_generation', 0)
    NETWORK_RECOVERY.apply_payload(payload, network)
    if not network['selected']:
        payload['camera_ready'] = False
        for key in ('can_change_options', 'can_start_photo_capture', 'can_classify',
                    'can_manual_grade',
                    'can_recapture', 'can_test_feeder', 'can_start_auto_run'):
            payload[key] = False
        payload['options_disabled_reason'] = network['reason']
        payload['photo_capture_disabled_reason'] = network['reason']
    if not _photo_only_session() and _network_hardware_disabled_reason():
        payload['can_classify'] = False
        payload['can_recapture'] = False
    return payload


def _detection_result_payload():
    payload = _with_artifact_urls(APP_STATE.get('detection_result'))
    if not payload or payload.get('hardware_mode') == 'photo_only':
        return payload
    if payload['fruit_id'] == APP_STATE.get('sorter_fruit_id'):
        payload['sorting'] = {
            'status': APP_STATE.get('sorter_status'),
            'command_id': APP_STATE.get('sorter_command_id'),
            'reason': APP_STATE.get('sorter_error'),
            'recovery_required': bool(APP_STATE.get('sorter_recovery_required') or NETWORK_RECOVERY.locked),
        }
    elif (NETWORK_RECOVERY.locked or not APP_STATE.get('gate3_waiting_boot_id')
          or APP_STATE.get('gate3_waiting_boot_id') != APP_STATE.get('esp32_boot_id')):
        payload['sorting'] = {'status': 'not_started', 'reason': 'gate3_boot_unconfirmed',
                              'recovery_required': True}
    return payload


def _with_artifact_urls(result):
    if not result:
        return None
    payload = json.loads(json.dumps(result, ensure_ascii=False))
    fruit_id = payload.get('fruit_id')
    for image in payload.get('images', {}).values():
        image['artifact_urls'] = {
            name: f'/api/detection/image/{fruit_id}/{filename}/?work_mode=detection'
            for name, filename in image.get('artifacts', {}).items()
        }
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


def _restore_capture_options(fruit_dir):
    try:
        options = dataset_store.read_capture_options(fruit_dir)
        if options['work_mode'] != APP_STATE['work_mode']:
            raise ValueError('Capture session does not match dataset work mode')
    except (OSError, ValueError) as exc:
        APP_STATE['session_hardware_mode'] = 'hardware'
        APP_STATE['status'] = 'error'
        APP_STATE['last_error_reason'] = 'capture_options_invalid'
        APP_STATE['message'] = f'無法確認本輪硬體選項，請保留照片並檢查復原紀錄：{exc}'
        return
    APP_STATE['session_hardware_mode'] = options['hardware_mode']
    APP_STATE['hardware_mode'] = options['hardware_mode']
    APP_STATE['session_work_mode'] = options['work_mode']
    APP_STATE['work_mode'] = options['work_mode']
    APP_STATE['capture_time'] = options.get('capture_time') or APP_STATE['capture_time']


def _sync_active_state_with_filesystem():
    if NETWORK_RECOVERY.disabled_reason('capture'):
        fruit_id = APP_STATE.get('active_fruit_id')
        if fruit_id and not (_temp_dir() / fruit_id).exists():
            APP_STATE['active_fruit_id'] = None
        if not APP_STATE.get('active_fruit_id'):
            pending = sorted(path for path in _temp_dir().glob('fruit_*')
                             if path.is_dir() and FRUIT_ID_PATTERN.match(path.name)
                             and not dataset_store.is_deferred_discard_path(_dataset_root(), path))
            if pending:
                APP_STATE['active_fruit_id'] = pending[0].name
        APP_STATE['pending_capture'] = False
        APP_STATE['status'] = 'error'
        return
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
            'photo_interval',
            'uploading',
            'waiting_motor',
            'error',
            'detection_pending',
            'detection_running',
            'detection_completed',
            'detection_failed',
        ):
            APP_STATE['pending_capture'] = False
            if APP_STATE['status'] == 'waiting_camera':
                APP_STATE['pending_capture'] = True
            APP_STATE['message'] = APP_STATE['message'] or f'{active_fruit_id} 拍攝流程進行中。'
            return

        image_total = dataset_store.temp_image_count(active_dir)
        if image_total == IMAGE_COUNT:
            APP_STATE['pending_capture'] = False
            APP_STATE['status'] = 'uploaded'
            if not APP_STATE['message']:
                APP_STATE['message'] = f'{active_fruit_id} 已收到 {IMAGE_COUNT} 張照片，請確認後分類。'
            return

        if image_total == 0 and not APP_STATE['pending_capture']:
            try:
                dataset_store.safe_rmtree(active_dir)
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
            and not dataset_store.is_ignored_reset_path(_dataset_root(), path)
            and not dataset_store.is_deferred_discard_path(_dataset_root(), path)
        )
    )
    non_empty_dirs = []
    terminal_dirs = []
    removed_empty = False
    for fruit_dir in temp_fruit_dirs:
        if _completed_photo_only_detection_input(fruit_dir):
            if _saved_detection_result(fruit_dir.name) is not None:
                _clear_saved_photo_input(fruit_dir, fruit_dir.name)
                continue
            terminal_dirs.append(fruit_dir)
            continue
        image_total = dataset_store.temp_image_count(fruit_dir)
        if image_total == 0:
            try:
                dataset_store.safe_rmtree(fruit_dir)
                removed_empty = True
            except DatasetFileBusyError as exc:
                removed_empty = True
                APP_STATE['message'] = f'空的暫存資料夾暫時刪不掉，將在之後自動重用或再清理。{exc}'
        else:
            non_empty_dirs.append(fruit_dir)

    if not non_empty_dirs and terminal_dirs and APP_STATE.get('detection_result') is None:
        non_empty_dirs = [max(terminal_dirs, key=lambda path: dataset_store.parse_fruit_number(path.name))]

    if len(non_empty_dirs) > 1:
        fruit_dir = non_empty_dirs[0]
        APP_STATE['active_fruit_id'] = fruit_dir.name
        APP_STATE['pending_capture'] = False
        APP_STATE['capture_time'] = _now_string()
        APP_STATE['status'] = 'multiple_temp'
        APP_STATE['message'] = 'temp 內有多個未處理資料夾，請逐筆刪除或重置 dataset。'
        _restore_capture_options(fruit_dir)
        return

    if len(non_empty_dirs) != 1:
        if removed_empty and not non_empty_dirs:
            _clear_active_state('已自動清除空的暫存資料夾，可重新拍攝。', status='idle')
        return

    fruit_dir = non_empty_dirs[0]
    image_total = dataset_store.temp_image_count(fruit_dir)
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
    _restore_capture_options(fruit_dir)
    if completed and APP_STATE['session_work_mode'] == 'detection':
        restored = detection.load_result(
            _detection_output_root(), fruit_dir.name
        )
        if restored is None:
            image_paths = [fruit_dir / name for name in IMAGE_FILENAMES]
            session = _detection_session(fruit_dir)
            try:
                restored = detection.save_interrupted_result(
                    fruit_id=fruit_dir.name,
                    image_paths=image_paths,
                    output_root=_detection_output_root(),
                    summary_path=_detection_summary_path(),
                    **session,
                )
            except OSError as exc:
                save_error = f'{type(exc).__name__}: {exc}'
                restored = detection.interrupted_result(
                    fruit_id=fruit_dir.name,
                    image_paths=image_paths,
                    save_errors=[save_error],
                    **session,
                )
                try:
                    (fruit_dir / '.detection-terminal').write_text(
                        save_error, encoding='utf-8'
                    )
                except OSError as marker_error:
                    restored['save_errors'].append(
                        f'{type(marker_error).__name__}: {marker_error}'
                    )
                APP_STATE['detection_error'] = save_error
        APP_STATE['detection_result'] = restored
        APP_STATE['detection_status'] = (
            restored.get('status', 'failed') if restored else 'failed'
        )
        APP_STATE['status'] = (
            'detection_completed'
            if restored and restored.get('complete')
            else 'detection_failed'
        )
        APP_STATE['message'] = (
            f'已恢復 {fruit_dir.name} 的檢測結果。'
            if restored.get('reason') != 'service_restarted'
            else f'{fruit_dir.name} 的服務曾在檢測期間中斷；保留原圖且不自動重跑。'
        )
        if _photo_only_session():
            _clear_active_state(APP_STATE['message'], status=APP_STATE['status'])
        else:
            _disable_auto_run()
            APP_STATE['auto_run_finishing'] = False
            APP_STATE['gate3_waiting_boot_id'] = None
            APP_STATE['message'] = (
                f'{APP_STATE["message"]} Gate 3 狀態未自動復原，'
                '請依人工安全復原提示處理果實。'
            )


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
    APP_STATE['photo_next_capture'] = None
    APP_STATE['gate3_waiting_boot_id'] = None
    APP_STATE['motor_command'] = None
    APP_STATE['last_error_reason'] = None
    APP_STATE['status'] = status
    APP_STATE['message'] = message
    _clear_wait_timer()
    _reset_timing_state()


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


def _retry_deferred_discards_if_due():
    now = time.monotonic()
    # ponytail: single-process maintenance gate; add a dedicated lock only if
    # concurrent dashboard pollers become a supported deployment mode.
    last_attempt = APP_STATE.get('discard_cleanup_last_monotonic')
    if last_attempt is not None and now - last_attempt < DISCARD_CLEANUP_RETRY_INTERVAL_SECONDS:
        return
    APP_STATE['discard_cleanup_last_monotonic'] = now

    state = dataset_store.read_discard_state(_dataset_root())
    remaining_entries = []
    for entry in state['pending_paths']:
        cleanup_path = _dataset_root() / entry.get('cleanup_path', '')
        if not cleanup_path.exists():
            continue
        try:
            if cleanup_path.is_dir():
                dataset_store.safe_rmtree(cleanup_path)
            else:
                dataset_store.safe_unlink(cleanup_path)
        except DatasetFileBusyError:
            remaining_entries.append(entry)

    if remaining_entries != state['pending_paths']:
        dataset_store.write_discard_state(_dataset_root(), {'pending_paths': remaining_entries})


def _save_station_image(fruit_dir, station_index, image_file):
    result = dataset_store.save_station_image(
        Path(fruit_dir),
        station_index,
        image_file,
        image_filenames=IMAGE_FILENAMES,
        staging_suffix=UPLOAD_STAGING_SUFFIX,
        safe_unlink=dataset_store.safe_unlink,
        safe_replace=dataset_store.safe_replace,
    )
    active_fruit_id = APP_STATE.get('active_fruit_id')
    if active_fruit_id:
        _load_image_list.cache_clear()
    return result


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
