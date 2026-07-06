import csv
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from . import views


class DataCollectionFlowTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self.temp_dir.name)
        self.settings_override = override_settings(DATASET_ROOT=self.dataset_root)
        self.settings_override.enable()
        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        self.client = Client()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()
        views.reset_runtime_state_for_tests()

    def test_manual_capture_upload_and_classify_writes_dataset(self):
        response = self._post_json('/api/set_counter/', {'start_id': 120})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_120')

        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fruit_id'], 'fruit_120')
        capture_token = response.json()['capture_token']
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_120').exists())

        response = self._upload_images('fruit_120', capture_token)
        self.assertEqual(response.status_code, 200)
        for index in range(1, 7):
            image_path = self.dataset_root / 'temp' / 'fruit_120' / f'img_{index:02d}.jpg'
            self.assertTrue(image_path.exists())

        response = self._post_json('/api/classify/', {
            'label': '上中等',
            'note': '表皮完整',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_120').exists())
        self.assertTrue((self.dataset_root / '上中等' / 'fruit_120' / 'img_06.jpg').exists())
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_121')

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fruit_id'], 'fruit_120')
        self.assertEqual(rows[0]['label'], '上中等')
        self.assertEqual(rows[0]['path'], '上中等/fruit_120')
        self.assertEqual(rows[0]['note'], '表皮完整')

        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 121)

    def test_esp32_trigger_creates_pending_capture_command(self):
        response = self._post_json('/api/set_counter/', {'start_id': 42})
        self.assertEqual(response.status_code, 200)

        response = self.client.post('/api/esp32_trigger/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['fruit_id'], 'fruit_042')
        self.assertEqual(payload['trigger_source'], 'esp32')
        self.assertTrue(payload['pending_capture'])
        self.assertEqual(payload['capture_interval_ms'], views.DEFAULT_CAPTURE_INTERVAL_MS)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_042').exists())

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        state_payload = response.json()
        self.assertEqual(state_payload['active_fruit_id'], 'fruit_042')
        self.assertTrue(state_payload['pending_capture'])
        self.assertEqual(state_payload['capture_token'], payload['capture_token'])
        self.assertFalse(state_payload['can_manual_capture'])

    def test_esp32_trigger_rejects_when_active_fruit_exists(self):
        first_response = self.client.post('/api/esp32_trigger/')
        self.assertEqual(first_response.status_code, 200)
        first_payload = first_response.json()
        self.assertEqual(first_payload['fruit_id'], 'fruit_001')

        second_response = self.client.post('/api/esp32_trigger/')
        self.assertEqual(second_response.status_code, 409)
        second_payload = second_response.json()
        self.assertFalse(second_payload['ok'])
        self.assertEqual(second_payload['status'], 'error')
        self.assertEqual(second_payload['reason'], 'active_fruit_exists')
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())

        state_response = self.client.get('/api/state/')
        self.assertEqual(state_response.status_code, 200)
        state_payload = state_response.json()
        self.assertEqual(state_payload['active_fruit_id'], 'fruit_001')
        self.assertEqual(state_payload['capture_token'], first_payload['capture_token'])

    def test_upload_failure_keeps_capture_pending_for_phone_retry(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        capture_token = response.json()['capture_token']

        with mock.patch.object(
            views,
            '_replace_temp_images',
            side_effect=views.DatasetFileBusyError('write locked'),
        ):
            response = self._upload_images(fruit_id, capture_token)
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload['reason'], 'upload_failed')

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        state_payload = response.json()
        self.assertEqual(state_payload['active_fruit_id'], fruit_id)
        self.assertEqual(state_payload['capture_token'], capture_token)
        self.assertEqual(state_payload['status'], 'waiting_camera')
        self.assertTrue(state_payload['pending_capture'])
        self.assertFalse(state_payload['can_manual_capture'])

    def test_uploading_state_blocks_destructive_actions(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']

        with views.STATE_LOCK:
            views.APP_STATE['status'] = 'uploading'
            views.APP_STATE['pending_capture'] = False

        discard_response = self._post_json('/api/discard/')
        self.assertEqual(discard_response.status_code, 409)
        reset_response = self._post_json('/api/reset_dataset/')
        self.assertEqual(reset_response.status_code, 409)
        self.assertTrue((self.dataset_root / 'temp' / fruit_id).exists())

        state_response = self.client.get('/api/state/')
        self.assertEqual(state_response.status_code, 200)
        state_payload = state_response.json()
        self.assertEqual(state_payload['status'], 'uploading')
        self.assertFalse(state_payload['can_discard'])
        self.assertFalse(state_payload['can_recapture'])

    def test_uploading_state_is_not_rewritten_as_incomplete(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        (self.dataset_root / 'temp' / fruit_id / 'img_01.jpg').write_bytes(b'partial-image')

        with views.STATE_LOCK:
            views.APP_STATE['status'] = 'uploading'
            views.APP_STATE['pending_capture'] = False

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'uploading')
        self.assertFalse(payload['pending_capture'])
        self.assertFalse(payload['can_discard'])

    def test_discard_removes_temp_without_metadata_or_counter_increment(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_005').exists())

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        discard_payload = response.json()
        self.assertEqual(discard_payload['discarded_fruit_id'], 'fruit_005')
        self.assertIsNone(discard_payload['active_fruit_id'])
        self.assertTrue(discard_payload['can_manual_capture'])
        self.assertFalse(discard_payload['can_discard'])
        self.assertFalse(discard_payload['can_classify'])
        self.assertEqual(discard_payload['latest_images'], [])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_005').exists())

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        state_payload = response.json()
        self.assertIsNone(state_payload['active_fruit_id'])
        self.assertTrue(state_payload['can_manual_capture'])

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [['fruit_id', 'label', 'capture_time', 'path', 'note']])

        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 5)

    def test_discard_uploaded_after_image_preview_releases_files(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        capture_token = response.json()['capture_token']

        response = self._upload_images(fruit_id, capture_token)
        self.assertEqual(response.status_code, 200)

        preview_response = self.client.get(f'/api/image/{fruit_id}/img_01.jpg/')
        self.assertEqual(preview_response.status_code, 200)
        self.assertGreater(len(preview_response.content), 0)

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertTrue(response.json()['can_manual_capture'])

    def test_empty_temp_folder_does_not_lock_manual_capture(self):
        stale_dir = self.dataset_root / 'temp' / 'fruit_001'
        stale_dir.mkdir(parents=True)

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(stale_dir.exists())
        self.assertIsNone(payload['active_fruit_id'])
        self.assertEqual(payload['image_total'], 0)
        self.assertTrue(payload['can_manual_capture'])
        self.assertFalse(payload['can_discard'])
        self.assertFalse(payload['can_classify'])

    def test_locked_empty_temp_folder_does_not_lock_manual_capture(self):
        stale_dir = self.dataset_root / 'temp' / 'fruit_001'
        stale_dir.mkdir(parents=True)

        with mock.patch.object(views, '_safe_rmtree', side_effect=views.DatasetFileBusyError('locked empty folder')):
            response = self.client.get('/api/state/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(stale_dir.exists())
            self.assertIsNone(payload['active_fruit_id'])
            self.assertEqual(payload['next_fruit_id'], 'fruit_001')
            self.assertTrue(payload['can_manual_capture'])
            self.assertFalse(payload['can_discard'])

            response = self._post_json('/api/manual_capture/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['fruit_id'], 'fruit_001')
            self.assertTrue(stale_dir.exists())

    def test_partial_temp_folder_allows_discard_but_not_classify(self):
        partial_dir = self.dataset_root / 'temp' / 'fruit_009'
        partial_dir.mkdir(parents=True)
        (partial_dir / 'img_01.jpg').write_bytes(b'partial-image')

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['active_fruit_id'], 'fruit_009')
        self.assertEqual(payload['status'], 'incomplete')
        self.assertEqual(payload['image_total'], 1)
        self.assertFalse(payload['can_manual_capture'])
        self.assertTrue(payload['can_discard'])
        self.assertFalse(payload['can_classify'])

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(partial_dir.exists())
        self.assertTrue(response.json()['can_manual_capture'])

    def test_recapture_keeps_fruit_id_and_increments_token(self):
        response = self._post_json('/api/manual_capture/', {'capture_interval_ms': 180})
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        first_token = response.json()['capture_token']

        partial_dir = self.dataset_root / 'temp' / fruit_id
        (partial_dir / 'img_01.jpg').write_bytes(b'partial-image')

        response = self._post_json('/api/recapture/', {'capture_interval_ms': 90})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['active_fruit_id'], fruit_id)
        self.assertEqual(payload['capture_token'], first_token + 1)
        self.assertEqual(payload['capture_interval_ms'], 90)
        self.assertFalse((partial_dir / 'img_01.jpg').exists())
        self.assertFalse(payload['can_manual_capture'])

    def test_reset_dataset_clears_all_data_and_counter(self):
        response = self._post_json('/api/set_counter/', {'start_id': 7})
        self.assertEqual(response.status_code, 200)
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        capture_token = response.json()['capture_token']
        response = self._upload_images(fruit_id, capture_token)
        self.assertEqual(response.status_code, 200)
        response = self._post_json('/api/classify/', {'label': '加工', 'note': 'reset test'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / '加工' / fruit_id).exists())

        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['reset_done'])
        self.assertEqual(payload['next_fruit_id'], 'fruit_001')
        self.assertTrue(payload['can_manual_capture'])
        self.assertFalse((self.dataset_root / '加工' / fruit_id).exists())

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [['fruit_id', 'label', 'capture_time', 'path', 'note']])
        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 1)

    def test_reset_dataset_resets_counter_even_when_empty_temp_delete_fails(self):
        self._post_json('/api/set_counter/', {'start_id': 9})
        stale_dir = self.dataset_root / 'temp' / 'fruit_001'
        stale_dir.mkdir(parents=True)

        with mock.patch.object(views, '_safe_rmtree', side_effect=views.DatasetFileBusyError('locked empty folder')):
            response = self._post_json('/api/reset_dataset/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload['reset_done'])
            self.assertEqual(payload['next_fruit_id'], 'fruit_001')
            self.assertIsNone(payload['active_fruit_id'])
            self.assertTrue(payload['can_manual_capture'])
            self.assertEqual(len(payload['delete_warnings']), 1)
            self.assertIn('_delete_pending', payload['delete_warnings'][0])

            response = self._post_json('/api/manual_capture/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['fruit_id'], 'fruit_001')

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [['fruit_id', 'label', 'capture_time', 'path', 'note']])
        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 1)

    def test_reset_dataset_removes_classified_folders_from_id_selection(self):
        for number in range(1, 5):
            fruit_dir = self.dataset_root / '上中等' / f'fruit_{number:03d}'
            fruit_dir.mkdir(parents=True)
            (fruit_dir / 'img_01.jpg').write_bytes(b'old-image')

        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['next_fruit_id'], 'fruit_001')
        self.assertTrue(payload['can_manual_capture'])
        for number in range(1, 5):
            self.assertFalse((self.dataset_root / '上中等' / f'fruit_{number:03d}').exists())

        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fruit_id'], 'fruit_001')

    def test_reset_dataset_ignores_leftover_when_delete_and_move_fail(self):
        stale_dir = self.dataset_root / '上中等' / 'fruit_001'
        stale_dir.mkdir(parents=True)
        (stale_dir / 'img_01.jpg').write_bytes(b'old-image')

        with (
            mock.patch.object(views, '_safe_rmtree', side_effect=views.DatasetFileBusyError('delete locked')),
            mock.patch.object(views, '_safe_move', side_effect=views.DatasetFileBusyError('move locked')),
        ):
            response = self._post_json('/api/reset_dataset/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload['next_fruit_id'], 'fruit_001')
            self.assertTrue(payload['can_manual_capture'])
            self.assertEqual(len(payload['delete_warnings']), 1)
            self.assertTrue(stale_dir.exists())

            response = self._post_json('/api/manual_capture/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['fruit_id'], 'fruit_001')

        with (self.dataset_root / 'reset_state.json').open('r', encoding='utf-8') as reset_file:
            reset_state = json.load(reset_file)
        self.assertEqual(reset_state['ignored_paths'], ['上中等/fruit_001'])

    def test_set_counter_rejects_valid_existing_id_but_allows_reset_leftover(self):
        valid_dir = self.dataset_root / '上中等' / 'fruit_001'
        valid_dir.mkdir(parents=True)
        (valid_dir / 'img_01.jpg').write_bytes(b'old-image')

        response = self._post_json('/api/set_counter/', {'start_id': 1})
        self.assertEqual(response.status_code, 409)

        (self.dataset_root / 'reset_state.json').write_text(
            json.dumps({
                'reset_at': 'test',
                'ignored_paths': ['上中等/fruit_001'],
                'moved_paths': [],
            }),
            encoding='utf-8',
        )
        response = self._post_json('/api/set_counter/', {'start_id': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_001')

    def test_discard_locked_empty_temp_folder_clears_active_state(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']

        with mock.patch.object(views, '_safe_rmtree', side_effect=views.DatasetFileBusyError('locked empty folder')):
            response = self._post_json('/api/discard/')
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload['discarded_fruit_id'], fruit_id)
            self.assertEqual(payload['delete_warnings'], ['locked empty folder'])
            self.assertIsNone(payload['active_fruit_id'])
            self.assertTrue(payload['can_manual_capture'])
            self.assertFalse(payload['can_discard'])

    def test_capture_timing_is_recorded(self):
        response = self._post_json('/api/manual_capture/', {'capture_interval_ms': 135})
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        capture_token = response.json()['capture_token']

        response = self._post_json('/api/capture_started/', {
            'fruit_id': fruit_id,
            'capture_token': capture_token,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()['timing']['command_to_phone_start_ms'])

        response = self._upload_images(
            fruit_id,
            capture_token,
            capture_meta={
                'timestamps_ms': [10, 145, 280, 420, 555, 690],
                'intervals_ms': [135, 135, 140, 135, 135],
            },
        )
        self.assertEqual(response.status_code, 200)
        timing = response.json()['timing']
        self.assertEqual(timing['capture_interval_ms'], 135)
        self.assertEqual(timing['phone_capture_intervals_ms'], [135.0, 135.0, 140.0, 135.0, 135.0])

    def test_classify_rejects_incomplete_capture_without_locking_ui(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        fruit_id = response.json()['fruit_id']
        (self.dataset_root / 'temp' / fruit_id / 'img_01.jpg').write_bytes(b'partial-image')

        response = self._post_json('/api/classify/', {'label': '上中等'})
        self.assertEqual(response.status_code, 409)

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'incomplete')
        self.assertTrue(payload['can_discard'])
        self.assertFalse(payload['can_classify'])

    def test_webrtc_signaling_round_trip(self):
        offer = {'type': 'offer', 'sdp': 'v=0\r\n'}
        answer = {'type': 'answer', 'sdp': 'v=0\r\n'}
        dashboard_candidate = {'candidate': 'candidate:dashboard', 'sdpMid': '0', 'sdpMLineIndex': 0}
        camera_candidate = {'candidate': 'candidate:camera', 'sdpMid': '0', 'sdpMLineIndex': 0}

        self.assertEqual(self._post_json('/api/webrtc/offer', {'offer': offer}).status_code, 200)
        self.assertEqual(self._post_json('/api/webrtc/ice', {
            'role': 'dashboard',
            'candidate': dashboard_candidate,
        }).status_code, 200)
        self.assertEqual(self._post_json('/api/webrtc/answer', {'answer': answer}).status_code, 200)
        self.assertEqual(self._post_json('/api/webrtc/ice', {
            'role': 'camera',
            'candidate': camera_candidate,
        }).status_code, 200)

        response = self.client.get('/api/webrtc/state')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['offer'], offer)
        self.assertEqual(payload['answer'], answer)
        self.assertEqual(payload['dashboard_ice'], [dashboard_candidate])
        self.assertEqual(payload['camera_ice'], [camera_candidate])

    def test_pages_render(self):
        home_response = self.client.get('/')
        self.assertEqual(home_response.status_code, 200)
        self.assertContains(home_response, '電腦 Dashboard')
        self.assertContains(home_response, '手機 Camera')

        dashboard_response = self.client.get('/dashboard/')
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, '不要開啟 /dashboard/')

        camera_response = self.client.get('/camera/')
        self.assertEqual(camera_response.status_code, 200)
        self.assertContains(camera_response, '開啟相機')
        self.assertContains(camera_response, '安全來源')
        self.assertContains(camera_response, 'isSecureContext')

    def _post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type='application/json',
        )

    def _fake_images(self):
        return [
            SimpleUploadedFile(
                f'source_{index:02d}.jpg',
                f'image-{index}'.encode('utf-8'),
                content_type='image/jpeg',
            )
            for index in range(1, 7)
        ]

    def _upload_images(self, fruit_id, capture_token, capture_meta=None):
        return self.client.post('/api/upload_images/', {
            'fruit_id': fruit_id,
            'capture_token': str(capture_token),
            'capture_meta': json.dumps(capture_meta or {}),
            'images': self._fake_images(),
        })
