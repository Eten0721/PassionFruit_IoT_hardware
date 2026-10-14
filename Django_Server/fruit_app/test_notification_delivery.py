"""Durable LINE/Cloudinary delivery, faked only at the external HTTP boundary."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import notification_delivery as delivery
from . import test_detection, test_notification, views

SECRETS = {
    'line_channel_access_token': 'line-token-SECRET',
    'line_group_id': 'C0123456789abcdef',
    'cloudinary_cloud_name': 'demo-cloud',
    'cloudinary_api_key': '123456789',
    'cloudinary_api_secret': 'cloud-secret-SECRET',
}
IMAGE_URL = 'https://res.cloudinary.com/demo-cloud/image/upload/v1/passionfruit_notifications/r.jpg'


class FakeHttp:
    """Scripted responses per service; exceptions model network failures."""

    def __init__(self):
        self.calls = []
        self.cloudinary = []
        self.line = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({'url': url, 'body': body, 'headers': dict(headers)})
        script = self.cloudinary if 'cloudinary' in url else self.line
        outcome = script.pop(0) if script else self._default(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @staticmethod
    def _default(url):
        if 'cloudinary' in url:
            return delivery.HttpResponse(200, {}, json.dumps({'secure_url': IMAGE_URL}).encode())
        return delivery.HttpResponse(200, {}, b'{}')

    def line_calls(self):
        return [call for call in self.calls if 'api.line.me' in call['url']]

    def upload_calls(self):
        return [call for call in self.calls if 'cloudinary' in call['url']]


class NotificationDeliveryTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = self.root / 'results'
        self.results.mkdir()
        self.state_path = self.root / 'runtime_config' / 'notification_outbox.json'
        self.credentials_path = self.root / 'runtime_config' / 'notification_credentials.json'
        self.credentials_path.parent.mkdir()
        self.credentials_path.write_text(json.dumps(SECRETS), encoding='utf-8')
        self.http = FakeHttp()
        self.clock = [1_000_000.0]

    def outbox(self):
        return delivery.Outbox(self.state_path, self.credentials_path,
                               http_post=self.http, now=lambda: self.clock[0])

    def save_result(self, name='fruit_001', *, result_id=None, mode='photo_only',
                    label='加工', source='model'):
        fixture = test_notification.NotificationTests()
        fixture.root = self.results / name
        fixture.root.mkdir()
        fixture.result_path = fixture.root / 'result.json'
        result = fixture.processing_fixture(source)
        grade, code = {'加工': (0, 'processing'), '上等': (3, 'high')}[label]
        result['classification'].update(grade=grade, label=label, code=code)
        result.update(result_id=result_id or f'{name}_id', fruit_id=name,
                      hardware_mode=mode)
        fixture.write_fixture(result)
        return result, fixture.result_path

    def enable(self, mode='photo_only', enabled=True):
        self.outbox().set_enabled(mode, enabled)

    def entry(self, result_id):
        return json.loads(self.state_path.read_text('utf-8'))['entries'][result_id]

    def test_notifications_start_disabled_and_disabled_results_are_never_backfilled(self):
        status = self.outbox().status('photo_only')
        self.assertFalse(status['enabled'])
        self.assertFalse(self.outbox().status('hardware')['enabled'])
        result, path = self.save_result()
        self.assertEqual(self.outbox().enqueue(result, path),
                         {'ok': True, 'queued': False, 'reason': 'notifications_disabled'})
        self.enable()
        self.assertEqual(self.outbox().deliver_pending(), [])
        self.assertEqual(self.http.calls, [])

    def test_only_saved_final_processing_results_are_queued_once(self):
        self.enable()
        self.enable('hardware')
        graded, graded_path = self.save_result('fruit_001', label='上等')
        self.assertEqual(self.outbox().enqueue(graded, graded_path)['reason'], 'not_processing')
        review, review_path = self.save_result('fruit_002')
        review['classification'].update(status='needs_review', grade=None, label=None,
                                        code=None, source=None)
        self.assertEqual(self.outbox().enqueue(review, review_path)['reason'], 'not_graded')
        unsaved, unsaved_path = self.save_result('fruit_003')
        unsaved['storage'] = {'status': 'failed'}
        self.assertEqual(self.outbox().enqueue(unsaved, unsaved_path)['reason'], 'result_not_saved')
        manual, manual_path = self.save_result('fruit_004', source='manual', mode='hardware')
        self.assertEqual(self.outbox().enqueue(manual, manual_path),
                         {'ok': True, 'queued': True, 'reason': None})
        retry_key = self.entry('fruit_004_id')['retry_key']
        # Sorting updates or repeated saves keep the stable identity and retry key.
        manual['sorting'] = {'status': 'completed'}
        self.assertEqual(self.outbox().enqueue(manual, manual_path),
                         {'ok': True, 'queued': False, 'reason': 'duplicate'})
        self.assertEqual(self.entry('fruit_004_id')['retry_key'], retry_key)
        self.assertEqual(self.outbox().status('hardware')['counts']['pending'], 1)

    def test_delivery_uploads_composite_and_pushes_text_and_image_in_one_request(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        outcomes = self.outbox().deliver_pending()
        self.assertEqual(outcomes, [{'result_id': 'fruit_001_id', 'status': 'sent', 'reason': None}])
        upload = self.http.upload_calls()
        self.assertEqual(len(upload), 1)
        self.assertEqual(upload[0]['url'],
                         'https://api.cloudinary.com/v1_1/demo-cloud/image/upload')
        self.assertIn(b'passionfruit_notifications/fruit_001_id', upload[0]['body'])
        self.assertIn(b'name="signature"', upload[0]['body'])
        self.assertNotIn(b'cloud-secret-SECRET', upload[0]['body'])
        self.assertIn(b'\xff\xd8', upload[0]['body'])  # the JPEG composite itself
        [push] = self.http.line_calls()
        self.assertEqual(push['url'], 'https://api.line.me/v2/bot/message/push')
        self.assertEqual(push['headers']['Authorization'], 'Bearer line-token-SECRET')
        self.assertEqual(push['headers']['X-Line-Retry-Key'], self.entry('fruit_001_id')['retry_key'])
        body = json.loads(push['body'])
        self.assertEqual(body['to'], 'C0123456789abcdef')
        text, image = body['messages']
        self.assertEqual(text['type'], 'text')
        self.assertTrue(text['text'].startswith('測試 |瑕疵通知|\n偵測時間：2026-10-14 09:00:00\n'))
        self.assertEqual(image['type'], 'image')
        self.assertEqual(image['originalContentUrl'], IMAGE_URL)
        self.assertTrue(image['previewImageUrl'].startswith('https://res.cloudinary.com/'))
        self.assertIn('/upload/c_limit,w_960,h_960', image['previewImageUrl'])
        self.assertEqual(self.entry('fruit_001_id')['status'], 'sent')
        self.assertEqual(self.outbox().deliver_pending(), [])
        self.assertEqual(len(self.http.line_calls()), 1)
        # Only the composite left the machine; the result folder is unchanged.
        self.assertFalse(any(p.name.startswith('notification_') for p in path.parent.iterdir()))

    def test_missing_credentials_keep_queue_with_readable_reason_and_no_requests(self):
        self.credentials_path.unlink()
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.assertEqual(self.outbox().deliver_pending(), [])
        self.assertEqual(self.http.calls, [])
        status = self.outbox().status('photo_only', 'fruit_001_id')
        self.assertFalse(status['configured'])
        self.assertIn('notification_credentials.json', status['config_reason'])
        self.assertEqual(status['current']['status'], 'pending')
        self.credentials_path.write_text(json.dumps({**SECRETS, 'line_group_id': ''}), 'utf-8')
        self.assertIn('line_group_id', self.outbox().status('photo_only')['config_reason'])

    def test_offline_upload_and_line_failures_retry_later_with_original_time(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.http.cloudinary.append(delivery.NetworkError('dns failed', maybe_delivered=False))
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'pending')
        self.assertIn('Cloudinary', outcome['reason'])
        self.assertEqual(self.http.line_calls(), [])
        # Backoff: nothing is retried before the next attempt time.
        self.assertEqual(self.outbox().deliver_pending(), [])
        self.clock[0] += 3600
        self.http.line.append(delivery.NetworkError('connection refused', maybe_delivered=False))
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'pending')
        self.assertIsNone(self.entry('fruit_001_id')['unknown_since'])
        self.clock[0] += 3600
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'sent')
        texts = [json.loads(call['body'])['messages'][0]['text'] for call in self.http.line_calls()]
        self.assertEqual(len(set(texts)), 1)
        self.assertIn('偵測時間：2026-10-14 09:00:00', texts[0])
        self.assertEqual(len(self.http.upload_calls()), 2)  # the URL is kept once uploaded

    def test_unknown_delivery_after_restart_reuses_retry_key_and_accepts_409(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.http.line.append(delivery.NetworkError('read timed out', maybe_delivered=True))
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'pending')
        self.assertIsNotNone(self.entry('fruit_001_id')['unknown_since'])
        self.clock[0] += 3600
        # A fresh Outbox models a restarted service reading the durable queue.
        self.http.line.append(delivery.HttpResponse(409, {'x-line-accepted-request-id': 'abc'}, b'{}'))
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'sent')
        keys = {call['headers']['X-Line-Retry-Key'] for call in self.http.line_calls()}
        self.assertEqual(len(keys), 1)
        self.assertEqual(self.entry('fruit_001_id')['accepted_request_id'], 'abc')

    def test_delivered_but_unrecorded_push_is_resolved_without_a_second_message(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        with mock.patch.object(delivery.Outbox, '_mark_sent', side_effect=OSError('disk full')):
            [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'pending')
        self.assertIn('本機讀寫失敗', outcome['reason'])
        self.assertEqual(self.entry('fruit_001_id')['status'], 'pending')
        self.http.line.append(delivery.HttpResponse(409, {}, b'{}'))
        self.clock[0] += 3600
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'sent')
        self.assertEqual(len({c['headers']['X-Line-Retry-Key'] for c in self.http.line_calls()}), 1)

    def test_unconfirmed_delivery_past_retry_key_window_is_tracked_not_resent(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.http.line.append(delivery.NetworkError('read timed out', maybe_delivered=True))
        self.outbox().deliver_pending()
        self.clock[0] += 24 * 3600
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'uncertain')
        self.assertEqual(len(self.http.line_calls()), 1)
        self.clock[0] += 3600
        self.assertEqual(self.outbox().deliver_pending(), [])
        status = self.outbox().status('photo_only', 'fruit_001_id')
        self.assertEqual(status['counts']['uncertain'], 1)
        self.assertEqual(status['current']['status'], 'uncertain')
        self.assertIn('無法確認', status['current']['reason'])

    def test_disabling_pauses_existing_queue_and_reenabling_resumes_without_backfill(self):
        self.enable()
        first, first_path = self.save_result('fruit_001')
        self.outbox().enqueue(first, first_path)
        self.enable(enabled=False)
        second, second_path = self.save_result('fruit_002')
        self.assertEqual(self.outbox().enqueue(second, second_path)['reason'],
                         'notifications_disabled')
        self.assertEqual(self.outbox().deliver_pending(), [])
        self.assertEqual(self.http.calls, [])
        status = self.outbox().status('photo_only')
        self.assertEqual((status['enabled'], status['counts']['pending'], status['paused']),
                         (False, 1, 1))
        self.enable()
        # A repeated save of the switched-off round (e.g. a duplicate manual
        # decision) after re-enabling is still not backfilled.
        self.assertEqual(self.outbox().enqueue(second, second_path)['reason'], 'duplicate')
        self.assertEqual([o['result_id'] for o in self.outbox().deliver_pending()],
                         ['fruit_001_id'])
        self.assertEqual(self.entry('fruit_002_id')['status'], 'skipped')
        self.assertEqual([c for c in self.http.line_calls()
                          if 'fruit_002' in c['body'].decode()], [])

    def test_rejection_after_unanswered_attempt_is_uncertain_not_failed(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.http.line.append(delivery.NetworkError('read timed out', maybe_delivered=True))
        self.outbox().deliver_pending()
        first_unknown = self.entry('fruit_001_id')['unknown_since']
        self.clock[0] += 3600
        self.http.line.append(delivery.HttpResponse(429, {}, b'{"message":"rate"}'))
        self.assertEqual(self.outbox().deliver_pending()[0]['status'], 'pending')
        self.assertEqual(self.entry('fruit_001_id')['unknown_since'], first_unknown)
        self.clock[0] += 3600
        self.http.line.append(delivery.HttpResponse(400, {}, b'{"message":"bad"}'))
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'uncertain')
        self.assertIn('無法確認', outcome['reason'])

    def test_modes_have_independent_switches(self):
        self.enable('hardware')
        hardware, hardware_path = self.save_result('fruit_001', mode='hardware')
        photo, photo_path = self.save_result('fruit_002')
        self.assertTrue(self.outbox().enqueue(hardware, hardware_path)['queued'])
        self.assertFalse(self.outbox().enqueue(photo, photo_path)['queued'])
        self.outbox().deliver_pending()
        push_text = json.loads(self.http.line_calls()[0]['body'])['messages'][0]['text']
        self.assertTrue(push_text.startswith('|瑕疵通知|'))
        photo_status = self.outbox().status('photo_only')
        self.assertEqual(photo_status['counts']['sent'], 0)
        self.assertEqual(photo_status['counts']['skipped'], 1)
        self.assertEqual(self.outbox().status('hardware')['counts']['sent'], 1)

    def test_rejections_and_quota_failures_are_reported_explicitly(self):
        self.enable()
        result, path = self.save_result('fruit_001')
        self.outbox().enqueue(result, path)
        self.http.cloudinary.append(delivery.HttpResponse(
            400, {}, json.dumps({'error': {'message': 'File size too large'}}).encode()))
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'failed')
        self.assertIn('File size too large', outcome['reason'])
        self.assertEqual(self.http.line_calls(), [])

        second, second_path = self.save_result('fruit_002')
        self.outbox().enqueue(second, second_path)
        self.http.line.append(delivery.HttpResponse(429, {}, b'{"message":"monthly limit"}'))
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'pending')
        self.assertIn('額度', outcome['reason'])
        self.clock[0] += 3600
        self.http.line.append(delivery.HttpResponse(400, {}, b'{"message":"invalid uri"}'))
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome['status'], 'failed')
        self.assertIn('invalid uri', outcome['reason'])
        self.assertIn('invalid uri', self.outbox().status('photo_only')['last_error'])

    def test_missing_result_fails_with_reason_instead_of_inventing_content(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        path.unlink()
        [outcome] = self.outbox().deliver_pending()
        self.assertEqual(outcome, {'result_id': 'fruit_001_id', 'status': 'failed',
                                   'reason': '通知內容無法產生：result_not_found'})
        self.assertEqual(self.http.calls, [])

    def test_secrets_never_reach_queue_status_or_errors(self):
        self.enable()
        result, path = self.save_result()
        self.outbox().enqueue(result, path)
        self.http.line.append(delivery.HttpResponse(401, {}, b'{"message":"Authentication failed"}'))
        self.outbox().deliver_pending()
        stored = self.state_path.read_text('utf-8')
        status = json.dumps(self.outbox().status('photo_only', 'fruit_001_id'), ensure_ascii=False)
        for field in ('line_channel_access_token', 'line_group_id',
                      'cloudinary_api_key', 'cloudinary_api_secret'):
            secret = SECRETS[field]
            self.assertNotIn(secret, stored)
            self.assertNotIn(secret, status)
        self.assertIn('憑證', self.entry('fruit_001_id')['last_error'])
        self.assertEqual(self.entry('fruit_001_id')['status'], 'pending')

    def test_http_boundary_separates_unsent_from_possibly_delivered_failures(self):
        import socket
        import urllib.error
        cases = (
            (urllib.error.URLError(socket.gaierror('no dns')), False),
            (urllib.error.URLError(ConnectionRefusedError('refused')), False),
            (urllib.error.URLError(OSError(10051, 'network unreachable')), False),
            (TimeoutError('read timed out'), True),
            (ConnectionResetError('reset'), True),
        )
        for error, maybe_delivered in cases:
            with self.subTest(error=error), mock.patch('urllib.request.urlopen',
                                                       side_effect=error):
                with self.assertRaises(delivery.NetworkError) as raised:
                    delivery.http_post('https://api.line.me/x', b'{}', {}, 1)
                self.assertIs(raised.exception.maybe_delivered, maybe_delivered)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.outbox().set_enabled('collection', True)


class NotificationFlowTests(SimpleTestCase):
    """The Dashboard switch and queueing through existing Django APIs."""

    flow = test_detection.DetectionFlowTests
    setUp = flow.setUp
    tearDown = flow.tearDown
    _post_json = flow._post_json
    _fake_image = flow._fake_image
    _upload_station = flow._upload_station
    _start_detection = flow._start_detection
    _upload_three = flow._upload_three
    _wait_for_detection = flow._wait_for_detection
    _external_result = staticmethod(flow._external_result)
    _worst_second_view = flow.__dict__['_worst_second_view']
    _run_photo_round = flow._run_photo_round
    _manual_grade = flow._manual_grade
    del flow

    def _processing_round(self):
        with mock.patch('fruit_app.detection.execute_model_batch',
                        side_effect=self._worst_second_view):
            return self._run_photo_round()

    def _outbox_entries(self):
        path = views.notification_outbox().state_path
        if not path.exists():
            return {}
        return json.loads(path.read_text('utf-8'))['entries']

    def test_photo_only_default_off_then_switch_queues_processing_without_blocking(self):
        fruit_id, state = self._processing_round()
        self.assertEqual(state['detection_status'], 'completed')
        self.assertFalse(state['notification']['enabled'])
        self.assertEqual(state['notification']['current']['status'], 'skipped')

        response = self._post_json('/api/notification/', {'enabled': True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['notification']['enabled'])
        self.assertEqual(self._outbox_entries()[state['detection_result']['result_id']]['status'],
                         'skipped')  # remembered, never backfilled

        with mock.patch.object(delivery.Outbox, 'deliver_pending',
                               side_effect=AssertionError('delivery must stay off the request path')):
            fruit_id, state = self._processing_round()
        self.assertEqual(state['detection_status'], 'completed')
        result_id = state['detection_result']['result_id']
        self.assertEqual(self._outbox_entries()[result_id]['status'], 'pending')
        self.assertEqual(state['notification']['current']['status'], 'pending')
        self.assertNotIn('SECRET', json.dumps(state))

        with mock.patch('fruit_app.detection.execute_model_batch',
                        side_effect=self._external_result):
            _fruit, state = self._run_photo_round()
        self.assertEqual(state['detection_result']['classification']['label'], '上等')
        self.assertEqual(len(self._outbox_entries()), 2)

    def test_switch_follows_current_hardware_mode(self):
        self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only'})
        self.assertEqual(self._post_json('/api/notification/', {'enabled': True}).status_code, 200)
        state = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'hardware'}).json()
        self.assertFalse(state['notification']['enabled'])
        self.assertEqual(state['notification']['mode'], 'hardware')
        self.assertEqual(self._post_json('/api/notification/', {'enabled': 'yes'}).status_code, 400)

    def test_manual_processing_decision_is_queued_once(self):
        self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only'})
        self._post_json('/api/notification/', {'enabled': True})
        with mock.patch('fruit_app.detection.execute_model_batch') as execute:
            def missing_roi(image_paths, model_paths, **kwargs):
                reports, artifacts = self._external_result(image_paths, model_paths, **kwargs)
                reports[0]['roi'] = {'status': 'error', 'reason': 'roi_not_detected'}
                reports[0]['models'] = {
                    role: {'status': 'skipped', 'reason': 'roi_unavailable'}
                    for role in ('color', 'wrinkle', 'defect')
                }
                artifacts[0] = {}
                return reports, artifacts
            execute.side_effect = missing_roi
            _fruit, state = self._run_photo_round()
        self.assertEqual(state['detection_result']['classification']['status'], 'needs_review')
        self.assertEqual(self._outbox_entries(), {})
        self.assertEqual(self._manual_grade(state, '加工').status_code, 200)
        self.assertEqual(self._manual_grade(state, '加工').status_code, 200)
        entries = self._outbox_entries()
        self.assertEqual(list(entries), [state['detection_result']['result_id']])
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['notification']['current']['status'], 'pending')
