"""Statistics image and ZIP export for the detection partition.

``snapshot`` freezes the numbers a report shows as plain JSON, so a trend
alert can be queued now and drawn later by the LINE worker without the data
having moved underneath it. ``render`` draws one snapshot; ``write_export``
packs the summary, the image and optionally every saved result into a ZIP.
All numbers come from ``detection_stats.summarize``, the same counting the
Dashboard assistant and the trend rules use.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # No display on the server; set before pyplot loads.
import matplotlib.pyplot as plt
from matplotlib import font_manager

from . import dataset_store, detection_stats


DEFAULT_FONT_PATH = Path('C:/Windows/Fonts/msjh.ttc')
REPORT_IMAGE_NAME = 'report.png'
SUMMARY_NAME = detection_stats.SUMMARY_FILENAME
INK, MUTED, GRID, ALERT = '#1f2421', '#6b716d', '#e6e8e4', '#c0392b'
GRADE_COLORS = {'上等': '#2f7d4f', '中等': '#7fae5a', '下等': '#d9a23b', '加工': '#b5523b'}
DEFECT_COLOR = '#8a5a9e'
PROCESSING = 'processing'  # trend_alerts category key for the processing-fruit share


def snapshot(records: list[dict], *, title: str, subtitle: str,
             previous: list[dict] | None = None, highlight=(), now: datetime | None = None) -> dict:
    """JSON-safe numbers for one report; ``previous`` adds a comparison window."""
    summary = detection_stats.summarize(records)
    times = [record['time'] for record in records]
    return {
        'title': title, 'subtitle': subtitle, 'highlight': sorted(highlight),
        'generated_at': (now or datetime.now()).isoformat(timespec='seconds'),
        'period': [min(times).isoformat(timespec='seconds'),
                   max(times).isoformat(timespec='seconds')] if times else None,
        'totals': summary['totals'], 'grades': summary['grades'], 'defects': summary['defects'],
        'by_grade': {
            grade: detection_stats.summarize(
                [r for r in records if r['status'] == 'graded' and r['label'] == grade])['defects']
            for grade in detection_stats.GRADE_ORDER
        },
        'previous_defects': None if previous is None
        else detection_stats.summarize(previous)['defects'],
    }


def render(report: dict, target, *, image_format: str = 'png',
           font_path: Path | None = None) -> None:
    """Draw ``report`` into a path or binary stream; a missing font raises."""
    if image_format not in ('png', 'jpg'):
        raise ValueError(f'統計圖只支援 png 或 jpg：{image_format!r}')
    font = _font_family(Path(font_path) if font_path else DEFAULT_FONT_PATH)
    with plt.rc_context({'font.family': font}):
        figure = _figure(report)
        try:
            figure.savefig(target, format=image_format, facecolor='white',
                           **({'pil_kwargs': {'quality': 90}} if image_format == 'jpg' else {}))
        finally:
            plt.close(figure)


def write_export(detection_root: Path, stream, *, include_results: bool,
                 font_path: Path | None = None, now: datetime | None = None) -> dict:
    """Write the partition's export ZIP to ``stream``; return what went in.

    Only results named by a saved summary row are packed, and never one the
    reset left behind as ignored, so the ZIP matches the summary it carries.
    """
    if type(include_results) is not bool:
        raise ValueError('include_results 必須是 true 或 false。')
    root = Path(detection_root).resolve()
    now = now or datetime.now()
    loaded = detection_stats.load_records(root)
    report = snapshot(loaded['records'], title='百香果分級檢測報告',
                      subtitle='範圍：目前檢測分區全部結果（上次重置後）', now=now)
    summary_path = root / SUMMARY_NAME
    result_dirs = _saved_result_dirs(root, summary_path) if include_results else []
    image = io.BytesIO()
    render(report, image, font_path=font_path)  # Before the ZIP, so a font error leaves no partial file.
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(REPORT_IMAGE_NAME, image.getvalue())
        if summary_path.is_file():
            archive.write(summary_path, SUMMARY_NAME, compress_type=zipfile.ZIP_DEFLATED)
        for directory in result_dirs:
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    # Photos are already compressed; JSON shrinks well.
                    compress = zipfile.ZIP_DEFLATED if path.suffix == '.json' else zipfile.ZIP_STORED
                    archive.write(path, path.relative_to(root).as_posix(), compress_type=compress)
    return {'fruits': report['totals']['fruits'], 'results': len(result_dirs),
            'filename': f"detection_export_{now:%Y%m%d_%H%M}.zip"}


def _saved_result_dirs(root, summary_path):
    if not summary_path.is_file():
        return []
    with summary_path.open('r', encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    directories = []
    for row in rows:
        if not row.get('result_json'):
            continue
        directory = (root / row['result_json']).resolve().parent
        if (not directory.is_relative_to(root / 'results') or not directory.is_dir()
                or directory in directories
                or dataset_store.is_ignored_reset_path(root, directory)):
            continue
        directories.append(directory)
    return directories


def _font_family(path):
    if not path.is_file():
        raise FileNotFoundError(f'找不到中文字型：{path}；請指定微軟正黑體 msjh.ttc。')
    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path)).get_name()


def _figure(report):
    highlight = set(report['highlight'])
    totals, graded = report['totals'], report['totals']['graded']
    figure = plt.figure(figsize=(12, 15), dpi=150, facecolor='white')
    grid = figure.add_gridspec(4, 2, height_ratios=[0.55, 0.5, 1.4, 1.4], width_ratios=[1, 1.25],
                               left=0.11, right=0.94, top=0.96, bottom=0.07,
                               hspace=0.5, wspace=0.35)
    _header(figure.add_subplot(grid[0, :]), report, alert=bool(highlight))
    top = report['grades'][0]
    _tiles(figure.add_subplot(grid[1, :]), [
        ('檢測總數', f"{totals['fruits']}", '顆'), ('完成分級', f'{graded}', '顆'),
        ('上等率', f"{top['ratio'] * 100:.0f}" if graded else '—', '%'),
        ('需人工複查', f"{totals['needs_review'] + totals['abandoned']}", '顆'),
    ])
    _grade_bars(figure.add_subplot(grid[2, 0]), report['grades'], PROCESSING in highlight)
    _grade_heatmap(figure.add_subplot(grid[2, 1]), report)
    _defect_bars(figure.add_subplot(grid[3, :]), report, highlight)
    figure.text(0.11, 0.012, f"產生時間：{report['generated_at'].replace('T', ' ')[:16]}",
                fontsize=10, color=MUTED)
    figure.text(0.94, 0.012, 'PassionFruit 自動分級系統', fontsize=10, color=MUTED, ha='right')
    return figure


def _header(axis, report, *, alert):
    axis.axis('off')
    axis.text(0, 0.95, report['title'], fontsize=30, fontweight='bold',
              color=ALERT if alert else INK, va='top', transform=axis.transAxes)
    axis.text(0, 0.45, report['subtitle'], fontsize=15, color=INK, va='top',
              transform=axis.transAxes)
    period = report['period']
    span = ' ～ '.join(moment.replace('T', ' ')[:16] for moment in period) if period else '—'
    axis.text(0, 0.15, f'檢測期間：{span}', fontsize=13, color=MUTED, va='top',
              transform=axis.transAxes)
    axis.plot([0, 1], [-0.1, -0.1], color=INK, linewidth=2, transform=axis.transAxes,
              clip_on=False)


def _tiles(axis, tiles):
    axis.axis('off')
    for index, (label, value, unit) in enumerate(tiles):
        x = index * 0.25
        axis.add_patch(plt.Rectangle((x + 0.005, 0), 0.235, 1, transform=axis.transAxes,
                                     facecolor='#f4f6f2', edgecolor=GRID))
        axis.text(x + 0.025, 0.78, label, fontsize=13, color=MUTED, va='center',
                  transform=axis.transAxes)
        axis.text(x + 0.025, 0.32, value, fontsize=34, fontweight='bold', color=INK,
                  va='center', transform=axis.transAxes)
        axis.text(x + 0.025 + 0.03 * len(value), 0.27, unit, fontsize=14, color=MUTED,
                  va='center', transform=axis.transAxes)


def _grade_bars(axis, grades, processing_alert):
    labels = [grade['label'] for grade in grades]
    counts = [grade['count'] for grade in grades]
    rows = list(range(len(labels)))[::-1]
    axis.barh(rows, counts, height=0.6, color=[GRADE_COLORS[label] for label in labels],
              edgecolor=[ALERT if label == '加工' and processing_alert else 'none'
                         for label in labels], linewidth=3)
    peak = max(counts) or 1
    for row, grade in zip(rows, grades):
        axis.text(grade['count'] + peak * 0.03, row,
                  f"{grade['count']} 顆\n{grade['ratio'] * 100:.0f}%",
                  va='center', fontsize=12, color=INK, linespacing=1.3)
    axis.set_yticks(rows, labels, fontsize=15)
    axis.set_xlim(0, peak * 1.45)
    axis.set_xticks([])
    for side in ('top', 'right', 'bottom'):
        axis.spines[side].set_visible(False)
    axis.spines['left'].set_color(GRID)
    _title(axis, '等級分布')


def _grade_heatmap(axis, report):
    labels = [grade['label'] for grade in report['grades']]
    counts = {grade['label']: grade['count'] for grade in report['grades']}
    names = [defect['label'] for defect in report['defects']]
    cells = [[defect['ratio'] for defect in report['by_grade'][label]] for label in labels]
    axis.imshow(cells, cmap='Purples', vmin=0, vmax=1, aspect='auto')
    for row, label in enumerate(labels):
        for column, ratio in enumerate(cells[row]):
            axis.text(column, row, f'{ratio * 100:.0f}%' if counts[label] else '—',
                      ha='center', va='center', fontsize=11,
                      color='white' if ratio > 0.55 else INK)
    axis.set_xticks(range(len(names)), [name.replace('異常', '\n異常') for name in names],
                    fontsize=11)
    axis.set_yticks(range(len(labels)), [f'{label}（{counts[label]}）' for label in labels],
                    fontsize=12)
    axis.tick_params(length=0)
    for spine in axis.spines.values():
        spine.set_visible(False)
    _title(axis, '各等級的瑕疵比例')


def _defect_bars(axis, report, highlight):
    defects = report['defects']
    previous = report['previous_defects']
    order = sorted(range(len(defects)), key=lambda index: defects[index]['ratio'])
    for row, index in enumerate(order):
        defect = defects[index]
        hit = defect['key'] in highlight
        axis.barh(row, defect['ratio'] * 100, height=0.6, color=ALERT if hit else DEFECT_COLOR)
        text = f"{defect['ratio'] * 100:.0f}%（{defect['count']} 顆）"
        if previous is not None:
            before = previous[index]['ratio'] * 100
            axis.plot([before, before], [row - 0.38, row + 0.38], color=INK, linewidth=2.5)
            text += f'\n前期 {before:.0f}%'
        axis.text(defect['ratio'] * 100 + 1, row, text, va='center', fontsize=12,
                  linespacing=1.2, color=ALERT if hit else INK,
                  fontweight='bold' if hit else 'normal')
    axis.set_yticks(range(len(order)), [defects[index]['label'] for index in order], fontsize=15)
    for tick, index in zip(axis.get_yticklabels(), order):
        if defects[index]['key'] in highlight:
            tick.set_color(ALERT)
            tick.set_fontweight('bold')
    axis.set_xlim(0, 118)  # Room right of 100 % for the value labels.
    axis.set_xticks(range(0, 101, 20))
    axis.xaxis.grid(True, color=GRID)
    axis.set_axisbelow(True)
    note = '出現比例（%，以已分級果實為分母；一顆可有多種瑕疵）'
    if previous is not None:
        note += '　｜　黑線＝前期比例'
    axis.set_xlabel(note, fontsize=12, color=MUTED)
    for side in ('top', 'right'):
        axis.spines[side].set_visible(False)
    _title(axis, '瑕疵出現比例')


def _title(axis, text):
    axis.set_title(text, fontsize=17, fontweight='bold', color=INK, loc='left', pad=14)
