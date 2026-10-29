"""Read-only statistics over the detection dataset.

The only inputs are ``detection_summary.csv`` and the ``results/*/result.json``
files it points to. Nothing here writes files, touches runtime state or moves
hardware; the Dashboard assistant and the rule-based trend alerts both call
``query``/``load_records`` so every surface reports the same numbers.

Counting rules:

- Only rows whose result was saved count; a fruit's time is its
  ``capture_time`` (``completed_at`` when missing), compared in local time.
- Every graded fruit in the detection partition counts.
- Graded fruits form the four-grade distribution and the defect denominator;
  ``abandoned`` and ``needs_review`` are counted separately.
- A fruit shows a defect when any view that finished detection observed it
  (the LINE notification rule in ``notification.view_observations``).
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import grading
from .notification import OBSERVATION_NAMES, PHOTO_NAMES, view_observations


SUMMARY_FILENAME = 'detection_summary.csv'
GRADE_ORDER = ('上等', '中等', '下等', '加工')
DEFECT_KEYS = tuple(OBSERVATION_NAMES)
BUCKETS = ('none', 'day', 'week', 'month')
GROUP_BY = ('grade', 'defect')
OTHER_STATUSES = ('abandoned', 'needs_review')


def query(detection_root: Path, *, start_date: str | None = None,
          end_date: str | None = None, bucket: str = 'none', group_by: str = 'grade',
          grades: list[str] | None = None,
          compare_grades: bool = False) -> dict:
    """Return statistics and one chart spec for an inclusive local date range.

    ``grades`` keeps only graded fruits of those grades (e.g. ``['加工']``), so
    defect ratios use that grade's fruit count as the denominator and the
    ``abandoned``/``needs_review`` totals drop to zero. ``compare_grades``
    (defect view, no time bucket, two or more grades) adds ``by_grade`` and
    draws one grouped bar chart with a dataset per grade on a shared 0-100 %
    axis. Invalid parameters raise ``ValueError``; an empty dataset is a
    valid, all-zero answer.
    """
    if bucket not in BUCKETS:
        raise ValueError(f'不支援的統計級距：{bucket!r}')
    if group_by not in GROUP_BY:
        raise ValueError(f'不支援的分組方式：{group_by!r}')
    grades = _parse_grades(grades)
    if type(compare_grades) is not bool:
        raise ValueError('compare_grades 必須是 true 或 false。')
    if compare_grades and (group_by != 'defect' or bucket != 'none' or len(grades or []) < 2):
        raise ValueError('比較等級需要 group_by=defect、bucket=none 且至少兩個等級。')
    start = _parse_date(start_date, 'start_date')
    end = _parse_date(end_date, 'end_date')
    if start and end and start > end:
        raise ValueError('起始日期不得晚於結束日期。')
    loaded = load_records(
        detection_root,
        start=datetime.combine(start, datetime.min.time()) if start else None,
        end=datetime.combine(end + timedelta(days=1), datetime.min.time()) if end else None,
    )
    records = loaded['records']
    if grades:
        records = [record for record in records
                   if record['status'] == 'graded' and record['label'] in grades]
    summary = summarize(records, bucket=bucket)
    by_grade = None
    if compare_grades:
        by_grade = {grade: summarize([r for r in records if r['label'] == grade])
                    for grade in grades}
        chart = compare_chart_spec(by_grade)
    else:
        chart = chart_spec(summary, group_by=group_by)
        if grades:
            chart['title'] += f"（{'、'.join(grades)}）"
    if not any(value for dataset in chart['datasets'] for value in dataset['data']):
        chart = None  # An all-zero chart only suggests data that does not exist.
    return {
        'ok': True,
        'filters': {
            'start_date': start.isoformat() if start else None,
            'end_date': end.isoformat() if end else None,
            'bucket': bucket, 'group_by': group_by,
            'grades': grades, 'compare_grades': compare_grades,
        },
        **summary,
        **({'by_grade': {
            grade: {'totals': part['totals'], 'defects': part['defects']}
            for grade, part in by_grade.items()
        }} if by_grade else {}),
        'skipped': loaded['skipped'],
        'chart': chart,
    }


def _parse_grades(grades):
    """None or [] means all grades; otherwise distinct labels in display order."""
    if grades is None:
        return None
    if not isinstance(grades, list) or any(not isinstance(grade, str) for grade in grades):
        raise ValueError('grades 必須是等級名稱陣列。')
    unknown = set(grades) - set(GRADE_ORDER)
    if unknown:
        raise ValueError(f"不支援的等級：{'、'.join(sorted(unknown))}；可用 {'、'.join(GRADE_ORDER)}")
    return [grade for grade in GRADE_ORDER if grade in grades] or None


def load_records(detection_root: Path, *, start: datetime | None = None,
                 end: datetime | None = None) -> dict:
    """Return saved fruits in ``[start, end)`` plus counts of skipped rows."""
    root = Path(detection_root).resolve()
    records = []
    skipped = {'result_not_saved': 0, 'time_invalid': 0, 'result_unreadable': 0}
    for row in _read_rows(root / SUMMARY_FILENAME):
        if not row.get('result_json'):
            skipped['result_not_saved'] += 1
            continue
        moment = _row_time(row)
        if moment is None:
            skipped['time_invalid'] += 1
            continue
        if (start and moment < start) or (end and moment >= end):
            continue
        record = _record(root, row, moment)
        if record is None:
            skipped['result_unreadable'] += 1
            continue
        records.append(record)
    records.sort(key=lambda record: record['time'])
    return {'records': records, 'skipped': skipped}


def summarize(records: list[dict], *, bucket: str = 'none') -> dict:
    """Aggregate records; ratios use graded fruits as the denominator."""
    if bucket not in BUCKETS:
        raise ValueError(f'不支援的統計級距：{bucket!r}')
    counts = _count(records)
    graded = counts['graded']
    summary = {
        'totals': {
            'fruits': len(records), 'graded': graded,
            **{status: counts[status] for status in OTHER_STATUSES},
        },
        'grades': [
            {'label': label, 'count': counts['grades'][label],
             'ratio': _ratio(counts['grades'][label], graded)}
            for label in GRADE_ORDER
        ],
        'sources': counts['sources'],
        'defects': [
            {'key': key, 'label': OBSERVATION_NAMES[key], 'count': counts['defects'][key],
             'ratio': _ratio(counts['defects'][key], graded)}
            for key in DEFECT_KEYS
        ],
        'series': [],
    }
    if bucket == 'none':
        return summary
    periods: dict[str, list[dict]] = {}
    for record in records:
        periods.setdefault(_period(record['time'], bucket), []).append(record)
    for period, members in periods.items():
        period_counts = _count(members)
        summary['series'].append({
            'period': period, 'graded': period_counts['graded'],
            **{status: period_counts[status] for status in OTHER_STATUSES},
            'grades': period_counts['grades'], 'defects': period_counts['defects'],
        })
    return summary


def chart_spec(summary: dict, *, group_by: str) -> dict:
    """A library-neutral chart description drawn without any LLM rewording."""
    series = summary['series']
    if group_by == 'grade' and not series:
        return {
            'type': 'bar', 'title': '四級分布', 'unit': '顆', 'stacked': False,
            'labels': list(GRADE_ORDER),
            'datasets': [{'label': '果實數', 'data': [g['count'] for g in summary['grades']]}],
        }
    if group_by == 'grade':
        return {
            'type': 'bar', 'title': '四級分布（依時間）', 'unit': '顆', 'stacked': True,
            'labels': [point['period'] for point in series],
            'datasets': [
                {'label': label, 'data': [point['grades'][label] for point in series]}
                for label in GRADE_ORDER
            ],
        }
    if len(series) < 2:  # one period draws a line of lone dots
        return {
            'type': 'bar', 'title': '瑕疵出現比例', 'unit': '%', 'stacked': False, 'y_max': 100,
            'labels': [d['label'] for d in summary['defects']],
            'datasets': [{'label': '佔已判級果實', 'data': [
                _percent(d['ratio']) for d in summary['defects']
            ]}],
        }
    return {
        'type': 'line', 'title': '瑕疵出現比例（依時間）', 'unit': '%', 'stacked': False, 'y_max': 100,
        'labels': [point['period'] for point in series],
        'datasets': [
            {'label': OBSERVATION_NAMES[key], 'data': [
                _percent(_ratio(point['defects'][key], point['graded'])) for point in series
            ]}
            for key in DEFECT_KEYS
        ],
    }


def compare_chart_spec(by_grade: dict) -> dict:
    """One grouped bar chart: defects on x, one dataset per grade, 0-100 %."""
    return {
        'type': 'bar', 'title': f"瑕疵出現比例比較（{'、'.join(by_grade)}）",
        'unit': '%', 'stacked': False, 'y_max': 100, 'compare': True,
        'labels': [OBSERVATION_NAMES[key] for key in DEFECT_KEYS],
        'datasets': [
            {'label': f"{grade}（{part['totals']['graded']} 顆）",
             'data': [_percent(item['ratio']) for item in part['defects']]}
            for grade, part in by_grade.items()
        ],
    }


def _count(records):
    counts = {
        'graded': 0, **dict.fromkeys(OTHER_STATUSES, 0),
        'grades': dict.fromkeys(GRADE_ORDER, 0),
        'sources': {'model': 0, 'manual': 0},
        'defects': dict.fromkeys(DEFECT_KEYS, 0),
    }
    for record in records:
        status = record['status']
        if status in OTHER_STATUSES:
            counts[status] += 1
            continue
        counts['graded'] += 1
        counts['grades'][record['label']] += 1
        counts['sources'][record['source']] += 1
        for key in record['defects']:
            counts['defects'][key] += 1
    return counts


def _record(root, row, moment):
    """Return one fruit from its saved result, or None when it cannot be read."""
    path = (root / row['result_json']).resolve()
    if not path.is_relative_to(root):
        return None
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or not isinstance(result.get('classification'), dict):
        return None
    classification = result['classification']
    status = classification.get('status')
    base = {
        'result_id': result.get('result_id'), 'fruit_id': result.get('fruit_id'),
        'time': moment,
        'status': status, 'label': None, 'source': classification.get('source'),
        'defects': [],
    }
    if status in OTHER_STATUSES:
        return base
    grade = classification.get('grade')
    if (status != 'graded' or type(grade) is not int or grade not in range(4)
            or base['source'] not in ('model', 'manual')):
        return None
    try:
        defects = _observed_defects(result.get('images'))
    except (TypeError, ValueError, AttributeError):
        return None
    return {**base, 'label': grading.grade_label(grade)[0], 'defects': defects}


def _observed_defects(images):
    if not isinstance(images, dict):
        raise ValueError('images 必須是物件。')
    observed = set()
    for index, name in enumerate(PHOTO_NAMES, 1):
        image = images.get(name) or {}
        if not isinstance(image, dict):
            raise ValueError(f'{name} 必須是物件。')
        keys, complete = view_observations(index, image)
        if complete:
            observed.update(keys)
    return [key for key in DEFECT_KEYS if key in observed]


def _read_rows(path):
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    except FileNotFoundError:
        return []


def _row_time(row):
    text = row.get('capture_time') or row.get('completed_at')
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _parse_date(text, field):
    if text in (None, ''):
        return None
    if not isinstance(text, str):
        raise ValueError(f'{field} 必須是 YYYY-MM-DD。')
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f'{field} 必須是 YYYY-MM-DD：{text!r}') from None


def _period(moment, bucket):
    day = moment.date()
    if bucket == 'day':
        return day.isoformat()
    if bucket == 'week':
        return f'{(day - timedelta(days=day.weekday())).isoformat()} 週'
    return day.strftime('%Y-%m')


def _ratio(count, total):
    return round(count / total, 4) if total else 0.0


def _percent(ratio):
    return round(ratio * 100, 1)
