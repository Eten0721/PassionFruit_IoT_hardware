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


LABELS = ['上中等', '下等', '廢棄', '加工']
IMAGE_COUNT = 6
IMAGE_FILENAMES = [f'img_{idx:02d}.jpg' for idx in range(1, IMAGE_COUNT + 1)]
FRUIT_ID_PATTERN = re.compile(r'^fruit_(\d{3,})$')
DEFAULT_CAPTURE_INTERVAL_MS = 220
MIN_CAPTURE_INTERVAL_MS = 0
MAX_CAPTURE_INTERVAL_MS = 3000
FILE_OPERATION_RETRIES = 8
FILE_OPERATION_RETRY_DELAY_SECONDS = 0.15
UPLOAD_STAGING_SUFFIX = '.uploading'

STATE_LOCK = threading.RLock()
APP_STATE = {
    'pending_capture': False,
    'capture_token': 0,
    'active_fruit_id': None,
    'capture_time': None,
    'capture_interval_ms': DEFAULT_CAPTURE_INTERVAL_MS,
    'command_created_at': None,
    'command_created_monotonic': None,
    'capture_started_at': None,
    'capture_started_monotonic': None,
    'command_to_phone_start_ms': None,
    'phone_capture_timestamps_ms': [],
    'phone_capture_intervals_ms': [],
    'upload_received_at': None,
    'status': 'idle',
    'message': '等待手機連線與手動拍攝。',
}
WEBRTC_STATE = {
    'offer': None,
    'answer': None,
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


def home_view(request):
    return render(request, 'home.html')


def camera_view(request):
    _ensure_dataset_structure()
    return render(request, 'camera.html')


def dashboard_view(request):
    _ensure_dataset_structure()
    return render(request, 'dashboard.html')


@require_GET
def state_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        payload = _state_payload()
    return JsonResponse(payload)


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
def manual_capture_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    with STATE_LOCK:
        capture_interval_ms = _parse_capture_interval_ms(data.get('capture_interval_ms'))
        try:
            payload = _create_capture_command(capture_interval_ms, source='manual')
        except CaptureCommandError as exc:
            return _json_error(exc.message, status=exc.status)
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def esp32_trigger_api(request):
    _ensure_dataset_structure()
    with STATE_LOCK:
        capture_interval_ms = DEFAULT_CAPTURE_INTERVAL_MS
        try:
            payload = _create_capture_command(capture_interval_ms, source='esp32')
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


@csrf_exempt
@require_POST
def recapture_api(request):
    _ensure_dataset_structure()
    data = _request_data(request)
    with STATE_LOCK:
        _sync_active_state_with_filesystem()
        if APP_STATE['status'] == 'uploading':
            return _json_error('照片正在上傳中，請等待上傳完成後再重新拍攝。', status=409)
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

        capture_interval_ms = _parse_capture_interval_ms(data.get('capture_interval_ms'))
        _set_capture_command(
            fruit_id,
            capture_interval_ms,
            message=f'已重新觸發 {fruit_id}，等待手機連拍 {IMAGE_COUNT} 張。',
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
        if fruit_id != APP_STATE['active_fruit_id'] or capture_token != APP_STATE['capture_token']:
            return _json_error('拍攝命令已過期，請重新拍攝。', status=409)

        APP_STATE['capture_started_at'] = _now_string()
        APP_STATE['capture_started_monotonic'] = time.monotonic()
        command_created = APP_STATE.get('command_created_monotonic')
        if command_created is not None:
            APP_STATE['command_to_phone_start_ms'] = round(
                (APP_STATE['capture_started_monotonic'] - command_created) * 1000,
                1,
            )
        APP_STATE['message'] = f'{fruit_id} 手機端已開始拍攝。'
        payload = {'status': 'success', 'timing': _timing_payload()}
    return JsonResponse(payload)


@csrf_exempt
@require_POST
def upload_images_api(request):
    _ensure_dataset_structure()
    image_files = request.FILES.getlist('images')
    if len(image_files) != IMAGE_COUNT:
        return _json_error(f'必須一次上傳 {IMAGE_COUNT} 張照片。', status=400)

    with STATE_LOCK:
        active_fruit_id = APP_STATE['active_fruit_id']
        if not active_fruit_id:
            return _json_error('目前沒有等待上傳的 fruit 資料夾。', status=409)
        request_fruit_id = request.POST.get('fruit_id')
        request_capture_token = _safe_int(request.POST.get('capture_token'))
        if request_fruit_id != active_fruit_id or request_capture_token != APP_STATE['capture_token']:
            return _json_error('上傳命令已過期，請重新拍攝。', status=409)
        fruit_dir = _temp_dir() / active_fruit_id
        if not fruit_dir.exists():
            return _json_error('暫存資料夾不存在，請重新拍攝。', status=404)
        APP_STATE['pending_capture'] = False
        APP_STATE['status'] = 'uploading'
        APP_STATE['message'] = f'{active_fruit_id} 正在接收照片，請勿刪除或重置。'

    try:
        _replace_temp_images(fruit_dir, image_files)
    except (DatasetFileBusyError, OSError) as exc:
        with STATE_LOCK:
            if (
                APP_STATE['active_fruit_id'] == active_fruit_id
                and APP_STATE['capture_token'] == request_capture_token
            ):
                APP_STATE['pending_capture'] = True
                APP_STATE['status'] = 'waiting_camera'
                APP_STATE['message'] = f'{active_fruit_id} 上傳失敗，手機端可重試。{exc}'
        return _json_error(f'照片上傳失敗：{exc}', status=409, reason='upload_failed')

    with STATE_LOCK:
        if active_fruit_id != APP_STATE['active_fruit_id'] or request_capture_token != APP_STATE['capture_token']:
            return _json_error('上傳完成時拍攝命令已過期，請重新拍攝。', status=409)
        _apply_capture_meta(request.POST.get('capture_meta'))
        APP_STATE['pending_capture'] = False
        APP_STATE['status'] = 'uploaded'
        APP_STATE['upload_received_at'] = _now_string()
        APP_STATE['message'] = f'{active_fruit_id} 已收到 {IMAGE_COUNT} 張照片，請在 dashboard 確認後分類。'
        payload = _state_payload(extra={'uploaded_fruit_id': active_fruit_id})
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
        _append_metadata(fruit_id, label, capture_time, relative_path, note)

        fruit_number = _fruit_number(fruit_id)
        if fruit_number is not None:
            _write_counter(max(_read_counter(), fruit_number + 1))

        APP_STATE['pending_capture'] = False
        APP_STATE['active_fruit_id'] = None
        APP_STATE['capture_time'] = None
        APP_STATE['status'] = 'classified'
        APP_STATE['message'] = f'{fruit_id} 已分類為「{label}」。'
        _reset_timing_state(keep_interval=True)
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
        delete_warnings = []
        if fruit_dir.exists():
            try:
                _safe_rmtree(fruit_dir)
            except DatasetFileBusyError as exc:
                if _is_reusable_temp_dir(fruit_dir):
                    delete_warnings.append(str(exc))
                else:
                    APP_STATE['pending_capture'] = False
                    APP_STATE['status'] = 'delete_failed'
                    APP_STATE['message'] = str(exc)
                    payload = _state_payload(extra={'delete_warnings': [str(exc)]})
                    return JsonResponse(payload, status=409)

        APP_STATE['pending_capture'] = False
        APP_STATE['active_fruit_id'] = None
        APP_STATE['capture_time'] = None
        APP_STATE['status'] = 'discarded'
        if delete_warnings:
            APP_STATE['message'] = f'{fruit_id} 已解除目前狀態；空資料夾暫時刪不掉，之後會自動重用或再清理。'
        else:
            APP_STATE['message'] = f'{fruit_id} 已刪除，counter 不會自動增加。'
        _reset_timing_state(keep_interval=True)
        payload = _state_payload(extra={'discarded_fruit_id': fruit_id, 'delete_warnings': delete_warnings})
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
        _reset_timing_state(keep_interval=True)
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
        WEBRTC_STATE['offer'] = offer
        WEBRTC_STATE['answer'] = None
        WEBRTC_STATE['dashboard_ice'] = []
        WEBRTC_STATE['camera_ice'] = []
        WEBRTC_STATE['updated_at'] = _now_string()
    return JsonResponse({'status': 'success'})


@csrf_exempt
@require_POST
def webrtc_answer_api(request):
    data = _request_data(request)
    answer = data.get('answer') or data.get('description') or data
    if not _is_session_description(answer, 'answer'):
        return _json_error('WebRTC answer 格式不正確。', status=400)

    with STATE_LOCK:
        WEBRTC_STATE['answer'] = answer
        WEBRTC_STATE['updated_at'] = _now_string()
    return JsonResponse({'status': 'success'})


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

    key = 'dashboard_ice' if role == 'dashboard' else 'camera_ice'
    with STATE_LOCK:
        WEBRTC_STATE[key].append(candidate)
        WEBRTC_STATE['updated_at'] = _now_string()
    return JsonResponse({'status': 'success'})


@require_GET
def webrtc_state_api(request):
    with STATE_LOCK:
        payload = {
            'offer': WEBRTC_STATE['offer'],
            'answer': WEBRTC_STATE['answer'],
            'dashboard_ice': WEBRTC_STATE['dashboard_ice'],
            'camera_ice': WEBRTC_STATE['camera_ice'],
            'updated_at': WEBRTC_STATE['updated_at'],
        }
    return JsonResponse(payload)


def reset_runtime_state_for_tests():
    with STATE_LOCK:
        APP_STATE.update({
            'pending_capture': False,
            'capture_token': 0,
            'active_fruit_id': None,
            'capture_time': None,
            'capture_interval_ms': DEFAULT_CAPTURE_INTERVAL_MS,
            'command_created_at': None,
            'command_created_monotonic': None,
            'capture_started_at': None,
            'capture_started_monotonic': None,
            'command_to_phone_start_ms': None,
            'phone_capture_timestamps_ms': [],
            'phone_capture_intervals_ms': [],
            'upload_received_at': None,
            'status': 'idle',
            'message': '等待手機連線與手動拍攝。',
        })
        WEBRTC_STATE.update({
            'offer': None,
            'answer': None,
            'dashboard_ice': [],
            'camera_ice': [],
            'updated_at': None,
        })


def _dataset_root():
    return Path(settings.DATASET_ROOT)


def _temp_dir():
    return _dataset_root() / 'temp'


def _counter_path():
    return _dataset_root() / 'counter.json'


def _metadata_path():
    return _dataset_root() / 'metadata.csv'


def _reset_state_path():
    return _dataset_root() / 'reset_state.json'


def _delete_pending_dir():
    return _dataset_root() / '_delete_pending'


def _ensure_dataset_structure():
    root = _dataset_root()
    root.mkdir(parents=True, exist_ok=True)
    _temp_dir().mkdir(parents=True, exist_ok=True)
    for label in LABELS:
        (root / label).mkdir(parents=True, exist_ok=True)
    if not _counter_path().exists():
        _write_counter(1)
    if not _metadata_path().exists():
        with _metadata_path().open('w', encoding='utf-8-sig', newline='') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(['fruit_id', 'label', 'capture_time', 'path', 'note'])


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


def _append_metadata(fruit_id, label, capture_time, relative_path, note):
    _ensure_dataset_structure()
    with _metadata_path().open('a', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([fruit_id, label, capture_time, relative_path, note])


def _create_capture_command(capture_interval_ms, source):
    _sync_active_state_with_filesystem()
    if APP_STATE['active_fruit_id']:
        raise CaptureCommandError(
            '目前已有未分類的暫存資料，請先分類或刪除。',
            reason='active_fruit_exists',
        )

    fruit_number = _next_available_number(_read_counter())
    fruit_id = _format_fruit_id(fruit_number)
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

    if source == 'esp32':
        message = f'ESP32 trigger 已建立 {fruit_id}，等待手機連拍 {IMAGE_COUNT} 張。'
    else:
        message = f'已建立 {fruit_id}，等待手機連拍 {IMAGE_COUNT} 張。'
    _set_capture_command(fruit_id, capture_interval_ms, message=message)

    return {
        'status': 'success',
        'fruit_id': fruit_id,
        'capture_token': APP_STATE['capture_token'],
        'pending_capture': True,
        'capture_interval_ms': capture_interval_ms,
    }


def _set_capture_command(fruit_id, capture_interval_ms, message):
    APP_STATE['pending_capture'] = True
    APP_STATE['capture_token'] += 1
    APP_STATE['active_fruit_id'] = fruit_id
    APP_STATE['capture_time'] = _now_string()
    APP_STATE['capture_interval_ms'] = capture_interval_ms
    APP_STATE['command_created_at'] = _now_string()
    APP_STATE['command_created_monotonic'] = time.monotonic()
    APP_STATE['capture_started_at'] = None
    APP_STATE['capture_started_monotonic'] = None
    APP_STATE['command_to_phone_start_ms'] = None
    APP_STATE['phone_capture_timestamps_ms'] = []
    APP_STATE['phone_capture_intervals_ms'] = []
    APP_STATE['upload_received_at'] = None
    APP_STATE['status'] = 'waiting_camera'
    APP_STATE['message'] = message


def _state_payload(extra=None):
    active_fruit_id = APP_STATE['active_fruit_id']
    latest_images = _build_image_list(active_fruit_id) if active_fruit_id else []
    image_total = len(latest_images)
    is_uploading = APP_STATE['status'] == 'uploading'
    can_classify = bool(active_fruit_id) and image_total == IMAGE_COUNT and APP_STATE['status'] == 'uploaded'
    can_discard = bool(active_fruit_id) and not is_uploading
    can_manual_capture = not active_fruit_id
    can_recapture = bool(active_fruit_id) and APP_STATE['status'] in ('waiting_camera', 'incomplete', 'uploaded')
    payload = {
        'status': APP_STATE['status'],
        'message': APP_STATE['message'],
        'pending_capture': APP_STATE['pending_capture'],
        'capture_token': APP_STATE['capture_token'],
        'capture_interval_ms': APP_STATE['capture_interval_ms'],
        'active_fruit_id': active_fruit_id,
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
        'timing': _timing_payload(),
        'dataset_path': str(_dataset_root()),
    }
    if extra:
        payload.update(extra)
    return payload


def _sync_active_state_with_filesystem():
    active_fruit_id = APP_STATE['active_fruit_id']
    if active_fruit_id:
        active_dir = _temp_dir() / active_fruit_id
        if not active_dir.exists():
            _clear_active_state('目前沒有暫存資料，可重新拍攝。', status='idle')
            return

        if APP_STATE['status'] == 'uploading':
            APP_STATE['pending_capture'] = False
            APP_STATE['message'] = APP_STATE['message'] or f'{active_fruit_id} 正在接收照片，請勿刪除或重置。'
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
        if path.is_dir() and FRUIT_ID_PATTERN.match(path.name)
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
    APP_STATE['active_fruit_id'] = None
    APP_STATE['capture_time'] = None
    APP_STATE['status'] = status
    APP_STATE['message'] = message
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
        writer = csv.writer(csv_file)
        writer.writerow(['fruit_id', 'label', 'capture_time', 'path', 'note'])
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
