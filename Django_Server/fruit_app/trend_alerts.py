"""Rule-based defect trend alerts over the detection dataset.

After a hardware result is saved, ``TrendAlerts.check`` compares each defect's
share of graded hardware fruits in the latest window with a fixed threshold and
with the previous window of the same length. Matching categories outside their
cooldown produce one fixed-template alert, shown on the Dashboard and queued to
LINE through the notification outbox. No LLM is involved, so alerts work
offline and are reproducible from the same data and settings.

Settings, cooldown timestamps and recent alerts live in one JSON file under
``runtime_config``; the switch is independent of processing-fruit
notifications and defaults to off.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import detection_stats
from .notification import OBSERVATION_NAMES


DEFAULT_SETTINGS = {
    'enabled': False,
    'window_days': 7,
    'min_samples': 20,
    'ratio_threshold': 0.3,
    'rise_threshold': 0.1,
    'cooldown_hours': 24,
}
# (type, minimum, maximum) for every adjustable setting.
SETTING_LIMITS = {
    'window_days': (int, 1, 60),
    'min_samples': (int, 1, 10_000),
    'ratio_threshold': (float, 0.01, 1.0),
    'rise_threshold': (float, 0.01, 1.0),
    'cooldown_hours': (float, 0.0, 720.0),
}
ADVICE = {
    'anthracnose': '須注意防疫',
    'insect_bite': '須注意蟲害防治',
    'insect_track': '須注意蟲害防治',
    'abrasion': '請留意採收與搬運作業',
    'bad_color': '請留意栽培與採收時機',
    'wrinkle': '請留意採收時機與貯藏條件',
}
RECENT_LIMIT = 20

_LOCK = threading.RLock()


def evaluate(current: dict, previous: dict, settings: dict) -> list[dict]:
    """Return defects whose current-window share breaks a rule.

    ``current`` and ``previous`` are ``detection_stats.summarize`` outputs.
    The current window needs ``min_samples`` graded fruits; the rise rule also
    needs the previous window to reach it, so a thin baseline never triggers.
    """
    graded = current['totals']['graded']
    if graded < settings['min_samples']:
        return []
    previous_graded = previous['totals']['graded']
    baseline = previous_graded >= settings['min_samples']
    previous_by_key = {item['key']: item for item in previous['defects']}
    triggers = []
    for item in current['defects']:
        before = previous_by_key[item['key']]
        reasons = []
        if item['count'] and item['ratio'] >= settings['ratio_threshold']:
            reasons.append('threshold')
        if baseline and item['count'] and (
                item['ratio'] - before['ratio'] >= settings['rise_threshold']):
            reasons.append('rise')
        if not reasons:
            continue
        triggers.append({
            'key': item['key'], 'label': item['label'], 'reasons': reasons,
            'current': {'graded': graded, 'count': item['count'], 'ratio': item['ratio']},
            'previous': {'graded': previous_graded, 'count': before['count'],
                         'ratio': before['ratio'], 'sufficient': baseline},
        })
    return triggers


def render_text(trigger: dict, settings: dict) -> str:
    """Fixed farmer-facing template; states data, never causes or remedies."""
    days = settings['window_days']
    label = trigger['label']
    current = trigger['current']
    previous = trigger['previous']
    previous_line = (
        f"前 {days} 天：已判級 {previous['graded']} 顆，{label} {previous['count']} 顆"
        f"（{_percent(previous['ratio'])}）"
        if previous['sufficient'] else
        f"前 {days} 天：已判級 {previous['graded']} 顆，樣本不足不比較"
    )
    basis = []
    if 'threshold' in trigger['reasons']:
        basis.append(f"比例達 {_percent(settings['ratio_threshold'])} 門檻")
    if 'rise' in trigger['reasons']:
        basis.append(f"比前 {days} 天上升 {settings['rise_threshold'] * 100:.1f} 個百分點以上")
    return '\n'.join([
        '|趨勢提醒|',
        f"{label}數量近來有上升趨勢，{ADVICE[trigger['key']]}。",
        f"最近 {days} 天：已判級 {current['graded']} 顆，{label} {current['count']} 顆"
        f"（{_percent(current['ratio'])}）",
        previous_line,
        f"依據：{'、'.join(basis)}",
        '說明：比例來自檢測模型觀察（只計使用硬體的果實），僅供參考，不代表病因判斷。',
    ]) + '\n'


class TrendAlerts:
    def __init__(self, state_path: Path, detection_root: Path, outbox, *,
                 now: Callable[[], datetime] = datetime.now):
        self.state_path = Path(state_path)
        self.detection_root = Path(detection_root)
        self.outbox = outbox
        self._now = now

    def update_settings(self, changes: dict) -> dict:
        """Validate and persist Dashboard changes; invalid values raise."""
        if not isinstance(changes, dict) or not changes:
            raise ValueError('趨勢提醒設定必須是非空物件。')
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"未知的趨勢提醒設定：{'、'.join(sorted(unknown))}")
        validated = {key: _validated(key, value) for key, value in changes.items()}
        with _LOCK:
            state = self._read()
            state['settings'].update(validated)
            self._write(state)
            return dict(state['settings'])

    def check(self) -> dict:
        """Evaluate the rules once and queue new alerts; switched off does nothing."""
        with _LOCK:
            state = self._read()
            settings = state['settings']
            if not settings['enabled']:
                return {'ok': True, 'alerts': [], 'reason': 'trend_alerts_disabled'}
            now = self._now()
            window = timedelta(days=settings['window_days'])
            current = self._summary(now - window, None)
            previous = self._summary(now - 2 * window, now - window)
            cooldown = timedelta(hours=settings['cooldown_hours'])
            created = []
            for trigger in evaluate(current, previous, settings):
                last = state['last_alerted'].get(trigger['key'])
                if last and now - datetime.fromisoformat(last) < cooldown:
                    continue
                alert = {
                    'id': f'trend_{uuid.uuid4().hex}', 'key': trigger['key'],
                    'label': trigger['label'], 'created_at': now.isoformat(timespec='seconds'),
                    'text': render_text(trigger, settings),
                    'current': trigger['current'], 'previous': trigger['previous'],
                }
                state['last_alerted'][trigger['key']] = alert['created_at']
                state['recent'] = ([alert] + state['recent'])[:RECENT_LIMIT]
                created.append(alert)
            if created:
                self._write(state)
        # Persisted first: a LINE queue failure never repeats or loses the alert.
        queue_errors = []
        for alert in created:
            try:
                self.outbox.enqueue_alert(alert['id'], alert['text'])
            except (OSError, ValueError) as error:
                queue_errors.append(f"{alert['label']}：{error}")
        return {'ok': not queue_errors, 'alerts': created,
                'reason': 'line_queue_failed' if queue_errors else None,
                'errors': queue_errors}

    def status(self) -> dict:
        """Dashboard view: settings, recent alerts and their LINE delivery."""
        with _LOCK:
            state = self._read()
        delivery = self.outbox.entry_statuses(alert['id'] for alert in state['recent'])
        return {
            'settings': dict(state['settings']),
            'labels': dict(OBSERVATION_NAMES),
            'line': self.outbox.configuration(),
            'recent': [
                {key: alert[key] for key in ('id', 'key', 'label', 'created_at', 'text')}
                | {'delivery': delivery.get(alert['id'])}
                for alert in state['recent']
            ],
        }

    def _summary(self, start, end):
        loaded = detection_stats.load_records(self.detection_root, start=start, end=end)
        return detection_stats.summarize(loaded['records'])

    def _read(self):
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'settings': dict(DEFAULT_SETTINGS),
                    'last_alerted': {}, 'recent': []}
        if (not isinstance(state, dict) or state.get('version') != 1
                or not isinstance(state.get('settings'), dict)
                or not isinstance(state.get('last_alerted'), dict)
                or not isinstance(state.get('recent'), list)):
            raise ValueError(f'趨勢提醒設定檔格式不正確：{self.state_path}')
        state['settings'] = {
            key: _validated(key, state['settings'].get(key, default))
            for key, default in DEFAULT_SETTINGS.items()
        }
        return state

    def _write(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.state_path.with_name(f'{self.state_path.name}.tmp')
        try:
            staging.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
            os.replace(staging, self.state_path)
        except OSError:
            staging.unlink(missing_ok=True)
            raise


def _validated(key, value):
    if key == 'enabled':
        if type(value) is not bool:
            raise ValueError('趨勢提醒開關必須是 true 或 false。')
        return value
    kind, low, high = SETTING_LIMITS[key]
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f'{key} 必須是數字。')
    if kind is int and value != int(value):
        raise ValueError(f'{key} 必須是整數。')
    if not low <= value <= high:
        raise ValueError(f'{key} 必須介於 {low} 與 {high} 之間。')
    return kind(value)


def _percent(ratio):
    return f'{ratio * 100:.1f}%'
