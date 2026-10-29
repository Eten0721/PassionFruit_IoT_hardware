"""Read-only detection statistics and rule-based trend alerts on fake data."""

import csv
import inspect
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings
from PIL import Image

from . import detection_stats, notification_delivery, trend_alerts, views
from .detection import SUMMARY_FIELDS
from .grading import GRADES


def _view(*, complete=True, color='good_color', wrinkle='無皺褶（好）', defects=()):
    status = 'ok' if complete else 'error'
    return {
        'roi': {'status': status},
        'models': {
            'color': {'status': status, 'class_name': color},
            'wrinkle': {'status': status, 'display_label': wrinkle},
            'defect': {'status': status, 'detections': [
                {'class_name': name, 'class_index': 0} for name in defects
            ]},
        },
    }


class FakeDataset:
    """Writes summary rows and result.json files in the saved-result layout."""

    def __init__(self, root):
        self.root = Path(root)
        self.rows = []

    def add(self, fruit, when, *, grade=1, status='graded', mode='hardware',
            source='model', views_=None, saved=True, raw_result=None):
        label, code = GRADES[grade] if status == 'graded' else (None, None)
        result = raw_result or {
            'contract_version': 1, 'result_id': f'r{fruit}', 'fruit_id': f'fruit_{fruit:03d}',
            'hardware_mode': mode, 'capture_time': when,
            'classification': {'status': status, 'grade': grade if status == 'graded' else None,
                               'label': label, 'code': code,
                               'source': 'manual' if status == 'abandoned' else source},
            'images': {f'img_0{index}.jpg': view for index, view in enumerate(
                views_ or [_view(), _view(), _view()], 1)},
        }
        relative = f'results/fruit_{fruit:03d}/result.json'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result) if isinstance(result, dict) else result,
                        encoding='utf-8')
        self.rows.append({
            'result_id': f'r{fruit}', 'fruit_id': f'fruit_{fruit:03d}', 'capture_time': when,
            'hardware_mode': mode, 'classification_status': status,
            'result_json': relative if saved else '',
        })
        with (self.root / 'detection_summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.rows)


class DetectionStatsTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = FakeDataset(temp.name)

    def test_grades_defects_and_other_statuses_follow_counting_rules(self):
        self.data.add(1, '2026-10-01 09:00:00', grade=3)
        # Anthracnose in a finished view counts once; bad color in an unfinished view never counts.
        self.data.add(2, '2026-10-01 10:00:00', grade=0, source='manual', views_=[
            _view(defects=['anthracnose', 'anthracnose']), _view(defects=['anthracnose']),
            _view(complete=False, color='bad_color'),
        ])
        self.data.add(3, '2026-10-02 09:00:00', grade=1, views_=[
            _view(wrinkle='有皺褶（壞）', defects=['insect_track']), _view(), _view(),
        ])
        self.data.add(4, '2026-10-02 10:00:00', status='abandoned')
        self.data.add(5, '2026-10-02 11:00:00', status='needs_review')
        self.data.add(6, '2026-10-02 12:00:00', grade=0, mode='photo_only', views_=[
            _view(defects=['anthracnose']), _view(), _view(),
        ])
        self.data.add(7, '2026-10-02 13:00:00', saved=False)
        self.data.add(8, '2026-10-02 14:00:00', raw_result='{broken')

        stats = detection_stats.query(self.data.root)

        # Every graded fruit in the partition counts, whatever its capture mode.
        self.assertEqual(stats['totals'], {'fruits': 6, 'graded': 4, 'abandoned': 1, 'needs_review': 1})
        self.assertEqual({g['label']: g['count'] for g in stats['grades']},
                         {'上等': 1, '中等': 0, '下等': 1, '加工': 2})
        self.assertEqual(stats['sources'], {'model': 3, 'manual': 1})
        defects = {d['key']: (d['count'], d['ratio']) for d in stats['defects']}
        self.assertEqual(defects['anthracnose'], (2, 0.5))
        self.assertEqual(defects['wrinkle'], (1, 0.25))
        self.assertEqual(defects['insect_track'], (1, 0.25))
        self.assertEqual(defects['bad_color'], (0, 0.0))
        self.assertEqual(stats['skipped'], {'result_not_saved': 1, 'time_invalid': 0,
                                            'result_unreadable': 1})
        self.assertEqual(stats['chart']['labels'], ['上等', '中等', '下等', '加工'])
        self.assertEqual(stats['chart']['datasets'][0]['data'], [1, 0, 1, 2])
        self.assertNotIn('include_photo_only', stats['filters'])

    def test_grade_filter_makes_that_grade_the_defect_denominator(self):
        self.data.add(1, '2026-10-01 09:00:00', grade=0, views_=[
            _view(color='bad_color', defects=['anthracnose']), _view(), _view()])
        self.data.add(2, '2026-10-01 10:00:00', grade=0)
        self.data.add(3, '2026-10-01 11:00:00', grade=3, views_=[
            _view(color='bad_color'), _view(), _view()])
        self.data.add(4, '2026-10-01 12:00:00', grade=1)
        self.data.add(5, '2026-10-01 13:00:00', status='abandoned')

        processing = detection_stats.query(self.data.root, group_by='defect', grades=['加工'])

        self.assertEqual(processing['totals'], {'fruits': 2, 'graded': 2, 'abandoned': 0,
                                                'needs_review': 0})
        defects = {d['key']: (d['count'], d['ratio']) for d in processing['defects']}
        self.assertEqual(defects['bad_color'], (1, 0.5))
        self.assertEqual(defects['anthracnose'], (1, 0.5))
        self.assertEqual(processing['filters']['grades'], ['加工'])
        self.assertEqual(processing['chart']['title'], '瑕疵出現比例（加工）')
        both = detection_stats.query(self.data.root, grades=['加工', '上等', '加工'])
        self.assertEqual(both['filters']['grades'], ['上等', '加工'])
        self.assertEqual(both['totals']['graded'], 3)
        self.assertEqual(detection_stats.query(self.data.root, grades=[])['totals']['fruits'], 5)
        for grades in (['特級'], '加工', [3]):
            with self.subTest(grades=grades), self.assertRaises(ValueError):
                detection_stats.query(self.data.root, grades=grades)

    def test_compare_grades_draws_one_grouped_chart_on_shared_axis(self):
        self.data.add(1, '2026-10-01 09:00:00', grade=3)
        self.data.add(2, '2026-10-01 10:00:00', grade=3, views_=[
            _view(defects=['abrasion']), _view(), _view()])
        self.data.add(3, '2026-10-01 11:00:00', grade=0, views_=[
            _view(color='bad_color'), _view(), _view()])

        stats = detection_stats.query(self.data.root, group_by='defect',
                                      grades=['加工', '上等'], compare_grades=True)

        chart = stats['chart']
        self.assertEqual((chart['type'], chart['y_max'], chart['compare']), ('bar', 100, True))
        self.assertEqual(chart['title'], '瑕疵出現比例比較（上等、加工）')
        self.assertEqual(chart['labels'][:3], ['色澤異常', '皺褶', '擦傷'])
        self.assertEqual([d['label'] for d in chart['datasets']], ['上等（2 顆）', '加工（1 顆）'])
        self.assertEqual([d['data'][:3] for d in chart['datasets']], [[0.0, 0.0, 50.0], [100.0, 0.0, 0.0]])
        self.assertEqual(stats['by_grade']['加工']['totals']['graded'], 1)
        self.assertEqual(stats['totals']['graded'], 3)
        single = detection_stats.query(self.data.root, group_by='defect', grades=['上等'])
        self.assertEqual(single['chart']['y_max'], 100)
        self.assertNotIn('by_grade', single)
        for kwargs in ({'grades': ['上等']}, {'grades': ['上等', '加工'], 'group_by': 'grade'},
                       {'grades': ['上等', '加工'], 'bucket': 'day'},
                       {'grades': ['上等', '加工'], 'compare_grades': 'yes'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                detection_stats.query(self.data.root, **{'group_by': 'defect', 'compare_grades': True,
                                                         **kwargs})

    def test_date_range_is_inclusive_and_buckets_build_time_series(self):
        self.data.add(1, '2026-09-30T23:59:00', grade=3)
        self.data.add(2, '2026-10-01 00:00:00', grade=2, views_=[_view(defects=['abrasion']), _view(), _view()])
        self.data.add(3, '2026-10-03 23:59:59', grade=1)
        self.data.add(4, '2026-10-04 00:00:00', grade=0)
        stats = detection_stats.query(self.data.root, start_date='2026-10-01',
                                      end_date='2026-10-03', bucket='day', group_by='defect')
        self.assertEqual(stats['totals']['graded'], 2)
        self.assertEqual([point['period'] for point in stats['series']], ['2026-10-01', '2026-10-03'])
        self.assertEqual(stats['chart']['type'], 'line')
        abrasion = next(d for d in stats['chart']['datasets'] if d['label'] == '擦傷')
        self.assertEqual(abrasion['data'], [100.0, 0.0])

        weekly = detection_stats.query(self.data.root, bucket='week')
        self.assertEqual([point['period'] for point in weekly['series']], ['2026-09-28 週'])
        single = detection_stats.query(self.data.root, bucket='week', group_by='defect')['chart']
        self.assertEqual((single['type'], single['title']), ('bar', '瑕疵出現比例'))
        monthly = detection_stats.query(self.data.root, bucket='month', group_by='grade')
        self.assertEqual([point['period'] for point in monthly['series']], ['2026-09', '2026-10'])
        self.assertTrue(monthly['chart']['stacked'])

    def test_invalid_parameters_raise_and_empty_dataset_is_zero(self):
        for kwargs in ({'bucket': 'hour'}, {'group_by': 'fruit'}, {'start_date': '10/01'},
                       {'start_date': '2026-10-02', 'end_date': '2026-10-01'},
                       {'compare_grades': 'yes', 'group_by': 'defect', 'grades': ['上等', '加工']}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                detection_stats.query(self.data.root, **kwargs)
        empty = detection_stats.query(self.data.root, group_by='defect')
        self.assertEqual(empty['totals']['fruits'], 0)
        self.assertIsNone(empty['chart'])  # an all-zero chart is never drawn


class FakeOutbox:
    def __init__(self):
        self.queued = []
        self.reports = []

    def enqueue_alert(self, alert_id, text, report=None):
        self.queued.append((alert_id, text))
        self.reports.append(report)
        return {'ok': True, 'queued': True, 'reason': None}

    def entry_statuses(self, ids):
        return {alert_id: {'status': 'pending', 'reason': None} for alert_id in ids}

    def configuration(self):
        return {'configured': False, 'config_reason': 'line_credentials_missing',
                'config_missing_fields': []}


class TrendAlertTests(SimpleTestCase):
    NOW = datetime(2026, 10, 15, 12, 0, 0)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = FakeDataset(Path(temp.name) / 'detection')
        self.now = self.NOW
        self.outbox = FakeOutbox()
        self.state_path = Path(temp.name) / 'trend_alerts.json'
        self.fruit = 0

    def monitor(self):
        return trend_alerts.TrendAlerts(self.state_path, self.data.root, self.outbox,
                                        now=lambda: self.now)

    def add_fruits(self, count, *, days_ago, defects=None, grade=1, mode='hardware'):
        """``defects`` maps a defect to how many of these fruits show it."""
        for index in range(count):
            self.fruit += 1
            when = (self.NOW - timedelta(days=days_ago, minutes=index + 1)).strftime('%Y-%m-%d %H:%M:%S')
            found = [name for name, hits in (defects or {}).items() if index < hits]
            local = [name for name in found if name not in ('bad_color', 'wrinkle')]
            view = _view(defects=local, color='bad_color' if 'bad_color' in found else 'good_color',
                         wrinkle='有皺褶（壞）' if 'wrinkle' in found else '無皺褶（好）')
            self.data.add(self.fruit, when, grade=grade, mode=mode, views_=[view, _view(), _view()])

    def enable(self, **changes):
        return self.monitor().update_settings({'enabled': True, **changes})

    def test_disabled_by_default_and_global_minimum_samples(self):
        self.add_fruits(30, days_ago=1, defects={'anthracnose': 30})
        self.assertEqual(self.monitor().check()['reason'], 'trend_alerts_disabled')
        self.enable(min_samples=31)
        self.assertEqual(self.monitor().check()['alerts'], [])
        self.assertEqual(self.outbox.queued, [])

    def test_threshold_alert_has_fixed_text_and_respects_cooldown(self):
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 8})
        self.enable()

        outcome = self.monitor().check()

        [alert] = outcome['alerts']
        self.assertEqual((alert['keys'], alert['label']), (['anthracnose'], '炭疽病'))
        self.assertEqual(alert['text'], '|趨勢提醒|\n近來偏高：炭疽病\n須注意：\n'
                         '・炭疽病：須注意病害防疫\n（模型觀察結果，僅供參考）\n'
                         '數據詳情可詢問奶蛋博士獲得更多資訊')
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)
        self.assertEqual(self.outbox.queued, [(alert['id'], alert['text'])])
        [report] = self.outbox.reports  # the LINE image carries the numbers instead
        self.assertEqual((report['title'], report['highlight']), ('趨勢提醒：炭疽病偏高', ['anthracnose']))
        self.assertEqual(report['totals']['graded'], 20)
        anthracnose = next(d for d in report['defects'] if d['key'] == 'anthracnose')
        self.assertEqual((anthracnose['count'], anthracnose['ratio']), (8, 0.4))
        self.assertTrue(all(d['count'] == 0 for d in report['previous_defects']))

        self.now += timedelta(hours=23)
        self.assertEqual(self.monitor().check()['alerts'], [])
        self.now += timedelta(hours=1)
        self.assertEqual(len(self.monitor().check()['alerts']), 1)
        status = self.monitor().status()
        self.assertEqual(len(status['recent']), 2)
        self.assertEqual(status['recent'][0]['delivery'], {'status': 'pending', 'reason': None})
        # The Dashboard lists time and categories; the message itself is LINE-only.
        self.assertEqual(status['recent'][0]['label'], '炭疽病')
        self.assertEqual(set(status['recent'][0]), {'id', 'label', 'created_at', 'delivery'})

    def test_every_graded_fruit_counts_without_extra_labels(self):
        self.add_fruits(10, days_ago=1, defects={'anthracnose': 4})
        self.add_fruits(10, days_ago=2, defects={'anthracnose': 4}, mode='photo_only')
        self.enable()

        [alert] = self.monitor().check()['alerts']

        self.assertTrue(alert['text'].startswith('|趨勢提醒|\n近來偏高：炭疽病\n'))
        self.assertEqual(alert['details'][0]['current'], {'count': 8, 'graded': 20, 'ratio': 0.4})
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)
        for word in ('測試', '純拍攝', '使用硬體'):
            self.assertNotIn(word, alert['text'])

    def test_each_category_uses_its_own_parameters_and_direction(self):
        self.add_fruits(20, days_ago=1, defects={'abrasion': 5, 'anthracnose': 3})
        self.enable()
        self.assertEqual(self.monitor().check()['alerts'], [])

        self.enable(categories={'abrasion': {'threshold': 0.2}})
        [alert] = self.monitor().check()['alerts']

        self.assertEqual(alert['keys'], ['abrasion'])
        self.assertIn('・擦傷：請檢查採收與搬運作業', alert['text'])
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)

    def test_rise_against_previous_window_lists_triggered_categories(self):
        self.add_fruits(20, days_ago=9)
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 3, 'wrinkle': 4})
        self.enable()

        [alert] = self.monitor().check()['alerts']

        self.assertEqual(alert['keys'], ['wrinkle', 'anthracnose'])
        text = alert['text']
        self.assertTrue(text.startswith('|趨勢提醒|\n近來偏高：皺褶、炭疽病\n'))
        self.assertEqual([detail['reasons'] for detail in alert['details']], [['rise'], ['rise']])
        self.assertNotRegex(text, trend_alerts.DIGITS)
        self.assertEqual(len(self.outbox.queued), 1)

    def test_processing_share_uses_the_same_rules(self):
        self.add_fruits(20, days_ago=1, grade=0)
        self.add_fruits(20, days_ago=2, grade=2)
        self.enable()
        [alert] = self.monitor().check()['alerts']
        self.assertEqual((alert['keys'], alert['label']), (['processing'], '加工果'))
        self.assertIn('近來偏高：加工果\n', alert['text'])
        self.assertIn('・加工果：請檢視近期整體品質與田間狀況', alert['text'])
        self.assertEqual(alert['details'][0]['current'], {'count': 20, 'graded': 40, 'ratio': 0.5})
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)

    def test_same_day_spike_against_window_before_today(self):
        self.add_fruits(20, days_ago=3, defects={'anthracnose': 1})
        self.add_fruits(10, days_ago=0, defects={'anthracnose': 3})
        self.enable()
        self.assertEqual(self.monitor().check()['alerts'], [])  # today below min_samples
        self.enable(min_samples=10)

        [alert] = self.monitor().check()['alerts']

        self.assertTrue(alert['text'].startswith('|趨勢提醒|\n今日明顯偏高：炭疽病\n須注意：'))
        self.assertNotIn('近來偏高', alert['text'])
        self.assertEqual(alert['details'][0]['reasons'], ['spike'])
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)

    def test_no_alert_without_any_rule(self):
        self.add_fruits(20, days_ago=9, defects={'anthracnose': 2})
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 3})
        self.enable()
        self.assertEqual(self.monitor().check()['alerts'], [])

    def test_saved_details_keep_trigger_time_statistics_and_rules(self):
        self.add_fruits(20, days_ago=9, defects={'abrasion': 2})
        self.add_fruits(20, days_ago=3, defects={'abrasion': 4})
        self.add_fruits(10, days_ago=0, defects={'abrasion': 8})
        self.enable(categories={'abrasion': {'threshold': 0.35, 'rise': 0.12}},
                    spike_factor=2.5, min_samples=10)
        [alert] = self.monitor().check()['alerts']
        saved = json.loads(self.state_path.read_text(encoding='utf-8'))['recent'][0]
        self.assertEqual(saved, alert)
        self.assertNotIn('wording', saved)
        self.assertEqual(saved['window_days'], 7)
        self.assertEqual(saved['details'], [{
            'key': 'abrasion', 'label': '擦傷', 'reasons': ['threshold', 'rise', 'spike'],
            'direction': 'handling', 'threshold': 0.35, 'rise': 0.12, 'min_samples': 10,
            'spike_factor': 2.5,
            'current': {'count': 12, 'graded': 30, 'ratio': 0.4},
            'previous': {'count': 2, 'graded': 20, 'ratio': 0.1},
            'today': {'count': 8, 'graded': 10, 'ratio': 0.8},
            'history': {'count': 4, 'graded': 20, 'ratio': 0.2},
        }])
        self.enable(window_days=14, spike_factor=3,
                    categories={'abrasion': {'threshold': 0.9, 'direction': 'quality'}})
        snapshot = self.monitor().status(include_details=True)['recent'][0]
        self.assertEqual(snapshot['details'], saved['details'])
        self.assertEqual(snapshot['window_days'], 7)
        self.assertEqual(set(self.monitor().status()['recent'][0]),
                         {'id', 'label', 'created_at', 'delivery'})

    def test_several_categories_from_one_check_become_one_message(self):
        self.add_fruits(20, days_ago=9)
        self.add_fruits(20, days_ago=1, defects={'bad_color': 10, 'wrinkle': 8, 'abrasion': 8})
        self.enable()

        [alert] = self.monitor().check()['alerts']

        self.assertEqual(alert['labels'], ['色澤異常', '皺褶', '擦傷'])
        self.assertEqual(alert['text'], '|趨勢提醒|\n近來偏高：色澤異常、皺褶、擦傷\n須注意：\n'
                         '・色澤異常、皺褶：請留意栽培管理與採收時機\n'
                         '・擦傷：請檢查採收與搬運作業\n（模型觀察結果，僅供參考）\n'
                         '數據詳情可詢問奶蛋博士獲得更多資訊')
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)
        self.assertEqual(self.outbox.queued, [(alert['id'], alert['text'])])

    def test_threshold_and_spike_categories_have_separate_lines(self):
        self.add_fruits(20, days_ago=3, defects={'bad_color': 15, 'abrasion': 10, 'anthracnose': 1})
        self.add_fruits(10, days_ago=0, defects={'abrasion': 10, 'anthracnose': 3})
        self.enable(min_samples=10)
        [alert] = self.monitor().check()['alerts']
        self.assertEqual([detail['reasons'] for detail in alert['details']],
                         [['threshold'], ['threshold', 'spike'], ['spike']])
        self.assertEqual(alert['text'], '|趨勢提醒|\n近來偏高：色澤異常、擦傷\n'
                         '今日明顯偏高：炭疽病\n須注意：\n'
                         '・色澤異常：請留意栽培管理與採收時機\n'
                         '・擦傷：請檢查採收與搬運作業\n・炭疽病：須注意病害防疫\n'
                         '（模型觀察結果，僅供參考）\n數據詳情可詢問奶蛋博士獲得更多資訊')
        self.assertNotRegex(alert['text'], trend_alerts.DIGITS)
        self.assertEqual(len(self.outbox.queued), 1)

    def test_cooldown_is_independent_for_each_category(self):
        self.add_fruits(20, days_ago=1, defects={'abrasion': 8})
        self.enable()
        self.monitor().check()
        self.add_fruits(20, days_ago=0, defects={'abrasion': 8, 'anthracnose': 10})
        [alert] = self.monitor().check()['alerts']
        self.assertEqual(alert['keys'], ['anthracnose'])
        self.assertNotIn('擦傷', alert['text'])
        self.assertEqual(len(self.outbox.queued), 2)

    def test_monitor_does_not_create_a_gemini_assistant_and_state_keeps_summary(self):
        self.NOW = datetime.now()
        self.add_fruits(20, days_ago=1, defects={'abrasion': 10})
        self.enable()
        with override_settings(DATASET_ROOT=self.data.root.parent,
                               MOTOR_COMMAND_SEQUENCE_PATH=self.state_path.parent / 'sequence.json'), \
                mock.patch.object(views, 'dashboard_assistant', side_effect=AssertionError('Gemini')):
            [alert] = views.trend_alert_monitor().check()['alerts']
            recent = self.client.get('/api/state/').json()['trend_alerts']['recent']
        self.assertEqual(recent, [{'id': alert['id'], 'label': '擦傷',
                                   'created_at': alert['created_at'],
                                   'delivery': {'status': 'pending', 'reason': None}}])

    def test_version_2_settings_migrate_to_one_loosest_minimum(self):
        categories = {key: {**params, 'min_samples': 20}
                      for key, params in trend_alerts.DEFAULT_CATEGORIES.items()}
        categories['bad_color']['min_samples'] = 1
        self.state_path.write_text(json.dumps({
            'version': 2, 'last_alerted': {}, 'recent': [],
            'settings': {'enabled': True, 'window_days': 7, 'cooldown_hours': 24,
                         'spike_factor': 10.0, 'spike_min_samples': 10, 'categories': categories},
        }), encoding='utf-8')
        settings = self.monitor().status()['settings']
        self.assertEqual((settings['min_samples'], settings['spike_factor']), (1, 10.0))
        self.assertNotIn('spike_min_samples', settings)
        self.assertNotIn('min_samples', settings['categories']['bad_color'])
        self.monitor().update_settings({'enabled': False})
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['version'], 3)

    def test_status_lists_categories_still_cooling(self):
        self.add_fruits(20, days_ago=1, defects={'abrasion': 8})
        self.enable()
        self.monitor().check()
        self.assertEqual(self.monitor().status()['cooling'], {'abrasion': '2026-10-16T12:00:00'})
        self.now += timedelta(hours=24)
        self.assertEqual(self.monitor().status()['cooling'], {})

    def test_test_alert_ignores_switch_and_cooldown_and_saves_nothing(self):
        self.add_fruits(20, days_ago=1, defects={'abrasion': 8})
        sent = self.monitor().send_test()  # switch still off
        self.assertEqual((sent['queued'], sent['labels']), (True, ['擦傷']))
        [(alert_id, text)] = self.outbox.queued
        self.assertTrue(alert_id.startswith('trend_test_'))
        self.assertTrue(text.startswith('測試 |趨勢提醒|\n近來偏高：擦傷'))
        self.assertEqual((self.outbox.reports[0]['title'], self.outbox.reports[0]['highlight']),
                         ('測試 趨勢提醒：擦傷偏高', ['abrasion']))
        self.assertFalse(self.state_path.exists())

        self.enable()
        self.monitor().check()
        self.monitor().send_test()  # abrasion is cooling, the test still lists it
        self.assertEqual(len(self.outbox.queued), 3)
        self.assertEqual(len(json.loads(self.state_path.read_text(encoding='utf-8'))['recent']), 1)

        empty = trend_alerts.render_text([], test=True)
        self.assertEqual(empty.split('\n')[:2], ['測試 |趨勢提醒|', '目前沒有類別達到提醒條件'])

    def test_settings_are_validated(self):
        for changes in ({}, {'enabled': 'yes'}, {'window_days': 0}, {'window_days': 1.5},
                        {'spike_factor': 1}, {'cooldown_hours': True}, {'unknown': 1},
                        {'min_samples': 0}, {'spike_min_samples': 10},
                        {'categories': {'anthracnose': {'min_samples': 5}}},
                        {'categories': {'mold': {}}},
                        {'categories': {'anthracnose': {'threshold': 2}}},
                        {'categories': {'anthracnose': {'direction': 'magic'}}},
                        {'categories': {'anthracnose': {'colour': 1}}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.monitor().update_settings(changes)
        saved = self.monitor().update_settings({
            'window_days': 14, 'categories': {'abrasion': {'threshold': 0.25, 'direction': 'quality'}}})
        self.assertEqual(saved['window_days'], 14)
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['categories']['abrasion'],
                         {'threshold': 0.25, 'rise': 0.1, 'direction': 'quality'})
        self.assertEqual(saved['categories']['anthracnose'],
                         trend_alerts.DEFAULT_CATEGORIES['anthracnose'])


class TrendAlertOutboxTests(SimpleTestCase):
    def test_alert_is_sent_as_text_with_retry_key_regardless_of_mode_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'credentials.json').write_text(json.dumps(
                dict.fromkeys(notification_delivery.CREDENTIAL_FIELDS, 'x')), encoding='utf-8')
            calls = []

            def post(url, body, headers, timeout):
                calls.append((url, json.loads(body), headers))
                return notification_delivery.HttpResponse(200, {}, b'{}')

            outbox = notification_delivery.Outbox(root / 'outbox.json', root / 'credentials.json',
                                                  http_post=post, now=lambda: 1000.0)
            with self.assertRaises(ValueError):
                outbox.enqueue_alert('fruit_1', 'text')
            self.assertTrue(outbox.enqueue_alert('trend_1', '|趨勢提醒|\n內容\n')['queued'])
            self.assertEqual(outbox.enqueue_alert('trend_1', 'again')['reason'], 'duplicate')

            outbox.deliver_pending()

            self.assertEqual(len(calls), 1)
            url, body, headers = calls[0]
            self.assertEqual(url, notification_delivery.PUSH_URL)
            self.assertEqual(body['messages'], [{'type': 'text', 'text': '|趨勢提醒|\n內容'}])
            self.assertIn('X-Line-Retry-Key', headers)
            self.assertEqual(outbox.entry_statuses(['trend_1', 'missing']),
                             {'trend_1': {'status': 'sent', 'reason': None}})
            self.assertEqual(outbox.status('hardware')['counts']['sent'], 0)


    def test_alert_with_report_is_sent_as_flex_with_statistics_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'credentials.json').write_text(json.dumps(
                dict.fromkeys(notification_delivery.CREDENTIAL_FIELDS, 'x')), encoding='utf-8')
            calls = []
            image_url = 'https://res.cloudinary.com/x/image/upload/v1/passionfruit_notifications/trend_1.jpg'

            def post(url, body, headers, timeout):
                if 'cloudinary' in url:
                    calls.append(url)
                    return notification_delivery.HttpResponse(
                        200, {}, json.dumps({'secure_url': image_url}).encode())
                calls.append(json.loads(body))
                return notification_delivery.HttpResponse(200, {}, b'{}')

            def draw(report, target, *, image_format, font_path):
                self.assertEqual((report, image_format), ({'title': 't'}, 'jpg'))
                Image.new('RGB', (40, 50), 'white').save(target, 'JPEG')

            outbox = notification_delivery.Outbox(root / 'outbox.json', root / 'credentials.json',
                                                  http_post=post, now=lambda: 1000.0)
            with self.assertRaises(ValueError):
                outbox.enqueue_alert('trend_1', 'text', report=['not', 'a', 'dict'])
            outbox.enqueue_alert('trend_1', '|趨勢提醒|\n近來偏高：擦傷', report={'title': 't'})
            with mock.patch.object(notification_delivery.detection_report, 'render', side_effect=draw):
                outbox.deliver_pending()

            self.assertEqual(calls[0], notification_delivery.UPLOAD_URL.format(cloud='x'))
            [message] = calls[1]['messages']
            self.assertEqual(message['type'], 'flex')
            hero = message['contents']['hero']
            self.assertEqual((hero['aspectRatio'], hero['action']['uri']), ('40:50', image_url))
            self.assertEqual(message['contents']['body']['contents'][0]['text'], '|趨勢提醒|')
            self.assertEqual(outbox.entry_statuses(['trend_1'])['trend_1']['status'], 'sent')

            outbox.enqueue_alert('trend_2', 'text', report={'title': 't'})
            with mock.patch.object(notification_delivery.detection_report, 'render',
                                   side_effect=FileNotFoundError('找不到中文字型')):
                outbox.deliver_pending()
            status = outbox.entry_statuses(['trend_2'])['trend_2']
            self.assertEqual(status['status'], 'failed')
            self.assertIn('統計圖無法產生', status['reason'])


class TrendAlertViewTests(SimpleTestCase):
    def test_check_starts_in_background_for_any_saved_graded_result(self):
        saved = {'hardware_mode': 'hardware', 'storage': {'status': 'saved'},
                 'classification': {'status': 'graded'}}
        with mock.patch.object(views.threading, 'Thread') as thread:
            for skipped in ({**saved, 'classification': {'status': 'needs_review'}},
                            {**saved, 'storage': {'status': 'failed'}}, None):
                self.assertEqual(views._check_trend_alerts(skipped), '')
            thread.assert_not_called()
            self.assertEqual(views._check_trend_alerts(saved), '')
            thread.assert_called_once_with(target=views._run_trend_check,
                                           name='trend-alert-check', daemon=True)
            thread.return_value.start.assert_called_once_with()
            views._check_trend_alerts({**saved, 'hardware_mode': 'photo_only'})
            self.assertEqual(thread.call_count, 2)

    def test_background_check_never_raises(self):
        monitor = mock.Mock()
        monitor.check.side_effect = ValueError('broken')
        with mock.patch.object(views, 'trend_alert_monitor', return_value=monitor), \
                self.assertLogs('fruit_app.views', 'ERROR'):
            views._run_trend_check()

    def test_settings_api_ignores_dashboard_dataset_mode(self):
        monitor = mock.Mock()
        request = RequestFactory().post('/api/trend_alerts/', data=json.dumps(
            {'dataset_mode': 'detection', 'enabled': True}), content_type='application/json')
        with mock.patch.object(views, 'trend_alert_monitor', return_value=monitor), \
                mock.patch.object(views, '_state_payload', return_value={}):
            response = inspect.unwrap(views.trend_alerts_api)(request)
        self.assertEqual(response.status_code, 200)
        monitor.update_settings.assert_called_once_with({'enabled': True})

    def test_test_alert_api_reports_labels_or_queue_failure(self):
        monitor = mock.Mock()
        monitor.send_test.return_value = {'ok': True, 'queued': True, 'labels': ['擦傷']}
        request = RequestFactory().post('/api/trend_alerts/test/', data='{}',
                                        content_type='application/json')
        test_api = inspect.unwrap(views.trend_alert_test_api)
        with mock.patch.object(views, 'trend_alert_monitor', return_value=monitor), \
                mock.patch.object(views, '_state_payload', side_effect=lambda extra: extra):
            self.assertEqual(json.loads(test_api(request).content),
                             {'trend_alert_test': monitor.send_test.return_value})
            monitor.send_test.side_effect = OSError('disk full')
            failed = test_api(request)
        self.assertEqual((failed.status_code, json.loads(failed.content)['reason']),
                         (503, 'trend_alert_test_failed'))

    def test_stats_api_rejects_bad_parameters(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(DATASET_ROOT=Path(temp)):
            response = self.client.get('/api/detection/stats/', {'bucket': 'hour'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['reason'], 'invalid_stats_query')
            ok = self.client.get('/api/detection/stats/', {'group_by': 'defect'})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()['totals']['fruits'], 0)
            graded = self.client.get('/api/detection/stats/', {'grade': ['加工', '下等']})
            self.assertEqual(graded.json()['filters']['grades'], ['下等', '加工'])
            compared = self.client.get('/api/detection/stats/', {
                'grade': ['上等', '加工'], 'group_by': 'defect', 'compare_grades': 'true'})
            self.assertTrue(compared.json()['filters']['compare_grades'])
            self.assertIsNone(compared.json()['chart'])  # empty data: no chart
            bad =self.client.get('/api/detection/stats/', {'grade': '特級'})
            self.assertEqual(bad.json()['reason'], 'invalid_stats_query')
