"""Read-only detection statistics and rule-based trend alerts on fake data."""

import csv
import inspect
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings

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
        self.state_path = Path(temp.name) / 'trend_alerts.json'
        self.fruit = 0

    def monitor(self, writer=None):
        return trend_alerts.TrendAlerts(self.state_path, self.data.root, self.outbox,
                                        writer=writer, now=lambda: self.now)

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

    def test_disabled_by_default_and_category_minimum_samples(self):
        self.add_fruits(30, days_ago=1, defects={'anthracnose': 30})
        self.assertEqual(self.monitor().check()['reason'], 'trend_alerts_disabled')
        self.enable(categories={'anthracnose': {'min_samples': 31}})
        self.assertEqual(self.monitor().check()['alerts'], [])
        self.assertEqual(self.outbox.queued, [])

    def test_threshold_alert_falls_back_to_template_and_respects_cooldown(self):
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 8})
        self.add_fruits(10, days_ago=2, defects={'anthracnose': 10}, mode='photo_only')
        self.enable()

        outcome = self.monitor().check()

        [alert] = outcome['alerts']
        self.assertEqual((alert['key'], alert['wording']), ('anthracnose', 'template'))
        for line in ('炭疽病近來有上升趨勢', '最近 7 天：已判級 20 顆，炭疽病 8 顆（40.0%）',
                     '前 7 天：已判級 0 顆，樣本不足不比較', '依據：比例達 20.0% 門檻',
                     '說明：炭疽病比例近來偏高，須注意病害防疫。'):
            self.assertIn(line, alert['text'])
        self.assertEqual(self.outbox.queued, [(alert['id'], alert['text'])])

        self.now += timedelta(hours=23)
        self.assertEqual(self.monitor().check()['alerts'], [])
        self.now += timedelta(hours=1)
        self.assertEqual(len(self.monitor().check()['alerts']), 1)
        status = self.monitor().status()
        self.assertEqual(len(status['recent']), 2)
        self.assertEqual(status['recent'][0]['delivery'], {'status': 'pending', 'reason': None})

    def test_each_category_uses_its_own_parameters_and_direction(self):
        self.add_fruits(20, days_ago=1, defects={'abrasion': 5, 'anthracnose': 3})
        self.enable()
        self.assertEqual(self.monitor().check()['alerts'], [])

        self.enable(categories={'abrasion': {'threshold': 0.2}})
        [alert] = self.monitor().check()['alerts']

        self.assertEqual(alert['key'], 'abrasion')
        self.assertIn('說明：擦傷比例近來偏高，請檢查採收與搬運作業。', alert['text'])

    def test_rise_against_previous_window_lists_other_rising_categories(self):
        self.add_fruits(20, days_ago=9)
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 3, 'wrinkle': 4})
        self.enable()

        alerts = {alert['key']: alert for alert in self.monitor().check()['alerts']}

        self.assertEqual(set(alerts), {'anthracnose', 'wrinkle'})
        text = alerts['anthracnose']['text']
        self.assertIn('前 7 天：已判級 20 顆，炭疽病 0 顆（0.0%）', text)
        self.assertIn('依據：比前 7 天上升 10.0 個百分點以上', text)
        self.assertIn('同時上升：皺褶', text)
        self.assertIn('同時上升：炭疽病', alerts['wrinkle']['text'])

    def test_processing_share_uses_the_same_rules(self):
        self.add_fruits(20, days_ago=1, grade=0)
        self.add_fruits(20, days_ago=2, grade=2)
        self.enable()
        [alert] = self.monitor().check()['alerts']
        self.assertEqual((alert['key'], alert['label']), ('processing', '加工果'))
        self.assertIn('最近 7 天：已判級 40 顆，加工果 20 顆（50.0%）', alert['text'])

    def test_same_day_spike_against_window_before_today(self):
        self.add_fruits(20, days_ago=3, defects={'anthracnose': 1})
        self.add_fruits(10, days_ago=0, defects={'anthracnose': 3})
        self.enable()

        [alert] = self.monitor().check()['alerts']

        self.assertIn('炭疽病今日明顯偏高', alert['text'])
        self.assertIn('今日：已判級 10 顆，炭疽病 3 顆（30.0%）；今日前 7 天為 5.0%', alert['text'])
        self.assertIn('依據：今日比例達前 7 天的 2 倍以上', alert['text'])

    def test_no_alert_without_any_rule(self):
        self.add_fruits(20, days_ago=9, defects={'anthracnose': 2})
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 3})
        self.enable()
        self.assertEqual(self.monitor().check()['alerts'], [])

    def test_writer_only_words_and_falls_back_to_template(self):
        self.add_fruits(20, days_ago=1, defects={'anthracnose': 8})
        self.enable()
        cases = (
            (lambda facts: {'ok': True, 'text': '近期病斑變多，建議加強清園與通風。'}, 'ai'),
            (lambda facts: {'ok': True, 'text': '約有 40% 果實受影響'}, 'template_rejected'),
            (lambda facts: {'ok': False, 'reason': 'assistant_offline'}, 'template_assistant_offline'),
            (mock.Mock(side_effect=TimeoutError('slow')), 'template_writer_failed'),
        )
        seen = []
        for writer, wording in cases:
            with self.subTest(wording=wording):
                self.now += timedelta(days=1, hours=1)
                recorder = mock.Mock(side_effect=writer)
                [alert] = self.monitor(writer=recorder).check()['alerts']
                facts = recorder.call_args.args[0]
                seen.append(facts)
                self.assertEqual(alert['wording'], wording)
                self.assertIn('最近 7 天：已判級', alert['text'])
                if wording == 'ai':
                    self.assertIn('說明（AI 依上列數據撰寫）：近期病斑變多，建議加強清園與通風。', alert['text'])
                else:
                    self.assertIn('說明：炭疽病比例近來偏高，須注意病害防疫。', alert['text'])
                    self.assertNotIn('約有', alert['text'])
        self.assertEqual(seen[0]['category'], '炭疽病')
        self.assertEqual(seen[0]['reasons'], ['比例達門檻'])
        self.assertEqual(seen[0]['current'], {'count': 8, 'graded': 20, 'ratio': 0.4})
        self.assertEqual(len(self.outbox.queued), 4)

    def test_settings_are_validated(self):
        for changes in ({}, {'enabled': 'yes'}, {'window_days': 0}, {'window_days': 1.5},
                        {'spike_factor': 1}, {'cooldown_hours': True}, {'unknown': 1},
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
                         {'threshold': 0.25, 'rise': 0.1, 'min_samples': 20, 'direction': 'quality'})
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


class TrendAlertViewTests(SimpleTestCase):
    def test_check_starts_in_background_only_for_saved_graded_hardware_results(self):
        saved = {'hardware_mode': 'hardware', 'storage': {'status': 'saved'},
                 'classification': {'status': 'graded'}}
        with mock.patch.object(views.threading, 'Thread') as thread:
            for skipped in ({**saved, 'hardware_mode': 'photo_only'},
                            {**saved, 'classification': {'status': 'needs_review'}},
                            {**saved, 'storage': {'status': 'failed'}}, None):
                self.assertEqual(views._check_trend_alerts(skipped), '')
            thread.assert_not_called()
            self.assertEqual(views._check_trend_alerts(saved), '')
            thread.assert_called_once_with(target=views._run_trend_check,
                                           name='trend-alert-check', daemon=True)
            thread.return_value.start.assert_called_once_with()

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

    def test_stats_api_rejects_bad_parameters(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(DATASET_ROOT=Path(temp)):
            response = self.client.get('/api/detection/stats/', {'bucket': 'hour'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['reason'], 'invalid_stats_query')
            ok = self.client.get('/api/detection/stats/', {'group_by': 'defect'})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()['totals']['fruits'], 0)
