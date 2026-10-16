"""Rule-based trend alerts over the detection dataset (ADR-0022).

Three layers, only the last one may use an LLM:

1. Detection: after any graded result is saved, the six observation
   categories and the processing-fruit share run through the same generic
   rules on every graded fruit in the detection partition: share over a
   threshold in the latest
   window, a rise against the previous window of the same length, and a
   same-day spike against the window before today.
2. Severity: every category has its own threshold, rise, minimum sample and
   advice direction; adding a model class only adds a ``DEFAULT_CATEGORIES``
   entry.
3. Wording: a triggered alert's structured facts may be handed to a writer
   (Gemini) for a short explanation. The writer never decides whether to send
   and never supplies numbers: every number is written by Django, and writer
   text containing a digit, or any writer failure, falls back to the fixed
   template. The text is frozen when the alert is queued.

Settings, cooldown timestamps and recent alerts live in one JSON file under
``runtime_config``; the switch is independent of processing-fruit
notifications and defaults to off.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import detection_stats
from .notification import OBSERVATION_NAMES


PROCESSING = 'processing'
CATEGORY_NAMES = {**OBSERVATION_NAMES, PROCESSING: '加工果'}
DIRECTIONS = {
    'disease': '須注意病害防疫',
    'pest': '須注意蟲害防治',
    'handling': '請檢查採收與搬運作業',
    'cultivation': '請留意栽培管理與採收時機',
    'quality': '請檢視近期整體品質與田間狀況',
}
DEFAULT_CATEGORIES = {
    'bad_color': {'threshold': 0.4, 'rise': 0.15, 'min_samples': 20, 'direction': 'cultivation'},
    'wrinkle': {'threshold': 0.4, 'rise': 0.15, 'min_samples': 20, 'direction': 'cultivation'},
    'abrasion': {'threshold': 0.3, 'rise': 0.1, 'min_samples': 20, 'direction': 'handling'},
    'anthracnose': {'threshold': 0.2, 'rise': 0.1, 'min_samples': 20, 'direction': 'disease'},
    'insect_bite': {'threshold': 0.3, 'rise': 0.1, 'min_samples': 20, 'direction': 'pest'},
    'insect_track': {'threshold': 0.3, 'rise': 0.1, 'min_samples': 20, 'direction': 'pest'},
    PROCESSING: {'threshold': 0.4, 'rise': 0.15, 'min_samples': 20, 'direction': 'quality'},
}
DEFAULT_SETTINGS = {
    'enabled': False,
    'window_days': 7,
    'cooldown_hours': 24,
    'spike_factor': 2.0,
    'spike_min_samples': 10,
}
# (type, minimum, maximum) for every numeric setting.
SETTING_LIMITS = {
    'window_days': (int, 1, 60),
    'cooldown_hours': (float, 0.0, 720.0),
    'spike_factor': (float, 1.1, 20.0),
    'spike_min_samples': (int, 1, 10_000),
    'threshold': (float, 0.01, 1.0),
    'rise': (float, 0.01, 1.0),
    'min_samples': (int, 1, 10_000),
}
REASON_TEXT = {'threshold': '比例達門檻', 'rise': '比前期上升', 'spike': '今日明顯偏高'}
ADVICE_MAX_CHARS = 200
DIGITS = re.compile(r'[0-9０-９%％]')
RECENT_LIMIT = 20

_LOCK = threading.RLock()


def category_shares(summary: dict) -> dict:
    """Count and graded denominator per category from a ``summarize`` output."""
    graded = summary['totals']['graded']
    shares = {item['key']: {'count': item['count'], 'graded': graded, 'ratio': item['ratio']}
              for item in summary['defects']}
    processing = next(item for item in summary['grades'] if item['label'] == '加工')
    shares[PROCESSING] = {'count': processing['count'], 'graded': graded,
                          'ratio': processing['ratio']}
    return shares


def evaluate(windows: dict, settings: dict) -> list[dict]:
    """Apply the generic rules to every category; return the triggered ones.

    ``windows`` maps ``current``, ``previous``, ``today`` and ``history`` (the
    window before today) to ``category_shares`` outputs. Each comparison needs
    both sides to reach the category's ``min_samples`` (``spike_min_samples``
    for today), so a thin baseline never triggers.
    """
    current, previous = windows['current'], windows['previous']
    today, history = windows['today'], windows['history']
    triggers = []
    for key, params in settings['categories'].items():
        now, before = current[key], previous[key]
        enough = now['graded'] >= params['min_samples']
        baseline = before['graded'] >= params['min_samples']
        reasons = []
        if enough and now['count'] and now['ratio'] >= params['threshold']:
            reasons.append('threshold')
        if enough and baseline and now['count'] and now['ratio'] - before['ratio'] >= params['rise']:
            reasons.append('rise')
        day, past = today[key], history[key]
        if (day['graded'] >= settings['spike_min_samples'] and past['graded'] >= params['min_samples']
                and day['count'] and day['ratio'] - past['ratio'] >= params['rise']
                and day['ratio'] >= past['ratio'] * settings['spike_factor']):
            reasons.append('spike')
        if reasons:
            triggers.append({
                'key': key, 'label': CATEGORY_NAMES[key], 'reasons': reasons,
                'direction': params['direction'],
                **{name: dict(windows[name][key]) for name in windows},
                'baseline_sufficient': baseline,
            })
    rising = [key for key, params in settings['categories'].items()
              if previous[key]['graded'] >= params['min_samples']
              and current[key]['graded'] >= params['min_samples']
              and current[key]['ratio'] - previous[key]['ratio'] >= params['rise']]
    for trigger in triggers:
        trigger['also_rising'] = [CATEGORY_NAMES[key] for key in rising if key != trigger['key']]
    return triggers


def alert_facts(triggers: list[dict], settings: dict) -> dict:
    """Structured facts handed to the writer; it words them, Django sends them."""
    triggered = {trigger['label'] for trigger in triggers}
    return {
        'window_days': settings['window_days'],
        'alerts': [{
            'category': trigger['label'], 'direction': DIRECTIONS[trigger['direction']],
            'reasons': [REASON_TEXT[reason] for reason in trigger['reasons']],
            'current': trigger['current'], 'previous': trigger['previous'],
            'today': trigger['today'], 'history': trigger['history'],
        } for trigger in triggers],
        'also_rising': _also_rising(triggers, triggered),
    }


def template_advice(triggers: list[dict]) -> str:
    """One sentence per direction, naming the categories that share it."""
    by_direction: dict[str, list[str]] = {}
    for trigger in triggers:
        by_direction.setdefault(trigger['direction'], []).append(trigger['label'])
    return '；'.join(f"{'、'.join(labels)}比例近來偏高，{DIRECTIONS[direction]}"
                    for direction, labels in by_direction.items()) + '。'


def accepted_advice(text) -> str | None:
    """Writer text is used only when it is short and contains no number."""
    if not isinstance(text, str):
        return None
    text = ' '.join(text.split())
    if not text or len(text) > ADVICE_MAX_CHARS or DIGITS.search(text):
        return None
    return text


def render_text(triggers: list[dict], settings: dict, advice: str, *, written_by_ai: bool) -> str:
    """One message for every category triggered by the same check.

    Every number below comes from Django; the shared window totals appear
    once and each category gets one line with the rule that triggered it.
    """
    days = settings['window_days']
    first = triggers[0]
    if len(triggers) == 1:
        title = f"{first['label']}{'今日明顯偏高' if first['reasons'] == ['spike'] else '近來有上升趨勢'}"
    else:
        title = f"多項瑕疵近來偏高：{'、'.join(trigger['label'] for trigger in triggers)}"
    previous = f"前 {days} 天：已判級 {first['previous']['graded']} 顆"
    if not any(trigger['baseline_sufficient'] for trigger in triggers):
        previous += '，樣本不足不比較'
    lines = ['|趨勢提醒|', title, f"最近 {days} 天：已判級 {first['current']['graded']} 顆", previous]
    lines += [_category_line(trigger, settings) for trigger in triggers]
    also = _also_rising(triggers, {trigger['label'] for trigger in triggers})
    if also:
        lines.append(f"同時上升：{'、'.join(also)}")
    lines.append(f"說明{'（AI 依上列數據撰寫）' if written_by_ai else ''}：{advice}")
    lines.append('比例來自檢測模型觀察，僅供參考，不代表病因判斷或防治效果。')
    return '\n'.join(lines) + '\n'


def _category_line(trigger, settings):
    days = settings['window_days']
    params = settings['categories'][trigger['key']]
    current = trigger['current']
    basis = {
        'threshold': f"達 {_percent(params['threshold'])} 門檻",
        'rise': f"前 {days} 天 {_percent(trigger['previous']['ratio'])}，"
                f"上升 {params['rise'] * 100:.1f} 個百分點以上",
        'spike': f"今日 {trigger['today']['count']}／{trigger['today']['graded']} 顆"
                 f"（{_percent(trigger['today']['ratio'])}），"
                 f"達今日前 {days} 天（{_percent(trigger['history']['ratio'])}）的 "
                 f"{settings['spike_factor']:g} 倍以上",
    }
    return (f"・{trigger['label']} {current['count']} 顆（{_percent(current['ratio'])}）："
            + '；'.join(basis[reason] for reason in trigger['reasons']))


def _also_rising(triggers, triggered_labels):
    """Rising categories that did not trigger themselves, in rule order."""
    seen = []
    for trigger in triggers:
        for label in trigger['also_rising']:
            if label not in triggered_labels and label not in seen:
                seen.append(label)
    return seen


class TrendAlerts:
    def __init__(self, state_path: Path, detection_root: Path, outbox, *,
                 writer: Callable[[dict], dict] | None = None,
                 now: Callable[[], datetime] = datetime.now):
        self.state_path = Path(state_path)
        self.detection_root = Path(detection_root)
        self.outbox = outbox
        self._writer = writer
        self._now = now

    def update_settings(self, changes: dict) -> dict:
        """Validate and persist Dashboard changes; invalid values raise."""
        if not isinstance(changes, dict) or not changes:
            raise ValueError('趨勢提醒設定必須是非空物件。')
        unknown = set(changes) - set(DEFAULT_SETTINGS) - {'categories'}
        if unknown:
            raise ValueError(f"未知的趨勢提醒設定：{'、'.join(sorted(unknown))}")
        with _LOCK:
            state = self._read()
            settings = _validated_settings({
                **state['settings'], **{k: v for k, v in changes.items() if k != 'categories'},
                'categories': _merged_categories(state['settings']['categories'],
                                                 changes.get('categories', {})),
            })
            state['settings'] = settings
            self._write(state)
            return settings

    def check(self) -> dict:
        """Evaluate once, word new alerts outside the lock, then queue them."""
        with _LOCK:
            state = self._read()
            settings = state['settings']
            if not settings['enabled']:
                return {'ok': True, 'alerts': [], 'reason': 'trend_alerts_disabled', 'errors': []}
            now = self._now()
            cooldown = timedelta(hours=settings['cooldown_hours'])
            triggers = [
                trigger for trigger in evaluate(self._windows(now, settings), settings)
                if not _cooling(state['last_alerted'].get(trigger['key']), now, cooldown)
            ]
            # Reserve the cooldown first so a parallel check cannot repeat it.
            for trigger in triggers:
                state['last_alerted'][trigger['key']] = now.isoformat(timespec='seconds')
            if triggers:
                self._write(state)
        if not triggers:
            return {'ok': True, 'alerts': [], 'reason': None, 'errors': []}
        # Everything triggered by one check goes out as one message.
        alert = self._compose(triggers, settings, now)
        with _LOCK:
            state = self._read()
            state['recent'] = ([alert] + state['recent'])[:RECENT_LIMIT]
            self._write(state)
        errors = []
        try:
            self.outbox.enqueue_alert(alert['id'], alert['text'])
        except (OSError, ValueError) as error:
            errors.append(f"{alert['label']}：{error}")
        return {'ok': not errors, 'alerts': [alert],
                'reason': 'line_queue_failed' if errors else None, 'errors': errors}

    def status(self) -> dict:
        """Dashboard view: settings, recent alerts and their LINE delivery."""
        with _LOCK:
            state = self._read()
        delivery = self.outbox.entry_statuses(alert['id'] for alert in state['recent'])
        return {
            'settings': state['settings'],
            'labels': dict(CATEGORY_NAMES),
            'directions': dict(DIRECTIONS),
            'line': self.outbox.configuration(),
            'recent': [
                # The message itself goes to LINE; the Dashboard lists categories only.
                {key: alert.get(key) for key in ('id', 'label', 'created_at', 'wording')}
                | {'delivery': delivery.get(alert['id'])}
                for alert in state['recent']
            ],
        }

    def _compose(self, triggers, settings, now):
        advice, wording = None, 'template'
        if self._writer is not None:
            try:
                written = self._writer(alert_facts(triggers, settings))
            except Exception:  # A wording failure must never stop the alert.
                written = None
            if isinstance(written, dict) and written.get('ok'):
                advice = accepted_advice(written.get('text'))
                wording = 'ai' if advice else 'template_rejected'
            else:
                wording = f"template_{(written or {}).get('reason') or 'writer_failed'}"
        advice = advice or template_advice(triggers)
        labels = [trigger['label'] for trigger in triggers]
        return {
            'id': f'trend_{uuid.uuid4().hex}', 'keys': [trigger['key'] for trigger in triggers],
            'labels': labels, 'label': '、'.join(labels),
            'created_at': now.isoformat(timespec='seconds'), 'wording': wording,
            'text': render_text(triggers, settings, advice, written_by_ai=wording == 'ai'),
        }

    def _windows(self, now, settings):
        window = timedelta(days=settings['window_days'])
        midnight = datetime.combine(now.date(), datetime.min.time())
        return {
            'current': self._shares(now - window, None),
            'previous': self._shares(now - 2 * window, now - window),
            'today': self._shares(midnight, None),
            'history': self._shares(midnight - window, midnight),
        }

    def _shares(self, start, end):
        loaded = detection_stats.load_records(self.detection_root, start=start, end=end)
        return category_shares(detection_stats.summarize(loaded['records']))

    def _read(self):
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 2, 'settings': _validated_settings({}),
                    'last_alerted': {}, 'recent': []}
        if (not isinstance(state, dict) or state.get('version') != 2
                or not isinstance(state.get('settings'), dict)
                or not isinstance(state.get('last_alerted'), dict)
                or not isinstance(state.get('recent'), list)):
            raise ValueError(f'趨勢提醒設定檔格式不正確：{self.state_path}')
        stored = state['settings']
        state['settings'] = _validated_settings({
            **stored, 'categories': _merged_categories(DEFAULT_CATEGORIES, stored.get('categories', {})),
        })
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


def _merged_categories(base, changes):
    if not isinstance(changes, dict):
        raise ValueError('categories 必須是物件。')
    unknown = set(changes) - set(DEFAULT_CATEGORIES)
    if unknown:
        raise ValueError(f"未知的提醒類別：{'、'.join(sorted(unknown))}")
    merged = {key: dict(DEFAULT_CATEGORIES[key], **base.get(key, {})) for key in DEFAULT_CATEGORIES}
    for key, params in changes.items():
        if not isinstance(params, dict) or set(params) - set(DEFAULT_CATEGORIES[key]):
            raise ValueError(f'{key} 只能設定 threshold、rise、min_samples、direction。')
        merged[key].update(params)
    return merged


def _validated_settings(settings):
    result = {}
    for key, default in DEFAULT_SETTINGS.items():
        value = settings.get(key, default)
        if key == 'enabled':
            if type(value) is not bool:
                raise ValueError('趨勢提醒開關必須是 true 或 false。')
            result[key] = value
        else:
            result[key] = _number(key, value)
    result['categories'] = {}
    for key, params in settings.get('categories', DEFAULT_CATEGORIES).items():
        if params.get('direction') not in DIRECTIONS:
            raise ValueError(f'{key}.direction 必須是 {"、".join(DIRECTIONS)} 之一。')
        result['categories'][key] = {
            **{name: _number(name, params[name], label=f'{key}.{name}')
               for name in ('threshold', 'rise', 'min_samples')},
            'direction': params['direction'],
        }
    return result


def _number(name, value, *, label=None):
    kind, low, high = SETTING_LIMITS[name]
    label = label or name
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f'{label} 必須是數字。')
    if kind is int and value != int(value):
        raise ValueError(f'{label} 必須是整數。')
    if not low <= value <= high:
        raise ValueError(f'{label} 必須介於 {low} 與 {high} 之間。')
    return kind(value)


def _cooling(last, now, cooldown):
    return bool(last) and now - datetime.fromisoformat(last) < cooldown


def _share(label, share):
    return f"已判級 {share['graded']} 顆，{label} {share['count']} 顆（{_percent(share['ratio'])}）"


def _percent(ratio):
    return f'{ratio * 100:.1f}%'
