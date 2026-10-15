"""Read-only detection statistics and rule-based trend alerts on fake data."""

import csv
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

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

        self.assertEqual(stats['totals'], {'fruits': 5, 'graded': 3, 'abandoned': 1, 'needs_review': 1})
        self.assertEqual({g['label']: g['count'] for g in stats['grades']},
                         {'上等': 1, '中等': 0, '下等': 1, '加工': 1})
        self.assertEqual(stats['sources'], {'model': 2, 'manual': 1})
        defects = {d['key']: (d['count'], d['ratio']) for d in stats['defects']}
        self.assertEqual(defects['anthracnose'], (1, 0.3333))
        self.assertEqual(defects['wrinkle'], (1, 0.3333))
        self.assertEqual(defects['insect_track'], (1, 0.3333))
        self.assertEqual(defects['bad_color'], (0, 0.0))
        self.assertEqual(stats['skipped'], {'result_not_saved': 1, 'time_invalid': 0,
                                            'result_unreadable': 1})
        self.assertEqual(stats['chart']['labels'], ['上等', '中等', '下等', '加工'])
        self.assertEqual(stats['chart']['datasets'][0]['data'], [1, 0, 1, 1])

        with_photo = detection_stats.query(self.data.root, include_photo_only=True)
        self.assertEqual(with_photo['totals']['graded'], 4)
        self.assertEqual({d['key']: d['count'] for d in with_photo['defects']}['anthracnose'], 2)

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
        monthly = detection_stats.query(self.data.root, bucket='month', group_by='grade')
        self.assertEqual([point['period'] for point in monthly['series']], ['2026-09', '2026-10'])
        self.assertTrue(monthly['chart']['stacked'])

    def test_invalid_parameters_raise_and_empty_dataset_is_zero(self):
        for kwargs in ({'bucket': 'hour'}, {'group_by': 'fruit'}, {'start_date': '10/01'},
                       {'start_date': '2026-10-02', 'end_date': '2026-10-01'},
                       {'include_photo_only': 'yes'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                detection_stats.query(self.data.root, **kwargs)
        empty = detection_stats.query(self.data.root, group_by='defect')
        self.assertEqual(empty['totals']['fruits'], 0)
        self.assertEqual(empty['chart']['datasets'][0]['data'], [0.0] * 6)


class FakeOutbox:
    def __init__(self):
        self.queued = []

    def enqueue_alert(self, alert_id, text):
        self.queued.append((alert_id, text))
        return {'ok': True, 'queued': True, 'reason': None}

    def entry_statuses(self, ids):
        return {alert_id: {'status': 'pending', 'reason': None} for alert_id in ids}

    def configuration(self):
        return {'configured': False, 'config_reason': '尚未設定'}


class TrendAlertTests(SimpleTestCase):
    NOW = datetime(2026, 10, 15, 12, 0, 0)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = FakeDataset(Path(temp.name) / 'detection')
        self.now = self.NOW
        self.outbox = FakeOutbox()
        self.alerts = trend_alerts.TrendAlerts(Path(temp.name) / 'trend_alerts.json',
                                               self.data.root, self.outbox, now=lambda: self.now)
        self.fruit = 0

    def add_fruits(self, count, *, days_ago, anthracnose=0, mode='hardware'):
        for index in range(count):
            self.fruit += 1
            when = (self.NOW - timedelta(days=days_ago, minutes=index)).strftime('%Y-%m-%d %H:%M:%S')
            defects = ['anthracnose'] if index < anthracnose else []
            self.data.add(self.fruit, when, grade=1, mode=mode,
                          views_=[_view(defects=defects), _view(), _view()])

    def test_disabled_by_default_and_needs_minimum_samples(self):
        self.add_fruits(30, days_ago=1, anthracnose=30)
        self.assertEqual(self.alerts.check()['reason'], 'trend_alerts_disabled')
        self.alerts.update_settings({'enabled': True, 'min_samples': 31})
        self.assertEqual(self.alerts.check()['alerts'], [])
        self.assertEqual(self.outbox.queued, [])

    def test_threshold_alert_uses_template_and_cooldown(self):
        self.add_fruits(20, days_ago=1, anthracnose=8)
        self.add_fruits(10, days_ago=2, anthracnose=10, mode='photo_only')
        self.alerts.update_settings({'enabled': True})

        outcome = self.alerts.check()

        self.assertEqual([alert['key'] for alert in outcome['alerts']], ['anthracnose'])
        text = outcome['alerts'][0]['text']
        self.assertIn('炭疽病數量近來有上升趨勢，須注意防疫。', text)
        self.assertIn('最近 7 天：已判級 20 顆，炭疽病 8 顆（40.0%）', text)
        self.assertIn('前 7 天：已判級 0 顆，樣本不足不比較', text)
        self.assertIn('依據：比例達 30.0% 門檻', text)
        self.assertEqual(self.outbox.queued, [(outcome['alerts'][0]['id'], text)])

        self.now += timedelta(hours=23)
        self.assertEqual(self.alerts.check()['alerts'], [])
        self.now += timedelta(hours=1)
        self.assertEqual(len(self.alerts.check()['alerts']), 1)
        status = self.alerts.status()
        self.assertEqual(len(status['recent']), 2)
        self.assertEqual(status['recent'][0]['delivery'], {'status': 'pending', 'reason': None})

    def test_rise_against_previous_window(self):
        self.add_fruits(20, days_ago=9, anthracnose=1)
        self.add_fruits(20, days_ago=1, anthracnose=4)
        self.alerts.update_settings({'enabled': True, 'ratio_threshold': 0.5})

        alert = self.alerts.check()['alerts'][0]

        self.assertEqual(alert['previous'], {'graded': 20, 'count': 1, 'ratio': 0.05,
                                             'sufficient': True})
        self.assertIn('依據：比前 7 天上升 10.0 個百分點以上', alert['text'])
        self.assertIn('前 7 天：已判級 20 顆，炭疽病 1 顆（5.0%）', alert['text'])

    def test_no_alert_without_rise_or_threshold(self):
        self.add_fruits(20, days_ago=9, anthracnose=3)
        self.add_fruits(20, days_ago=1, anthracnose=4)
        self.alerts.update_settings({'enabled': True})
        self.assertEqual(self.alerts.check()['alerts'], [])

    def test_settings_are_validated(self):
        for changes in ({}, {'enabled': 'yes'}, {'window_days': 0}, {'window_days': 1.5},
                        {'ratio_threshold': 2}, {'cooldown_hours': True}, {'unknown': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.alerts.update_settings(changes)
        saved = self.alerts.update_settings({'window_days': 14, 'ratio_threshold': 0.25})
        self.assertEqual(saved['window_days'], 14)
        self.assertFalse(saved['enabled'])


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


class TrendAlertViewTests(SimpleTestCase):
    def test_check_runs_only_for_saved_graded_hardware_results_and_never_raises(self):
        saved = {'hardware_mode': 'hardware', 'storage': {'status': 'saved'},
                 'classification': {'status': 'graded'}}
        monitor = mock.Mock()
        monitor.check.return_value = {'ok': True, 'alerts': [{'label': '炭疽病'}], 'errors': []}
        with mock.patch.object(views, 'trend_alert_monitor', return_value=monitor):
            for skipped in ({**saved, 'hardware_mode': 'photo_only'},
                            {**saved, 'classification': {'status': 'needs_review'}},
                            {**saved, 'storage': {'status': 'failed'}}, None):
                self.assertEqual(views._check_trend_alerts(skipped), '')
            monitor.check.assert_not_called()
            self.assertEqual(views._check_trend_alerts(saved), ' 已產生趨勢提醒：炭疽病。')
            monitor.check.side_effect = ValueError('broken')
            with self.assertLogs('fruit_app.views', 'ERROR'):
                self.assertEqual(views._check_trend_alerts(saved), ' 趨勢提醒檢查失敗：ValueError')

    def test_stats_api_rejects_bad_parameters(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(DATASET_ROOT=Path(temp)):
            response = self.client.get('/api/detection/stats/', {'bucket': 'hour'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['reason'], 'invalid_stats_query')
            ok = self.client.get('/api/detection/stats/', {'group_by': 'defect'})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()['totals']['fruits'], 0)
