"""No unexpected error or hung job may leave Django refusing every dataset action."""

import json
from unittest import mock

from django.test import SimpleTestCase

from . import dataset_store, local_network, views
from . import tests as existing
from .runtime_state import RuntimeState


class ReleasingDatasetOperationTests(SimpleTestCase):
    def test_releases_own_token_on_error_and_leaves_newer_operation(self):
        state = RuntimeState({'dataset_operation': None})
        token = state.begin_dataset_operation('classify', 'fruit_001')
        with self.assertRaises(ValueError), state.releasing_dataset_operation(token):
            raise ValueError('boom')
        self.assertIsNone(state['dataset_operation'])

        stale = state.begin_dataset_operation('discard', 'fruit_001')
        state.finish_dataset_operation(stale)
        newer = state.begin_dataset_operation('reset', None)
        with state.releasing_dataset_operation(stale):
            pass
        self.assertTrue(state.operation_matches(newer))


class DeadlockRecoveryTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown
    _post_json = existing.DataCollectionFlowTests._post_json
    _mark_esp32_online = existing.DataCollectionFlowTests._mark_esp32_online
    _report = existing.DataCollectionFlowTests._report
    _esp32_command = existing.DataCollectionFlowTests._esp32_command
    _esp32_poll_params = existing.DataCollectionFlowTests._esp32_poll_params
    _fake_image = existing.DataCollectionFlowTests._fake_image
    _start_capture_and_ready_station_1 = existing.DataCollectionFlowTests._start_capture_and_ready_station_1

    def _uploaded_fruit(self, status='uploaded'):
        fruit_dir = self.dataset_root / 'temp' / 'fruit_001'
        fruit_dir.mkdir(parents=True)
        for filename in views.IMAGE_FILENAMES:
            (fruit_dir / filename).write_bytes(b'jpeg')
        with views.STATE_LOCK:
            views.APP_STATE['active_fruit_id'] = 'fruit_001'
            views.APP_STATE['status'] = status
            views.APP_STATE['capture_time'] = views._now_string()
            views.APP_STATE['gate3_waiting_boot_id'] = views.APP_STATE.get('esp32_boot_id')
        return fruit_dir

    def test_classify_releases_operation_after_unexpected_error(self):
        self._mark_esp32_online()
        self._uploaded_fruit()
        with mock.patch.object(dataset_store, 'append_metadata',
                               side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, 'bad')), \
                self.assertRaises(UnicodeDecodeError):
            self._post_json('/api/classify/', {'label': '上等'})
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))

    def test_discard_releases_operation_after_unexpected_error_and_can_retry(self):
        self._uploaded_fruit(status='incomplete')
        with mock.patch.object(dataset_store, 'discard_temp_fruit', side_effect=ValueError('bad csv')), \
                self.assertRaises(ValueError):
            self._post_json('/api/discard/')
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))
        self.assertEqual(self._post_json('/api/discard/').status_code, 200)

    def test_non_object_capture_meta_does_not_strand_saved_photo(self):
        self._start_capture_and_ready_station_1()
        response = self.client.post('/api/upload_images/', {
            'fruit_id': views.APP_STATE['active_fruit_id'],
            'capture_token': str(views.APP_STATE['capture_token']),
            'station_index': '1',
            'capture_meta': json.dumps([]),
            'image': self._fake_image(1),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(views.APP_STATE['status'], 'waiting_motor')

    def test_stuck_upload_times_out_into_discardable_error(self):
        self._uploaded_fruit(status='uploading')
        views.APP_STATE['upload_started_monotonic'] = (
            views.time.monotonic() - views.UPLOAD_SAVE_TIMEOUT_SECONDS - 1)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(views.APP_STATE['last_error_reason'], 'upload_timeout')

    def test_hung_detection_times_out_and_ignores_late_result(self):
        self._uploaded_fruit(status='detection_running')
        with views.STATE_LOCK:
            token = views.APP_STATE.begin_dataset_operation('detection', 'fruit_001')
            views.APP_STATE['detection_status'] = 'running'
            views.APP_STATE['detection_started_monotonic'] = (
                views.time.monotonic() - views.DETECTION_TIMEOUT_SECONDS - 1)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'detection_failed')
        self.assertEqual(views.APP_STATE['detection_error'], 'detection_timeout')
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))
        self.assertFalse(views.APP_STATE.operation_matches(token))

    def test_crashed_detection_job_fails_round_and_releases_operation(self):
        self._uploaded_fruit(status='detection_pending')
        with views.STATE_LOCK:
            token = views.APP_STATE.begin_dataset_operation('detection', 'fruit_001')
        with mock.patch.object(views, '_finish_detection_job', side_effect=KeyError('entries')),                 self.assertLogs('fruit_app.views', level='ERROR'):
            views._run_detection_job(token, 'fruit_001', self.dataset_root / 'temp' / 'fruit_001', {})
        self.assertEqual(views.APP_STATE['status'], 'detection_failed')
        self.assertEqual(views.APP_STATE['detection_error'], 'detection_job_crashed')
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))

    def test_runtime_reset_clears_state_and_network_but_keeps_photos(self):
        fruit_dir = self._uploaded_fruit(status='uploading')
        marker = views.NETWORK_RECOVERY.path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'generation': 1, 'reason': 'esp32_connection_lost'}), encoding='utf-8')
        views.APP_STATE.begin_dataset_operation('detection', 'fruit_001')

        rejected = self.client.post('/api/runtime/reset/', REMOTE_ADDR='192.168.50.20')
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()['reason'], 'network_local_dashboard_required')

        response = self.client.post('/api/runtime/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['runtime_reset'])
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertFalse(marker.exists())
        self.assertFalse(local_network.NETWORK.snapshot()['selected'])
        self.assertEqual(len(list(fruit_dir.iterdir())), len(views.IMAGE_FILENAMES))
        self.assertEqual(views.APP_STATE['active_fruit_id'], 'fruit_001')
        self.assertEqual(views.APP_STATE['status'], 'uploaded')
