"""Photo-only collection contracts through HTTP and real temporary files."""
import csv

from django.test import SimpleTestCase
from . import tests as existing


class PhotoOnlyFlowTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown
    def _post_json(self, url, payload=None):
        if url in ('/api/classify/', '/api/discard/'):
            state = self.client.get('/api/state/').json()
            payload = {'fruit_id': state['active_fruit_id'], 'capture_token': state['capture_token'], **(payload or {})}
        return existing.DataCollectionFlowTests._post_json(self, url, payload)

    _fake_image = existing.DataCollectionFlowTests._fake_image
    _upload_station = existing.DataCollectionFlowTests._upload_station

    def test_offline_collection_saves_three_images_and_classifies_once(self):
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['operator_alert'])
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(self._post_json('/api/photo_capture/').status_code, 200)
        for station in (1, 2, 3):
            state = self.client.get('/api/camera/state/').json()
            self.assertTrue(state['capture_requested'])
            self.assertEqual(state['station_index'], station)
            response = self._upload_station('fruit_001', state['capture_token'], station)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['motor_command']['command'], 'none')
            self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / f'img_{station:02d}.jpg').exists())
        self.assertEqual(response.json()['status'], 'uploaded')
        self.assertTrue(response.json()['can_classify'])
        response = self._post_json('/api/classify/', {'label': '上等', 'note': '純拍攝'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['sorter_command_queued'])
        self.assertTrue((self.dataset_root / '上等' / 'fruit_001' / 'img_03.jpg').exists())
        self.assertFalse((self.dataset_root / '上等' / 'fruit_001' / '.capture-session.json').exists())
        self.assertEqual(self._post_json('/api/classify/', {'label': '上等'}).status_code, 409)
        with (self.dataset_root / 'metadata.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['capture_count'], '3')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'idle')
        self.assertIsNone(state['active_fruit_id'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertFalse(self.motor_command_sequence_path.exists())

    def select_photo_only(self):
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 200)

    def start_photo_only(self):
        self.select_photo_only()
        self.client.get('/api/camera/state/?camera_ready=1')
        response = self._post_json('/api/photo_capture/')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_mode_and_camera_guards_do_not_create_partial_sessions(self):
        self.assertEqual(self._post_json('/api/photo_capture/').json()['reason'], 'photo_only_required')
        self.select_photo_only()
        self.assertEqual(self._post_json('/api/photo_capture/').json()['reason'], 'camera_not_ready')
        self.assertEqual(list((self.dataset_root / 'temp').iterdir()), [])
        self.assertEqual(self._post_json('/api/auto_run/', {'enabled': True}).status_code, 409)
        self.assertEqual(self._post_json('/api/feeder/test/').status_code, 409)
        self.assertEqual(self._post_json('/api/manual_capture/').status_code, 404)
        detection = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(detection.status_code, 200)
        self.assertEqual(detection.json()['work_mode'], 'detection')
        from unittest import mock
        with mock.patch('fruit_app.views.time.monotonic', return_value=100):
            self.client.get('/api/camera/state/?camera_ready=1')
        with mock.patch('fruit_app.views.time.monotonic', return_value=106):
            self.assertEqual(self._post_json('/api/photo_capture/').json()['reason'], 'camera_not_ready')
        self.assertEqual(list((self.dataset_root / 'temp').iterdir()), [])

    def test_duplicate_start_upload_and_option_change_cannot_advance_session(self):
        state = self.start_photo_only()
        token = state['capture_token']
        self.assertEqual(self._post_json('/api/photo_capture/').json()['reason'], 'active_fruit_exists')
        self.assertEqual(self._post_json('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'hardware',
        }).status_code, 409)
        self.assertEqual(self._upload_station('fruit_001', token, 2).status_code, 409)
        self.assertEqual(self._upload_station('fruit_999', token, 1).status_code, 409)
        self.assertEqual(self._upload_station('fruit_001', token + 1, 1).status_code, 409)
        self.assertEqual(self._upload_station('fruit_001', token, 1).status_code, 200)
        image = self.dataset_root / 'temp' / 'fruit_001' / 'img_01.jpg'
        original = image.read_bytes()
        self.assertEqual(self._upload_station('fruit_001', token, 1).status_code, 409)
        self.assertEqual(image.read_bytes(), original)
        self.assertEqual(self.client.get('/api/camera/state/').json()['station_index'], 2)
        self.assertEqual(self._post_json('/api/classify/', {'label': '中等'}).status_code, 409)

    def test_hardware_events_and_restarts_cannot_change_photo_session(self):
        state = self.start_photo_only()
        for station in (1, 2, 3):
            for event in ('hcsr04_trigger', 'hcsr04_station_1_ready', 'station_1_ready',
                          'station_2_ready', 'station_3_ready', 'capture_sequence_finished',
                          'motor_error', 'classification_sorter_completed', 'feeder_max_run_timeout'):
                response = self._post_json('/api/esp32/report/', {'event': event, 'trigger_id': 'late', 'command_id': 1})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['ignored'])
            self.assertEqual(self._post_json('/api/esp32_trigger/').status_code, 409)
            command = self.client.get('/api/esp32/command/', {'boot_id': f'boot-{station}'}).json()
            self.assertEqual(command['command'], 'none')
            self.assertEqual(command['auto_trigger_enabled'], 0)
            camera = self.client.get('/api/camera/state/').json()
            self.assertEqual(camera['station_index'], station)
            self.assertEqual(camera['capture_token'], state['capture_token'])
            self.assertEqual(self._upload_station('fruit_001', camera['capture_token'], station).status_code, 200)
            state = self.client.get('/api/state/').json()
        self.client.get('/api/esp32/command/', {'boot_id': 'after-upload'})
        state = self.client.get('/api/state/').json()
        self.assertTrue(state['can_classify'])
        self.assertIsNone(state['last_error_reason'])
        self.assertFalse(self.motor_command_sequence_path.exists())

    def test_failed_atomic_upload_retries_same_token_without_advancing(self):
        from unittest import mock
        state = self.start_photo_only()
        with mock.patch('pathlib.Path.replace', side_effect=PermissionError('disk busy')):
            response = self._upload_station('fruit_001', state['capture_token'], 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'upload_failed')
        camera = self.client.get('/api/camera/state/').json()
        self.assertEqual(camera['station_index'], 1)
        self.assertEqual(camera['capture_token'], state['capture_token'])
        self.assertEqual(list((self.dataset_root / 'temp' / 'fruit_001').glob('*.jpg')), [])
        self.assertEqual(self._upload_station('fruit_001', camera['capture_token'], 1).status_code, 200)

    def test_camera_timeout_preserves_partial_images_until_discard(self):
        from unittest import mock
        state = self.start_photo_only()
        self._upload_station('fruit_001', state['capture_token'], 1)
        now = existing.views.time.monotonic()
        with mock.patch('fruit_app.views.time.monotonic', return_value=now + 46):
            state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['last_error_reason'], 'camera_upload_timeout')
        self.assertFalse(state['can_classify'])
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / 'img_01.jpg').exists())
        self.assertEqual(self._post_json('/api/discard/').status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'idle')
        self.assertIsNone(state['auto_run_recovery_reason'])
        self.assertFalse(state['pending_capture'])

    def test_discarded_fruit_id_does_not_reuse_upload_token(self):
        state = self.start_photo_only()
        token = state['capture_token']
        self._post_json('/api/discard/')
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(self._post_json('/api/photo_capture/').status_code, 200)
        self.assertEqual(self._upload_station('fruit_001', token, 1).status_code, 409)

    def test_switching_never_clears_runtime_or_recovery_locks(self):
        cases = (
            {'auto_run_enabled': True}, {'auto_run_finishing': True},
            {'motor_command': {'command': 'feed_one', 'command_id': 1}},
            {'sorter_recovery_required': True}, {'auto_run_recovery_reason': 'feed_arrival_unconfirmed'},
            {'status': 'waiting_fruit'}, {'sorter_status': 'pending'},
            {'dataset_operation': {'token': 1, 'kind': 'classify', 'fruit_id': 'fruit_001'}},
        )
        for lock in cases:
            with self.subTest(lock=lock):
                existing.views.reset_runtime_state_for_tests()
                existing.views.APP_STATE.update(lock)
                response = self._post_json('/api/collection_options/', {
                    'work_mode': 'collection', 'hardware_mode': 'photo_only',
                })
                self.assertEqual(response.status_code, 409)
                for key, value in lock.items():
                    self.assertEqual(existing.views.APP_STATE[key], value)
                self.assertEqual(existing.views.APP_STATE['hardware_mode'], 'hardware')

    def test_esp32_reboot_during_atomic_write_does_not_interrupt_upload(self):
        from unittest import mock
        from pathlib import Path
        state = self.start_photo_only()
        real_replace = Path.replace
        def reboot_then_replace(source, target):
            self.client.get('/api/esp32/command/', {'boot_id': 'reboot-during-save'})
            self.assertEqual(self._post_json('/api/collection_options/', {
                'work_mode': 'collection', 'hardware_mode': 'hardware',
            }).status_code, 409)
            self.assertEqual(self._post_json('/api/photo_capture/').json()['reason'], 'camera_busy')
            self.assertEqual(self._upload_station('fruit_001', state['capture_token'], 1).status_code, 409)
            return real_replace(source, target)
        with mock.patch('pathlib.Path.replace', reboot_then_replace):
            response = self._upload_station('fruit_001', state['capture_token'], 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['active_station_index'], 2)
        self.assertEqual(response.json()['motor_command']['command'], 'none')

    def test_delayed_classification_and_discard_cannot_affect_new_session(self):
        state = self.start_photo_only()
        stale = {'fruit_id': state['active_fruit_id'], 'capture_token': state['capture_token']}
        self._post_json('/api/discard/')
        self.client.get('/api/camera/state/?camera_ready=1')
        self._post_json('/api/photo_capture/')
        self.assertEqual(self._post_json('/api/discard/', stale).json()['reason'], 'stale_capture')
        for station in (1, 2, 3):
            camera = self.client.get('/api/camera/state/').json()
            self._upload_station('fruit_001', camera['capture_token'], station)
        response = self._post_json('/api/classify/', {**stale, 'label': '上等'})
        self.assertEqual(response.json()['reason'], 'stale_capture')
        self.assertTrue(self.client.get('/api/state/').json()['can_classify'])

    def test_switch_back_preserves_hardware_requirements(self):
        self.select_photo_only()
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'hardware',
        })
        self.assertEqual(response.status_code, 200)
        self.client.get('/api/camera/state/?camera_ready=1')
        response = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(response.json()['reason'], 'esp32_offline')
        self.assertFalse(self.motor_command_sequence_path.exists())

    def test_restart_restores_photo_only_session_without_hardware_requirements(self):
        self.start_photo_only()
        for station in (1, 2, 3):
            camera = self.client.get('/api/camera/state/').json()
            self._upload_station('fruit_001', camera['capture_token'], station)
        existing.views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['hardware_mode'], 'photo_only')
        self.assertTrue(state['can_classify'])
        response = self._post_json('/api/classify/', {'label': '加工'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['hardware_skipped'])

    def test_photo_token_is_not_reused_after_hardware_cycle(self):
        state = self.start_photo_only()
        token = state['capture_token']
        self._post_json('/api/discard/')
        self._post_json('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'hardware'})
        self._post_json('/api/esp32_trigger/')
        self._post_json('/api/discard/')
        self.start_photo_only()
        self.assertEqual(self._upload_station('fruit_001', token, 1).status_code, 409)

    def test_session_marker_write_failure_leaves_no_active_fruit(self):
        from unittest import mock
        self.select_photo_only()
        self.client.get('/api/camera/state/?camera_ready=1')
        with mock.patch('fruit_app.dataset_store.save_photo_session', side_effect=PermissionError('disk busy')):
            response = self._post_json('/api/photo_capture/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['reason'], 'capture_options_persist_failed')
        self.assertIsNone(self.client.get('/api/state/').json()['active_fruit_id'])
        self.assertEqual(list((self.dataset_root / 'temp').iterdir()), [])
