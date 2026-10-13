"""Mode isolation through HTTP, using disposable datasets only."""

import json
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import tests as existing, views
from . import test_detection as detection_tests


class DatasetModeTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown
    _post_json = existing.DataCollectionFlowTests._post_json
    _fake_image = existing.DataCollectionFlowTests._fake_image
    _upload_station = existing.DataCollectionFlowTests._upload_station

    def _capture_three(self):
        self.client.get('/api/camera/state/?camera_ready=1')
        started = self._post_json('/api/photo_capture/').json()
        fruit_id = started['active_fruit_id']
        for station in (1, 2, 3):
            token = self.client.get('/api/camera/state/').json()['capture_token']
            self.assertEqual(self._upload_station(fruit_id, token, station).status_code, 200)
        return fruit_id

    def _select(self, mode):
        response = self._post_json('/api/collection_options/', {
            'work_mode': mode, 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_counters_reset_and_legacy_data_are_independent(self):
        base = Path(views.settings.DATASET_ROOT)
        legacy = base / 'temp' / 'fruit_001'
        legacy.mkdir(parents=True)
        (legacy / 'img_01.jpg').write_bytes(b'legacy')
        collection = self._select('collection')
        self.assertEqual(Path(collection['dataset_path']), base / 'collection')
        self.assertEqual(collection['next_fruit_id'], 'fruit_001')
        self.assertEqual(self._post_json('/api/set_counter/', {'start_id': 12}).status_code, 200)
        detected = self._select('detection')
        self.assertEqual(Path(detected['dataset_path']), base / 'detection')
        self.assertEqual(detected['next_fruit_id'], 'fruit_001')
        self.assertEqual(self._post_json('/api/set_counter/', {'start_id': 7}).status_code, 200)
        results = base / 'detection' / 'results' / 'fruit_006'
        results.mkdir(parents=True)
        (results / 'result.json').write_text('{}')
        self.assertEqual(self._post_json('/api/reset_dataset/').status_code, 200)
        self.assertFalse(results.exists())
        self.assertEqual(self._select('collection')['next_fruit_id'], 'fruit_012')
        self.assertEqual((legacy / 'img_01.jpg').read_bytes(), b'legacy')

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=detection_tests.DetectionFlowTests._external_result)
    def test_both_rounds_use_their_own_photos_results_and_cleanup(self, _execute):
        self._select('collection')
        collected = self._capture_three()
        token = self.client.get('/api/state/').json()['capture_token']
        classified = self._post_json('/api/classify/', {
            'fruit_id': collected, 'capture_token': token, 'label': '上等',
        })
        self.assertEqual(classified.status_code, 200, classified.content)
        photo = self.dataset_base / 'collection' / '上等' / collected / 'img_01.jpg'
        original = photo.read_bytes()
        metadata = (self.dataset_base / 'collection' / 'metadata.csv').read_bytes()
        self._select('detection')
        detected = self._capture_three()
        self.assertEqual(collected, detected)
        state = detection_tests.DetectionFlowTests._wait_for_detection(self)
        self.assertEqual(state['detection_status'], 'completed')
        run_dir = self.dataset_base / 'detection' / 'results' / detected
        result = json.loads((run_dir / 'result.json').read_text(encoding='utf-8'))
        url = state['detection_result']['images']['img_01.jpg']['artifact_urls']['original']
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self._post_json('/api/discard/', {'fruit_id': detected}).status_code, 200)
        self.assertFalse(run_dir.exists())
        self.assertEqual(self._post_json('/api/reset_dataset/').status_code, 200)
        views.reset_runtime_state_for_tests()
        restored = self.client.get('/api/state/').json()
        self.assertEqual(restored['work_mode'], 'detection')
        self.assertEqual(restored['next_fruit_id'], 'fruit_001')
        self.assertEqual(self._select('collection')['next_fruit_id'], 'fruit_002')
        self.assertIsNone(self.client.get('/api/state/').json()['detection_result'])
        self.assertEqual(photo.read_bytes(), original)
        self.assertEqual((self.dataset_base / 'collection' / 'metadata.csv').read_bytes(), metadata)
        self.assertEqual(result['fruit_id'], detected)

    def test_old_tab_cannot_reset_or_open_the_other_mode(self):
        self._select('detection')
        self._post_json('/api/set_counter/', {'start_id': 7})
        for url in ('/api/reset_dataset/', '/api/set_counter/', '/api/discard/', '/api/photo_capture/'):
            response = self._post_json(url, {'dataset_mode': 'collection', 'start_id': 1})
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()['reason'], 'stale_dataset_mode')
        response = self.client.get('/api/open_dataset_folder/?dataset_mode=collection')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get('/api/state/').json()['next_fruit_id'], 'fruit_007')
        with mock.patch.object(views.os, 'startfile', create=True) as open_folder:
            response = self.client.get('/api/open_dataset_folder/?dataset_mode=detection')
            self.assertEqual(Path(response.json()['path']), self.dataset_base / 'detection')
            open_folder.assert_called_once_with(self.dataset_base / 'detection')

    def test_same_number_images_are_scoped_and_old_result_is_not_current(self):
        for mode, data in (('collection', b'collection'), ('detection', b'detection')):
            folder = self.dataset_base / mode / '上等' / 'fruit_001'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'img_01.jpg').write_bytes(data)
        for mode in ('collection', 'detection'):
            self._select(mode)
            response = self.client.get(f'/api/image/fruit_001/img_01.jpg/?work_mode={mode}')
            self.assertEqual(b''.join(response.streaming_content), mode.encode())
            response.close()
            other = 'detection' if mode == 'collection' else 'collection'
            self.assertEqual(self.client.get(f'/api/image/fruit_001/img_01.jpg/?work_mode={other}').status_code, 404)
        self._select('collection')
        self.assertEqual(self.client.get('/api/detection/image/fruit_001/img_01_original.jpg/').status_code, 404)

    def test_failed_mode_save_preserves_current_mode_and_restart(self):
        self._select('detection')
        with mock.patch.object(views.os, 'replace', side_effect=PermissionError('occupied')):
            response = self._post_json('/api/collection_options/', {
                'work_mode': 'collection', 'hardware_mode': 'photo_only',
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['reason'], 'dataset_options_persist_failed')
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['work_mode'], 'detection')
        self.assertEqual(state['hardware_mode'], 'photo_only')

    def test_partial_detection_recovers_in_its_namespace_and_blocks_switch(self):
        self._select('detection')
        self.client.get('/api/camera/state/?camera_ready=1')
        started = self._post_json('/api/photo_capture/').json()
        fruit_id = started['active_fruit_id']
        self.assertEqual(self._upload_station(fruit_id, started['capture_token'], 1).status_code, 200)
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['work_mode'], 'detection')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertEqual(state['status'], 'incomplete')
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 409)
        self.assertTrue((self.dataset_base / 'detection' / 'temp' / fruit_id / 'img_01.jpg').is_file())

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=detection_tests.DetectionFlowTests._external_result)
    def test_completed_detection_recovers_its_result_and_next_number(self, _execute):
        self._select('detection')
        fruit_id = self._capture_three()
        detection_tests.DetectionFlowTests._wait_for_detection(self)
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['work_mode'], 'detection')
        self.assertEqual(state['detection_result']['fruit_id'], fruit_id)
        self.assertEqual(state['detection_status'], 'completed')
        self.assertEqual(state['next_fruit_id'], 'fruit_002')
        self.assertTrue(state['can_discard'])
        self.assertEqual(state['motor_command']['command'], 'none')

    def test_occupied_detection_reset_cannot_serve_or_overwrite_leftovers(self):
        self._select('detection')
        result_dir = self.dataset_base / 'detection' / 'results' / 'fruit_001'
        result_dir.mkdir(parents=True)
        old_photo = result_dir / 'img_01_original.jpg'
        old_photo.write_bytes(b'occupied')
        collection_counter = (self.dataset_base / 'collection' / 'counter.json').read_bytes()
        with mock.patch.object(views.shutil, 'rmtree', side_effect=PermissionError('occupied')), \
                mock.patch.object(views.shutil, 'move', side_effect=PermissionError('occupied')):
            reset = self._post_json('/api/reset_dataset/')
            self.assertEqual(reset.status_code, 200, reset.content)
            self.assertTrue(reset.json()['delete_warnings'])
            self.assertEqual(self.client.get('/api/detection/image/fruit_001/img_01_original.jpg/').status_code, 404)
            self.client.get('/api/camera/state/?camera_ready=1')
            start = self._post_json('/api/photo_capture/')
            self.assertEqual(start.status_code, 409)
            self.assertEqual(start.json()['reason'], 'reset_leftover_busy')
        self.assertEqual(old_photo.read_bytes(), b'occupied')
        self.assertEqual((self.dataset_base / 'collection' / 'counter.json').read_bytes(), collection_counter)

    def test_corrupt_mode_record_stops_mutation_without_touching_data(self):
        path = self.motor_command_sequence_path.with_name('dataset_options.json')
        path.write_text('{broken', encoding='utf-8')
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['auto_run_recovery_reason'], 'dataset_options_invalid')
        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'dataset_options_invalid')
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_corrupt_mode_record_survives_a_later_recovery_lock(self):
        path = self.motor_command_sequence_path.with_name('dataset_options.json')
        path.write_text('{broken', encoding='utf-8')
        views.reset_runtime_state_for_tests()
        self.client.get('/api/state/')
        with views.STATE_LOCK:
            views.APP_STATE['auto_run_recovery_reason'] = 'gate3_manual_removal_required'
        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'dataset_options_invalid')
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
