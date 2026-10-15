"""Read-only Dashboard assistant backed by the Gemini REST API.

Gemini only chooses tools and words the answer. Numbers and charts come from
``detection_stats`` in Django and reach the browser without passing through
the model; agricultural knowledge comes from a separate Google Search grounded
request whose source links Django attaches to the reply. The backend always
splits search into its own request, so it works whether or not the selected
model accepts search grounding and function calling together.

Nothing here touches the state machine, classification, motor commands or
files other than the per-day request counter and the optional launcher icon. Expected failures return
``{'ok': False, 'reason': ...}`` (``assistant_offline``,
``assistant_not_configured``, ``assistant_quota_exceeded`` ...) so the chat
box can explain them while capture, detection and sorting keep running.

The API key and model live only in ``assistant_credentials.json`` and never
enter responses, logs or result files. Conversations are not stored.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from . import detection_stats


API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
HTTP_TIMEOUT_SECONDS = 60
DEFAULT_DAILY_REQUEST_LIMIT = 200
MAX_TOOL_ROUNDS = 4
MAX_MESSAGES = 12
MAX_MESSAGE_CHARS = 2000
MAX_SOURCES = 8
DOC_NAMES = ('快速開始.md', '環境部署說明文件.md')
# Launcher icon: stored at 2x the 64 px on-screen size for high-DPI screens.
ICON_SIZE = 128
ICON_MAX_BYTES = 10 * 1024 * 1024
ICON_MAX_PIXELS = 40_000_000
MESSAGES = {
    'assistant_not_configured': '助手尚未設定：請依部署說明放入 assistant_credentials.json。',
    'assistant_offline': '無法連線到 Gemini，請確認這台電腦可以上網；拍攝、檢測與分槽不受影響。',
    'assistant_quota_exceeded': '已達今日助手請求上限或 Gemini 額度限制，請明天再試或調整上限。',
    'assistant_rejected': 'Gemini 拒絕這次請求，請確認 API key、模型名稱與帳單設定。',
    'assistant_billing_required': 'Gemini 帳單餘額不足：請到 Google AI Studio 的帳單頁儲值（新帳戶須先預付）後再試。',
    'assistant_model_unavailable': '設定的 Gemini 模型無法使用：請在 assistant_credentials.json 改用 AI Studio 模型清單中的模型代碼。',
    'assistant_service_error': 'Gemini 服務暫時異常，請稍後再試。',
    'assistant_invalid_response': 'Gemini 回應格式無法解讀，請稍後再試。',
    'assistant_tool_rounds_exceeded': '這個問題需要太多步查詢，請把問題拆小一點再問。',
}
STATS_TOOL = {
    'name': 'query_detection_stats',
    'description': (
        '查詢檢測分區的百香果統計：四級分布（上等、中等、下等、加工）、六種瑕疵'
        '（色澤異常、皺褶、擦傷、炭疽病、蟲咬、畫圖蟲）出現顆數與佔已判級果實比例，'
        '放棄本輪與待人工處理另列。數字與圖表由系統計算並直接顯示給使用者。'
        '問到特定等級（例如加工果）的瑕疵時，必須傳 grades，瑕疵比例的分母才會是該等級果實數。'
    ),
    'parameters': {
        'type': 'object',
        'properties': {
            'start_date': {'type': 'string', 'description': '起始日期 YYYY-MM-DD（含），不限則省略'},
            'end_date': {'type': 'string', 'description': '結束日期 YYYY-MM-DD（含），不限則省略'},
            'bucket': {'type': 'string', 'enum': list(detection_stats.BUCKETS),
                       'description': '時間級距；none 表示整段合計，day/week/month 產生趨勢'},
            'group_by': {'type': 'string', 'enum': list(detection_stats.GROUP_BY),
                         'description': 'grade 看四級分布，defect 看瑕疵比例'},
            'include_photo_only': {'type': 'boolean',
                                   'description': '是否納入純拍攝測試；使用者沒有要求時為 false'},
            'grades': {'type': 'array',
                       'items': {'type': 'string', 'enum': list(detection_stats.GRADE_ORDER)},
                       'description': '只統計這些等級的已判級果實（可複選）；不限等級則省略'},
        },
        'required': ['group_by'],
    },
}
SEARCH_TOOL = {
    'name': 'search_agriculture_knowledge',
    'description': '以 Google 搜尋查詢百香果病蟲害成因、防治、栽培與採收等一般農業知識。',
    'parameters': {
        'type': 'object',
        'properties': {'question': {'type': 'string', 'description': '要搜尋的問題（繁體中文）'}},
        'required': ['question'],
    },
}
SYSTEM_RULES = """你是百香果 IoT 分級系統 Dashboard 的唯讀助手，一律以繁體中文回答，語氣簡潔、適合農夫閱讀。

規則：
1. 檢測統計一律呼叫 query_detection_stats，不得自行計算、推估或編造數字；回答中引用的數字必須來自工具結果。圖表會由系統依工具結果自動顯示，不需要在文字中重畫表格。
2. 統計預設只計使用硬體的輪次；使用者明確要求時才納入純拍攝測試。放棄本輪與待人工處理不屬於四級分布，需要時另外說明。
3. 病蟲害、防治、栽培等農業知識呼叫 search_agriculture_knowledge，並根據結果回答；系統會自動附上來源網址。涉及用藥時，提醒以植物保護資訊系統與在地農業改良場的建議為準。
4. 部署、啟動與操作問題依下方文件回答，文件沒有寫的不要猜測，請使用者詢問專案擁有者。
5. 你只能查詢，不能開始運轉、切換模式、分類、刪除資料或控制馬達；使用者要求這些操作時，說明要在 Dashboard 上自行操作。
6. 檢測比例只反映模型觀察，不代表病因判斷或防治效果。
"""
HTTP_REASONS = {
    402: 'assistant_billing_required',
    404: 'assistant_model_unavailable',
    429: 'assistant_quota_exceeded',
}
ALERT_TIMEOUT_SECONDS = 20
ALERT_RULES = """你替百香果分級系統的趨勢提醒寫一段給農夫看的說明與建議，繁體中文，兩句以內、一百字以內。
輸入是系統已判定要發送的提醒事實（JSON）。數據會由系統另外列出，所以你的文字中不得出現任何阿拉伯數字或百分比，也不要重述數據。
依 direction 給方向性建議；若有 also_rising，可提醒一併留意。不得宣稱病因或保證防治效果；涉及用藥時提醒以植物保護資訊系統與在地農業改良場建議為準。只輸出說明文字本身。"""
SEARCH_UNAVAILABLE = {
    'assistant_offline': '農業知識搜尋暫時無法連線，這次回答沒有使用網路搜尋。',
    'assistant_service_error': '農業知識搜尋暫時異常，這次回答沒有使用網路搜尋。',
    'default': '農業知識搜尋需要 Gemini 付費層級（免費層級不提供 Google 搜尋），這次回答沒有使用網路搜尋。',
}
SEARCH_RULES = '以繁體中文、根據搜尋結果簡潔回答百香果相關農業問題；不確定時直說。涉及用藥時提醒以植物保護資訊系統與在地農業改良場建議為準。'

_USAGE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class _Failure(Exception):
    def __init__(self, reason: str, detail: str = ''):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def http_post_json(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    """POST JSON; network failures raise ``_Failure('assistant_offline')``."""
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json; charset=utf-8', **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as error:
        raise _Failure('assistant_offline', type(error).__name__) from None
    try:
        value = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        value = {}
    return status, value if isinstance(value, dict) else {}


class Assistant:
    def __init__(self, credentials_path: Path, usage_path: Path, detection_root: Path,
                 docs_root: Path, *, http_post: Callable = http_post_json,
                 today: Callable[[], date] = date.today):
        self.credentials_path = Path(credentials_path)
        self.usage_path = Path(usage_path)
        self.detection_root = Path(detection_root)
        self.docs_root = Path(docs_root)
        self._http_post = http_post
        self._today = today

    def ask(self, messages: list) -> dict:
        """Answer the latest user message; invalid input raises ``ValueError``."""
        contents = _validated_contents(messages)
        credentials = self._credentials()
        if credentials is None:
            return _failure('assistant_not_configured')
        charts, sources, notices = [], [], []
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                candidate = self._generate(credentials, {
                    'systemInstruction': {'parts': [{'text': self._system_instruction()}]},
                    'contents': contents,
                    'tools': [{'functionDeclarations': [STATS_TOOL, SEARCH_TOOL]}],
                })
                content = candidate.get('content') or {}
                parts = content.get('parts') or []
                calls = [part['functionCall'] for part in parts
                         if isinstance(part.get('functionCall'), dict)]
                if not calls:
                    reply = _text(parts)
                    if not reply:
                        raise _Failure('assistant_invalid_response', 'empty reply')
                    return {'ok': True, 'reason': None, 'reply': reply, 'charts': charts,
                            'sources': _unique_sources(sources), 'notices': list(dict.fromkeys(notices)),
                            'usage': self._usage(credentials)}
                # Keep the model turn verbatim; newer models need its thought signatures.
                contents.append({'role': 'model', 'parts': parts})
                contents.append({'role': 'user', 'parts': [
                    {'functionResponse': {'name': call.get('name'), 'response': self._run_tool(
                        call, credentials, charts, sources, notices)}}
                    for call in calls
                ]})
            raise _Failure('assistant_tool_rounds_exceeded')
        except _Failure as failure:
            return _failure(failure.reason, usage=self._usage(credentials))

    def write_alert_advice(self, facts: dict) -> dict:
        """Word a triggered trend alert; the caller decides sending and numbers.

        Returns ``{'ok': True, 'text': ...}`` or a structured failure; the
        trend module rejects any text containing digits and falls back to its
        fixed template.
        """
        credentials = self._credentials()
        if credentials is None:
            return {'ok': False, 'reason': 'assistant_not_configured'}
        try:
            candidate = self._generate(credentials, {
                'systemInstruction': {'parts': [{'text': ALERT_RULES}]},
                'contents': [{'role': 'user', 'parts': [
                    {'text': json.dumps(facts, ensure_ascii=False)}]}],
            }, timeout=ALERT_TIMEOUT_SECONDS)
        except _Failure as failure:
            return {'ok': False, 'reason': failure.reason}
        text = _text((candidate.get('content') or {}).get('parts') or [])
        if not text:
            return {'ok': False, 'reason': 'assistant_invalid_response'}
        return {'ok': True, 'reason': None, 'text': text}

    # -- tools ---------------------------------------------------------------

    def _run_tool(self, call, credentials, charts, sources, notices):
        name = call.get('name')
        args = call.get('args') if isinstance(call.get('args'), dict) else {}
        if name == STATS_TOOL['name']:
            try:
                stats = detection_stats.query(self.detection_root, **{
                    key: args[key] for key in
                    ('start_date', 'end_date', 'bucket', 'group_by', 'include_photo_only', 'grades')
                    if key in args
                })
            except (TypeError, ValueError) as error:
                return {'ok': False, 'error': str(error)}
            charts.append(stats['chart'])
            return {key: stats[key] for key in
                    ('ok', 'filters', 'totals', 'grades', 'sources', 'defects', 'series', 'skipped')}
        if name == SEARCH_TOOL['name']:
            question = args.get('question')
            if not isinstance(question, str) or not question.strip():
                return {'ok': False, 'error': 'question 不得為空'}
            try:
                candidate = self._generate(credentials, {
                    'systemInstruction': {'parts': [{'text': SEARCH_RULES}]},
                    'contents': [{'role': 'user', 'parts': [{'text': question[:MAX_MESSAGE_CHARS]}]}],
                    'tools': [{'google_search': {}}],
                })
            except _Failure as failure:
                # The daily limit ends the whole turn; any other search failure
                # (free tier has no grounding) only loses this tool's answer.
                if failure.reason == 'assistant_quota_exceeded' and failure.detail == 'daily limit':
                    raise
                notices.append(SEARCH_UNAVAILABLE.get(failure.reason, SEARCH_UNAVAILABLE['default']))
                return {'ok': False, 'error': 'search_unavailable', 'message': notices[-1]}
            found = _grounding_sources(candidate)
            sources.extend(found)
            return {'ok': True, 'answer': _text((candidate.get('content') or {}).get('parts') or []),
                    'sources': found}
        return {'ok': False, 'error': f'未知的工具：{name}'}

    # -- Gemini ----------------------------------------------------------------

    def _generate(self, credentials, payload, *, timeout=HTTP_TIMEOUT_SECONDS):
        self._count_request(credentials['daily_request_limit'])
        status, body = self._http_post(
            API_URL.format(model=credentials['model']), payload,
            {'x-goog-api-key': credentials['gemini_api_key']}, timeout,
        )
        if status != 200:
            reason = HTTP_REASONS.get(status) or (
                'assistant_rejected' if 400 <= status < 500 else 'assistant_service_error')
            # Gemini's own explanation helps the operator; the key never appears in it.
            message = str((body.get('error') or {}).get('message') or '')
            logger.warning('Gemini HTTP %s (%s): %s', status, reason,
                           message.replace(credentials['gemini_api_key'], '***')[:500])
            raise _Failure(reason, f'HTTP {status}')
        candidates = body.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise _Failure('assistant_invalid_response', 'no candidates')
        return candidates[0]

    def _system_instruction(self):
        docs = []
        for name in DOC_NAMES:
            try:
                docs.append(f'===== {name} =====\n{(self.docs_root / name).read_text(encoding="utf-8")}')
            except OSError:
                docs.append(f'===== {name} =====\n（本機找不到此文件）')
        return '\n\n'.join([SYSTEM_RULES, f'今天日期：{self._today().isoformat()}',
                            '以下是本機版本的部署文件：', *docs])

    # -- credentials and daily limit -------------------------------------------

    def _credentials(self):
        try:
            values = json.loads(self.credentials_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(values, dict):
            return None
        key, model = values.get('gemini_api_key'), values.get('model')
        if not all(isinstance(value, str) and value.strip() for value in (key, model)):
            return None
        limit = values.get('daily_request_limit', DEFAULT_DAILY_REQUEST_LIMIT)
        if type(limit) is not int or limit < 1:
            return None
        return {'gemini_api_key': key.strip(), 'model': model.strip(), 'daily_request_limit': limit}

    def _count_request(self, limit):
        """Reserve one Gemini request for today or fail before sending it."""
        with _USAGE_LOCK:
            usage = self._read_usage()
            if usage['count'] >= limit:
                raise _Failure('assistant_quota_exceeded', 'daily limit')
            usage['count'] += 1
            staging = self.usage_path.with_name(f'{self.usage_path.name}.tmp')
            try:
                self.usage_path.parent.mkdir(parents=True, exist_ok=True)
                staging.write_text(json.dumps(usage), encoding='utf-8')
                os.replace(staging, self.usage_path)
            except OSError:
                staging.unlink(missing_ok=True)
                raise

    def _read_usage(self):
        today = self._today().isoformat()
        try:
            usage = json.loads(self.usage_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            usage = None
        except (OSError, ValueError):
            usage = None
        if (not isinstance(usage, dict) or usage.get('date') != today
                or type(usage.get('count')) is not int):
            return {'date': today, 'count': 0}
        return usage

    def _usage(self, credentials):
        with _USAGE_LOCK:
            used = self._read_usage()['count']
        return {'used': used, 'limit': credentials['daily_request_limit']}


def save_launcher_icon(data: bytes, path: Path) -> dict:
    """Store an uploaded picture as the square launcher icon.

    The image is EXIF-rotated, center-cropped to a square and resized to
    ``ICON_SIZE`` before an atomic PNG write; the original upload is not kept.
    Unreadable or oversized pictures return a structured reason.
    """
    if not data:
        return {'ok': False, 'reason': 'icon_empty'}
    if len(data) > ICON_MAX_BYTES:
        return {'ok': False, 'reason': 'icon_too_large'}
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > ICON_MAX_PIXELS:
                return {'ok': False, 'reason': 'icon_too_large'}
            with ImageOps.exif_transpose(source) as oriented:
                icon = ImageOps.fit(oriented.convert('RGBA'), (ICON_SIZE, ICON_SIZE),
                                    method=Image.Resampling.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError):
        return {'ok': False, 'reason': 'icon_unreadable'}
    path = Path(path)
    staging = path.with_name(f'{path.name}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        icon.save(staging, format='PNG', optimize=True)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
        icon.close()
    return {'ok': True, 'reason': None}


def _validated_contents(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages 必須是非空陣列。')
    if len(messages) > MAX_MESSAGES:
        messages = messages[-MAX_MESSAGES:]
    contents = []
    for message in messages:
        if (not isinstance(message, dict) or message.get('role') not in ('user', 'model')
                or not isinstance(message.get('text'), str) or not message['text'].strip()):
            raise ValueError('每則訊息必須含 role（user／model）與非空 text。')
        if len(message['text']) > MAX_MESSAGE_CHARS:
            raise ValueError(f'單則訊息不得超過 {MAX_MESSAGE_CHARS} 字。')
        contents.append({'role': message['role'], 'parts': [{'text': message['text']}]})
    if contents[-1]['role'] != 'user':
        raise ValueError('最後一則訊息必須是使用者提問。')
    return contents


def _text(parts):
    return ''.join(part['text'] for part in parts
                   if isinstance(part, dict) and isinstance(part.get('text'), str)
                   and not part.get('thought')).strip()


def _grounding_sources(candidate):
    chunks = (candidate.get('groundingMetadata') or {}).get('groundingChunks') or []
    sources = []
    for chunk in chunks:
        web = chunk.get('web') if isinstance(chunk, dict) else None
        if isinstance(web, dict) and isinstance(web.get('uri'), str) and web['uri'].startswith('https://'):
            sources.append({'title': str(web.get('title') or web['uri']), 'uri': web['uri']})
    return sources


def _unique_sources(sources):
    seen = {}
    for source in sources:
        seen.setdefault(source['uri'], source)
    return list(seen.values())[:MAX_SOURCES]


def _failure(reason, *, usage=None):
    return {'ok': False, 'reason': reason, 'message': MESSAGES[reason], 'usage': usage}
