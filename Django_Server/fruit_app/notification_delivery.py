"""Durable LINE delivery of processing-fruit notifications.

One JSON outbox holds the per-mode switches and one entry per ``result_id``.
Entries are created only when a final processing result is saved while the
switch for its mode is on; delivery happens later on a background worker, so
Cloudinary or LINE failures never block detection or sorting. Each entry keeps
one ``X-Line-Retry-Key`` for its lifetime: a retry after a timeout or a
restart is answered by LINE with 409 instead of a second message. When the
outcome stays unknown past the retry-key window, the entry becomes
``uncertain`` and is never resent automatically.

Trend alerts (``kind`` = ``trend_alert``) share the outbox and retry rules but
carry their final text and are sent as one plain text message; their own
switch lives in ``trend_alerts.py`` and is applied before queueing.

Credentials come only from a private JSON file next to the outbox; they never
enter the outbox, status payloads or error text. The push and retry rules
(retry key, 409 = already accepted, 4xx not retried as-is) follow the delivered
``line_notifier.py``, extended to one Flex bubble (composite + text) and a
durable per-result key.
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, NamedTuple

from PIL import Image

from . import dataset_store, grading
from .notification import generate_notification


MODES = ('hardware', 'photo_only')
ALERT_KIND = 'trend_alert'
STATUSES = ('pending', 'sent', 'failed', 'uncertain', 'skipped')
# Socket errors that prove the request never left this machine.
UNSENT_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
                 10050, 10051, 10065}  # Windows WSAENETDOWN/UNREACH, WSAEHOSTUNREACH
PUSH_URL = 'https://api.line.me/v2/bot/message/push'
UPLOAD_URL = 'https://api.cloudinary.com/v1_1/{cloud}/image/upload'
PUBLIC_ID_PREFIX = 'passionfruit_notifications/'
# Flex hero images must stay within 1024 x 1024 px; the full image opens on tap.
PREVIEW_TRANSFORMATION = 'c_limit,w_960,h_960,q_auto:good,f_jpg'
FALLBACK_ASPECT_RATIO = '2:1'
ALT_TEXT_LIMIT = 400
CREDENTIAL_FIELDS = (
    'line_channel_access_token', 'line_group_id',
    'cloudinary_cloud_name', 'cloudinary_api_key', 'cloudinary_api_secret',
)
# LINE keeps a retry key for 24 h after its first request; stay inside it.
RETRY_KEY_WINDOW_SECONDS = 23 * 3600
BACKOFF_SECONDS = 30
BACKOFF_MAX_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 15
WORKER_POLL_SECONDS = 5

_STATE_LOCK = threading.RLock()
_DELIVERY_LOCK = threading.Lock()
_WAKE = threading.Event()
logger = logging.getLogger(__name__)


class HttpResponse(NamedTuple):
    status: int
    headers: dict
    body: bytes


class NetworkError(Exception):
    """No HTTP response; ``maybe_delivered`` is False only when nothing was sent."""

    def __init__(self, detail: str, *, maybe_delivered: bool):
        super().__init__(detail)
        self.maybe_delivered = maybe_delivered


def http_post(url: str, body: bytes, headers: dict, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, data=body, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers or {}), error.read())
    except urllib.error.URLError as error:
        reason = error.reason
        not_sent = isinstance(reason, (socket.gaierror, ConnectionRefusedError)) or (
            isinstance(reason, OSError) and reason.errno in UNSENT_ERRNOS)
        raise NetworkError(str(error.reason), maybe_delivered=not not_sent) from None
    except (TimeoutError, http.client.HTTPException, OSError) as error:
        raise NetworkError(f'{type(error).__name__}: {error}', maybe_delivered=True) from None


def wake() -> None:
    _WAKE.set()


def run_worker(outbox_factory: Callable[[], 'Outbox'], stop: threading.Event) -> None:
    """Deliver due entries until ``stop``; one failing pass never ends the loop."""
    while not stop.is_set():
        try:
            outbox_factory().deliver_pending()
        except Exception:
            logger.exception('LINE notification delivery pass failed')
        _WAKE.wait(WORKER_POLL_SECONDS)
        _WAKE.clear()


class _Rejected(Exception):
    """A definite service answer that ends this attempt."""

    def __init__(self, reason: str, *, final: bool):
        super().__init__(reason)
        self.reason = reason
        self.final = final


class Outbox:
    def __init__(self, state_path: Path, credentials_path: Path, *,
                 http_post: Callable = http_post, now: Callable[[], float] = time.time,
                 font_path: Path | None = None):
        self.state_path = Path(state_path)
        self.credentials_path = Path(credentials_path)
        self.image_dir = self.state_path.with_name('notification_images')
        self._http_post = http_post
        self._now = now
        self._font_path = font_path

    # -- switches and queueing -------------------------------------------

    def set_enabled(self, mode: str, enabled: bool) -> None:
        if mode not in MODES:
            raise ValueError(f'未知的通知模式：{mode!r}')
        if type(enabled) is not bool:
            raise ValueError('通知開關必須是 true 或 false。')
        with _STATE_LOCK:
            state = self._read()
            state['enabled'][mode] = enabled
            self._write(state)
        if enabled:
            wake()

    def enqueue(self, result: dict, result_path: Path) -> dict:
        """Queue a saved final processing result once; never send here."""
        classification = result.get('classification') or {}
        if (result.get('storage') or {}).get('status') != 'saved':
            return {'ok': True, 'queued': False, 'reason': 'result_not_saved'}
        if classification.get('status') != 'graded':
            return {'ok': True, 'queued': False, 'reason': 'not_graded'}
        if grading.grade_label(classification.get('grade'))[0] != '加工':
            return {'ok': True, 'queued': False, 'reason': 'not_processing'}
        mode = result.get('hardware_mode')
        result_id = result.get('result_id')
        if mode not in MODES or not isinstance(result_id, str) or not result_id:
            raise ValueError('檢測結果缺少拍攝來源或穩定結果識別。')
        with _STATE_LOCK:
            state = self._read()
            if result_id in state['entries']:
                return {'ok': True, 'queued': False, 'reason': 'duplicate'}
            enabled = state['enabled'][mode]
            state['entries'][result_id] = {
                'result_id': result_id, 'fruit_id': result.get('fruit_id'),
                'hardware_mode': mode, 'capture_time': result.get('capture_time'),
                'result_json': str(Path(result_path).resolve()),
                # A result saved while switched off is remembered as skipped so
                # that a later save of the same result is never backfilled.
                'queued_at': self._now(), 'status': 'pending' if enabled else 'skipped',
                'retry_key': str(uuid.uuid4()),
                'text': None, 'image_url': None, 'preview_url': None,
                'attempts': 0, 'next_attempt_at': self._now(), 'last_error': None,
                'key_first_used_at': None, 'unknown_since': None,
                'sent_at': None, 'accepted_request_id': None,
            }
            self._write(state)
        if not enabled:
            return {'ok': True, 'queued': False, 'reason': 'notifications_disabled'}
        wake()
        return {'ok': True, 'queued': True, 'reason': None}

    def enqueue_alert(self, alert_id: str, text: str) -> dict:
        """Queue one trend-alert text once; never send here."""
        if not isinstance(alert_id, str) or not alert_id.startswith('trend_'):
            raise ValueError('趨勢提醒識別必須以 trend_ 開頭。')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('趨勢提醒內容不得為空。')
        with _STATE_LOCK:
            state = self._read()
            if alert_id in state['entries']:
                return {'ok': True, 'queued': False, 'reason': 'duplicate'}
            state['entries'][alert_id] = {
                'result_id': alert_id, 'kind': ALERT_KIND, 'hardware_mode': None,
                'queued_at': self._now(), 'status': 'pending',
                'retry_key': str(uuid.uuid4()), 'text': text,
                'attempts': 0, 'next_attempt_at': self._now(), 'last_error': None,
                'key_first_used_at': None, 'unknown_since': None,
                'sent_at': None, 'accepted_request_id': None,
            }
            self._write(state)
        wake()
        return {'ok': True, 'queued': True, 'reason': None}

    def entry_statuses(self, entry_ids) -> dict:
        """Delivery status per entry id, without message bodies or recipients."""
        with _STATE_LOCK:
            entries = self._read()['entries']
        return {
            entry_id: {'status': entries[entry_id]['status'],
                       'reason': entries[entry_id]['last_error']}
            for entry_id in entry_ids if entry_id in entries
        }

    def configuration(self) -> dict:
        credentials = self._credentials()
        return {'configured': credentials['ok'],
                'config_reason': None if credentials['ok'] else credentials['reason'],
                'config_missing_fields': credentials.get('missing_fields', [])}

    def status(self, mode: str, result_id: str | None = None) -> dict:
        """Dashboard view without credentials, recipients or message bodies."""
        with _STATE_LOCK:
            state = self._read()
        entries = [entry for entry in state['entries'].values()
                   if entry.get('hardware_mode') == mode]
        counts = dict.fromkeys(STATUSES, 0)
        for entry in entries:
            counts[entry.get('status')] = counts.get(entry.get('status'), 0) + 1
        errors = [entry for entry in entries
                  if entry.get('status') not in ('sent', 'skipped') and entry.get('last_error')]
        current = state['entries'].get(result_id) if result_id else None
        return {
            'mode': mode, 'enabled': state['enabled'].get(mode, False),
            **self.configuration(),
            'counts': counts,
            'paused': 0 if state['enabled'].get(mode) else counts['pending'],
            'last_error': max(errors, key=lambda e: e['next_attempt_at'])['last_error']
            if errors else None,
            'current': None if current is None else {
                'status': current['status'], 'reason': current['last_error'],
                'sent_at': current['sent_at'],
            },
        }

    # -- delivery ----------------------------------------------------------

    def deliver_pending(self) -> list[dict]:
        """Try every due entry of an enabled mode once; network runs unlocked."""
        if not _DELIVERY_LOCK.acquire(blocking=False):
            return []
        try:
            credentials = self._credentials()
            if not credentials['ok']:
                return []
            with _STATE_LOCK:
                state = self._read()
            due = sorted(
                (entry for entry in state['entries'].values() if self._due(state, entry)),
                key=lambda entry: entry['queued_at'],
            )
            return [self._deliver_isolated(entry['result_id'], credentials['values'])
                    for entry in due]
        finally:
            _DELIVERY_LOCK.release()

    def _deliver_isolated(self, result_id, credentials):
        # A local disk error leaves the entry pending with its retry key; the
        # next pass retries, and other due entries still get their turn.
        try:
            return self._deliver(result_id, credentials)
        except OSError as error:
            logger.warning('LINE notification %s hit a local I/O error: %s', result_id, error)
            return {'result_id': result_id, 'status': 'pending',
                    'reason': f'本機讀寫失敗：{type(error).__name__}'}

    def _due(self, state, entry):
        if entry['status'] != 'pending' or entry['next_attempt_at'] > self._now():
            return False
        # A trend alert passed its own switch when queued.
        return entry.get('kind') == ALERT_KIND or state['enabled'][entry['hardware_mode']]

    def _deliver(self, result_id, credentials):
        with _STATE_LOCK:
            state = self._read()
            entry = state['entries'][result_id]
            if not self._due(state, entry):
                # Switched off or handled while earlier entries were sent.
                return {'result_id': result_id, 'status': entry['status'], 'reason': None}
        if (entry['unknown_since'] is not None
                and self._now() - entry['key_first_used_at'] >= RETRY_KEY_WINDOW_SECONDS):
            return self._update(result_id, status='uncertain', last_error=(
                'LINE 未回覆是否收到，重送防重期限已過，無法確認是否已送達；'
                '請到群組確認，系統不會自動重送。'))
        try:
            if entry.get('kind') != ALERT_KIND and entry['image_url'] is None:
                entry = self._prepare(entry, credentials)
            return self._push(entry, credentials)
        except _Rejected as rejection:
            return self._retry_later(result_id, rejection.reason, final=rejection.final)

    def _prepare(self, entry, credentials):
        try:
            preview = generate_notification(
                Path(entry['result_json']), output_dir=self.image_dir,
                font_path=self._font_path,
            )
        except (OSError, ValueError) as error:
            raise _Rejected(f'通知內容無法產生：{error}', final=True) from None
        if not preview['ok']:
            raise _Rejected(f"通知內容無法產生：{preview['reason']}", final=True)
        try:
            with Image.open(preview['image_path']) as image:
                aspect_ratio = f'{image.width}:{image.height}'
        except OSError as error:
            raise _Rejected(f'通知合成圖無法讀取：{type(error).__name__}', final=False) from None
        image_url = self._upload(Path(preview['image_path']), entry['result_id'], credentials)
        return self._update(entry['result_id'], text=preview['text'], image_url=image_url,
                            image_aspect_ratio=aspect_ratio,
                            preview_url=image_url.replace(
                                '/image/upload/', f'/image/upload/{PREVIEW_TRANSFORMATION}/', 1),
                            entry_only=True)

    def _upload(self, image_path, result_id, credentials):
        params = {
            'overwrite': 'false', 'public_id': PUBLIC_ID_PREFIX + result_id,
            'timestamp': str(int(self._now())),
        }
        signed = '&'.join(f'{key}={params[key]}' for key in sorted(params))
        params['signature'] = hashlib.sha1(
            (signed + credentials['cloudinary_api_secret']).encode()).hexdigest()
        params['api_key'] = credentials['cloudinary_api_key']
        try:
            image = image_path.read_bytes()
        except OSError as error:
            raise _Rejected(f'通知合成圖無法讀取：{type(error).__name__}', final=False) from None
        body, content_type = _multipart(params, image)
        try:
            response = self._http_post(
                UPLOAD_URL.format(cloud=credentials['cloudinary_cloud_name']), body,
                {'Content-Type': content_type}, HTTP_TIMEOUT_SECONDS)
        except NetworkError as error:
            raise _Rejected(f'Cloudinary 連線失敗：{error}', final=False) from None
        if response.status == 200:
            url = _json(response.body).get('secure_url')
            if isinstance(url, str) and url.startswith('https://'):
                return url
            raise _Rejected('Cloudinary 回應缺少 HTTPS 圖片網址', final=False)
        detail = (_json(response.body).get('error') or {}).get('message') or ''
        if response.status in (401, 403):
            raise _Rejected(f'Cloudinary 憑證被拒絕（HTTP {response.status}）：{detail}', final=False)
        if response.status in (420, 429):
            raise _Rejected(f'Cloudinary 額度或速率限制（HTTP {response.status}）：{detail}', final=False)
        if 400 <= response.status < 500:
            raise _Rejected(f'Cloudinary 拒絕上傳（HTTP {response.status}）：{detail}', final=True)
        raise _Rejected(f'Cloudinary 服務錯誤（HTTP {response.status}）', final=False)

    def _push(self, entry, credentials):
        first_use = entry['key_first_used_at'] is None
        was_unknown = entry['unknown_since'] is not None
        now = self._now()
        # Write ahead: from here on LINE may hold this retry key.
        entry = self._update(entry['result_id'], entry_only=True,
                             key_first_used_at=entry['key_first_used_at'] or now,
                             unknown_since=entry['unknown_since'] or now)
        body = json.dumps({'to': credentials['line_group_id'],
                           'messages': [_text_message(entry) if entry.get('kind') == ALERT_KIND
                                        else _flex_message(entry)]},
                          ensure_ascii=False).encode('utf-8')
        try:
            response = self._http_post(PUSH_URL, body, {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': f"Bearer {credentials['line_channel_access_token']}",
                'X-Line-Retry-Key': entry['retry_key'],
            }, HTTP_TIMEOUT_SECONDS)
        except NetworkError as error:
            if error.maybe_delivered:
                raise _Rejected(f'LINE 連線中斷，送達狀態待確認：{error}', final=False) from None
            self._undo_write_ahead(entry, was_unknown=was_unknown, first_use=first_use)
            raise _Rejected(f'LINE 連線失敗：{error}', final=False) from None
        if response.status in (200, 409):
            headers = {key.lower(): value for key, value in response.headers.items()}
            return self._mark_sent(entry['result_id'], headers.get('x-line-accepted-request-id'))
        if response.status >= 500:
            raise _Rejected(f'LINE 服務錯誤（HTTP {response.status}），送達狀態待確認', final=False)
        # A 4xx answer rejects this request only; an earlier unanswered
        # attempt with the same key may still have been delivered.
        self._undo_write_ahead(entry, was_unknown=was_unknown, first_use=first_use)
        detail = _json(response.body).get('message') or ''
        if response.status == 429:
            raise _Rejected(f'LINE 額度或速率限制（HTTP 429）：{detail}', final=False)
        if response.status in (401, 403):
            raise _Rejected(f'LINE 憑證或群組權限被拒絕（HTTP {response.status}）：{detail}', final=False)
        if was_unknown:
            return self._update(entry['result_id'], status='uncertain', last_error=(
                f'LINE 拒絕重送（HTTP {response.status}）：{detail}；先前請求是否送達無法確認，'
                '請到群組確認，系統不會自動重送。'))
        raise _Rejected(f'LINE 拒絕訊息（HTTP {response.status}）：{detail}', final=True)

    def _undo_write_ahead(self, entry, *, was_unknown, first_use):
        """Forget only this attempt's possible delivery, never an earlier one."""
        if was_unknown:
            return
        self._update(entry['result_id'], entry_only=True, unknown_since=None,
                     key_first_used_at=None if first_use else entry['key_first_used_at'])

    def _mark_sent(self, result_id, accepted_request_id):
        return self._update(result_id, status='sent', sent_at=self._now(), last_error=None,
                            unknown_since=None, accepted_request_id=accepted_request_id)

    def _retry_later(self, result_id, reason, *, final):
        with _STATE_LOCK:
            attempts = self._read()['entries'][result_id]['attempts'] + 1
        delay = min(BACKOFF_SECONDS * 2 ** (attempts - 1), BACKOFF_MAX_SECONDS)
        return self._update(result_id, status='failed' if final else 'pending',
                            attempts=attempts, last_error=reason,
                            next_attempt_at=self._now() + delay)

    def _update(self, result_id, *, entry_only=False, **changes):
        with _STATE_LOCK:
            state = self._read()
            entry = state['entries'][result_id]
            entry.update(changes)
            self._write(state)
        if entry_only:
            return entry
        return {'result_id': result_id, 'status': entry['status'],
                'reason': entry['last_error']}

    # -- storage -------------------------------------------------------------

    def _credentials(self):
        path = self.credentials_path
        try:
            values = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'ok': False, 'reason': 'line_credentials_missing'}
        except OSError as error:
            logger.warning('LINE credentials unreadable: %s', type(error).__name__)
            return {'ok': False, 'reason': 'line_credentials_unreadable'}
        except ValueError:
            return {'ok': False, 'reason': 'line_credentials_invalid'}
        if not isinstance(values, dict):
            return {'ok': False, 'reason': 'line_credentials_invalid'}
        missing = [field for field in CREDENTIAL_FIELDS
                   if not isinstance(values.get(field), str) or not values[field].strip()]
        if missing:
            return {'ok': False, 'reason': 'line_credentials_incomplete', 'missing_fields': missing}
        return {'ok': True, 'values': {field: values[field].strip() for field in CREDENTIAL_FIELDS}}

    def _read(self):
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'enabled': dict.fromkeys(MODES, False), 'entries': {}}
        if (not isinstance(state, dict) or state.get('version') != 1
                or not isinstance(state.get('entries'), dict)):
            raise ValueError(f'通知待送檔格式不正確：{self.state_path}')
        state['enabled'] = {mode: bool((state.get('enabled') or {}).get(mode)) for mode in MODES}
        return state

    def _write(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        dataset_store.atomic_write_json(self.state_path, state)


def _text_message(entry):
    return {'type': 'text', 'text': entry['text'].strip()}


def _flex_message(entry):
    """One bubble: the composite on top (tap for full size), the text below."""
    lines = entry['text'].strip().split('\n')
    alt_text = ' '.join(line for line in lines if not line.startswith('偵測時間：'))
    return {
        'type': 'flex', 'altText': alt_text[:ALT_TEXT_LIMIT],
        'contents': {
            'type': 'bubble', 'size': 'giga',
            'hero': {
                'type': 'image', 'url': entry['preview_url'], 'size': 'full',
                'aspectMode': 'fit',
                'aspectRatio': entry.get('image_aspect_ratio') or FALLBACK_ASPECT_RATIO,
                'action': {'type': 'uri', 'label': '查看原圖', 'uri': entry['image_url']},
            },
            'body': {
                'type': 'box', 'layout': 'vertical', 'spacing': 'sm',
                'contents': [
                    {'type': 'text', 'text': line, 'wrap': True,
                     **({'weight': 'bold', 'size': 'md'} if index == 0 else {'size': 'sm'})}
                    for index, line in enumerate(lines)
                ],
            },
        },
    }


def _json(body):
    try:
        value = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _multipart(fields, image):
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="notification.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
        + image + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'
