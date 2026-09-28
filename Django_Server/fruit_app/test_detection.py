"""Automatic-detection contracts through HTTP and real temporary files."""

import json
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from . import tests as existing
from . import views


class DetectionModelSettingsTests(SimpleTestCase):
    def test_default_weights_use_fixed_django_model_assets(self):
        model_root = Path(settings.BASE_DIR) / 'models'
        self.assertEqual(Path(settings.DETECTION_MODEL_ROOT), model_root)
        self.assertEqual(settings.DETECTION_MODEL_PATHS, {
            'roi': str(model_root / 'ROI.pt'),
            'color': str(model_root / 'Color.pt'),
            'wrinkle': str(model_root / 'Wrinkle.pt'),
            'defect': str(model_root / 'Defect.pt'),
        })


class DetectionFlowTests(SimpleTestCase):
    def setUp(self):
        existing.DataCollectionFlowTests.setUp(self)
        self.detection_root = self.dataset_root / 'detection-results'
        model_root = self.dataset_root / 'model-repository'
        paths = {
            role: model_root / 'models' / f'{role}.pt'
            for role in ('roi', 'color', 'wrinkle', 'defect')
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.detection_settings = override_settings(
            DETECTION_OUTPUT_ROOT=self.detection_root,
            MODEL_REPOSITORY_ROOT=model_root,
            DETECTION_MODEL_PATHS={
                role: str(path) for role, path in paths.items()
            },
        )
        self.detection_settings.enable()

    def tearDown(self):
        self._wait_for_detection()
        self.detection_settings.disable()
        existing.DataCollectionFlowTests.tearDown(self)

    def _post_json(self, url, payload=None):
        return existing.DataCollectionFlowTests._post_json(self, url, payload)

    _fake_image = existing.DataCollectionFlowTests._fake_image
    _upload_station = existing.DataCollectionFlowTests._upload_station
    _prepare_auto_run = existing.DataCollectionFlowTests._prepare_auto_run
    _esp32_command = existing.DataCollectionFlowTests._esp32_command
    _esp32_poll_params = existing.DataCollectionFlowTests._esp32_poll_params
    _report = existing.DataCollectionFlowTests._report
    _report_feed_success = existing.DataCollectionFlowTests._report_feed_success

    def _start_detection(self, hardware_mode='photo_only'):
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': hardware_mode,
        })
        if hardware_mode != 'photo_only':
            return response
        self.assertEqual(response.status_code, 200)
        self.client.get('/api/camera/state/?camera_ready=1')
        response = self._post_json('/api/photo_capture/')
        self.assertEqual(response.status_code, 200)
        return response

    def _upload_three(self, fruit_id):
        originals = []
        for station in (1, 2, 3):
            camera = self.client.get('/api/camera/state/').json()
            response = self._upload_station(
                fruit_id, camera['capture_token'], station
            )
            self.assertEqual(response.status_code, 200)
            originals.append(
                (self.dataset_root / 'temp' / fruit_id /
                 f'img_{station:02d}.jpg').read_bytes()
            )
        return originals

    def _wait_for_detection(self, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.client.get('/api/state/').json()
            if state.get('detection_status') not in ('pending', 'running'):
                return state
            time.sleep(0.01)
        self.fail('Detection did not reach a terminal state.')

    @staticmethod
    def _external_result(image_paths, _model_paths, **_kwargs):
        reports = []
        artifacts = []
        for path in image_paths:
            reports.append({
                'ok': True, 'source': str(path),
                'roi': {'status': 'ok', 'confidence': 0.98},
                'models': {
                    'color': {'status': 'ok', 'class_name': 'good_color',
                              'confidence': 0.91},
                    'wrinkle': {'status': 'ok',
                                'class_name': 'false(沒皺褶)',
                                'confidence': 0.87},
                    'defect': {'status': 'ok', 'detections': [],
                               'area_ratios': {}, 'max_confidences': {}},
                },
            })
            image = np.full((24, 32, 3), 128, dtype=np.uint8)
            artifacts.append({
                'roi_annotated': image, 'masked_roi': image,
                'wrinkle_gray': image[:, :, 0],
                'defect_annotated': image,
            })
        return reports, artifacts

    def test_detection_supports_both_hardware_modes_and_hides_classification(self):
        response = self._start_detection('hardware')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['hardware_mode'], 'hardware')
        self.assertFalse(response.json()['can_classify'])
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['work_mode'], 'detection')
        self.assertFalse(response.json()['can_classify'])

    def _start_hardware_detection(self):
        self._prepare_auto_run(camera_ready=True)
        selected = self._start_detection('hardware')
        self.assertEqual(selected.status_code, 200)
        started = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(started.status_code, 200)
        feed = started.json()['motor_command']
        self.assertEqual(feed['command'], 'feed_one')
        self.assertEqual(self._report_feed_success(feed).status_code, 200)
        triggered = self._report('hcsr04_trigger')
        self.assertEqual(triggered.status_code, 200)
        return triggered.json()['fruit_id']

    def _upload_hardware_stations(self, fruit_id):
        for station in (1, 2, 3):
            command = self._esp32_command()
            self.assertEqual(
                command['command'],
                'start_sequence' if station == 1 else 'release_gate',
            )
            ready = self._report(
                f'station_{station}_ready',
                station_index=station,
                command_id=command['command_id'],
            )
            self.assertEqual(ready.status_code, 200)
            uploaded = self._upload_station(
                fruit_id, ready.json()['capture_token'], station,
            )
            self.assertEqual(uploaded.status_code, 200)
        return uploaded

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_hardware_detection_is_single_round_and_keeps_gate3_locked(self, execute):
        fruit_id = self._start_hardware_detection()
        third = self._upload_hardware_stations(fruit_id)
        self.assertIn(third.json()['detection_status'], ('pending', 'running'))

        state = self._wait_for_detection()
        self.assertEqual(state['detection_status'], 'completed')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertTrue(state['gate3_manual_removal_required'])
        self.assertEqual(
            state['operator_alert']['reason'], 'gate3_manual_removal_required'
        )
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertEqual(execute.call_count, 1)

        duplicate = self._report('capture_sequence_finished')
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()['ignored'])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(
            self._post_json('/api/auto_run/', {'enabled': True}).json()['reason'],
            'active_fruit_exists',
        )
        self.assertEqual(
            self._post_json('/api/collection_options/', {
                'work_mode': 'detection', 'hardware_mode': 'photo_only',
            }).json()['reason'],
            'active_fruit_exists',
        )

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_partial_hardware_detection_keeps_gate3_locked(self, execute):
        reports, artifacts = self._external_result(
            [Path(f'img_{index:02d}.jpg') for index in range(1, 4)], {}
        )
        reports[0]['models']['defect'] = {
            'status': 'failed', 'reason': 'inference_failed',
        }
        artifacts[0].pop('defect_annotated')
        execute.return_value = reports, artifacts

        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        state = self._wait_for_detection()

        self.assertEqual(state['detection_status'], 'failed')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertTrue(state['gate3_manual_removal_required'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_hardware_detection_recovery_requires_explicit_confirmation(self, _execute):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self._wait_for_detection()

        discarded = self._post_json('/api/discard/', {
            'fruit_id': fruit_id,
            'capture_token': views.APP_STATE['capture_token'],
        })
        self.assertEqual(discarded.status_code, 200)
        self.assertTrue(views._gate3_recovery_path().exists())
        self.assertEqual(
            discarded.json()['auto_run_recovery_reason'],
            'gate3_manual_removal_required',
        )
        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'gate3_manual_removal_required')

        recovered = self._post_json('/api/auto_run/', {
            'enabled': True, 'recovery_confirmed': True,
        })
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()['motor_command']['command'], 'feed_one')
        self.assertFalse(views._gate3_recovery_path().exists())

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_restart_after_completed_hardware_detection_keeps_gate3_locked(
        self, _execute
    ):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self.assertEqual(self._wait_for_detection()['detection_status'], 'completed')

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        state = self.client.get('/api/state/').json()

        self.assertEqual(state['detection_status'], 'completed')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertTrue(state['gate3_manual_removal_required'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_restart_after_discard_keeps_persisted_gate3_recovery_lock(
        self, _execute
    ):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self._wait_for_detection()
        discarded = self._post_json('/api/discard/', {
            'fruit_id': fruit_id,
            'capture_token': views.APP_STATE['capture_token'],
        })
        self.assertEqual(discarded.status_code, 200)
        self.assertTrue(views._gate3_recovery_path().exists())

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        state = self.client.get('/api/state/').json()
        self.assertEqual(
            state['auto_run_recovery_reason'], 'gate3_manual_removal_required'
        )
        self.assertEqual(state['motor_command']['command'], 'none')
        switched = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(switched.status_code, 409)
        self.assertEqual(switched.json()['reason'], 'gate3_manual_removal_required')
        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'gate3_manual_removal_required')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_three_uploads_run_once_and_save_complete_result(self, execute):
        started = self._start_detection().json()
        fruit_id = started['active_fruit_id']
        original_bytes = self._upload_three(fruit_id)

        state = self._wait_for_detection()
        self.assertEqual(state['detection_status'], 'completed')
        self.assertTrue(state['detection_result']['complete'])
        self.assertFalse(state['can_classify'])
        self.assertEqual(execute.call_count, 1)
        self.client.get('/api/state/')
        self.assertEqual(execute.call_count, 1)

        run_dir = self.detection_root / fruit_id
        result = json.loads((run_dir / 'result.json').read_text('utf-8'))
        self.assertFalse((self.detection_root / '.current.json').exists())
        self.assertEqual(len(result['images']), 3)
        self.assertNotIn('capture_time', result)
        self.assertNotIn('model_paths', json.dumps(result))
        for index, source_bytes in enumerate(original_bytes, start=1):
            name = f'img_{index:02d}.jpg'
            image = result['images'][name]
            self.assertEqual(
                (run_dir / image['artifacts']['original']).read_bytes(),
                source_bytes,
            )
            self.assertEqual(
                list(image['artifacts']),
                ['original', 'roi_annotated', 'masked_roi',
                 'wrinkle_gray', 'defect_annotated'],
            )

        self.client.get('/api/camera/state/?camera_ready=1')
        next_started = self._post_json('/api/photo_capture/').json()
        self.assertNotEqual(next_started['active_fruit_id'], fruit_id)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_completed_photo_detection_can_clear_temp_without_deleting_result(
        self, _execute
    ):
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        state = self._wait_for_detection()

        self.assertTrue(state['can_discard'])
        self.assertEqual(state['discardable_fruit_id'], fruit_id)
        response = self._post_json('/api/discard/', {'fruit_id': fruit_id})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertTrue((self.detection_root / fruit_id / 'result.json').is_file())

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_skipped_model_is_incomplete(self, execute):
        reports, artifacts = self._external_result(
            [Path(f'img_{index:02d}.jpg') for index in range(1, 4)], {}
        )
        reports[1]['models']['wrinkle'] = {
            'status': 'skipped', 'reason': 'model_missing',
        }
        artifacts[1].pop('wrinkle_gray')
        execute.return_value = reports, artifacts

        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        state = self._wait_for_detection()

        self.assertEqual(state['detection_status'], 'failed')
        self.assertFalse(state['detection_result']['complete'])
        image = state['detection_result']['images']['img_02.jpg']
        self.assertEqual(image['models']['wrinkle']['status'], 'skipped')
        self.assertNotIn('wrinkle_gray', image['artifacts'])
        self.assertIn('masked_roi', image['artifacts'])
        self.assertIn('檢測未完整完成', state['message'])

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_running_detection_keeps_api_responsive_and_rejects_changes(
        self, execute
    ):
        release = threading.Event()

        def blocked(image_paths, model_paths, **kwargs):
            release.wait(2)
            return self._external_result(image_paths, model_paths, **kwargs)

        execute.side_effect = blocked
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)

        state = self.client.get('/api/state/').json()
        self.assertIn(state['detection_status'], ('pending', 'running'))
        self.assertEqual(
            self._post_json('/api/photo_capture/').json()['reason'],
            'detection_in_progress',
        )
        self.assertEqual(
            self._post_json('/api/collection_options/', {
                'work_mode': 'collection', 'hardware_mode': 'photo_only',
            }).json()['reason'],
            'detection_in_progress',
        )
        self.assertEqual(self._post_json('/api/reset_dataset/').status_code, 409)
        release.set()
        self.assertEqual(
            self._wait_for_detection()['detection_status'], 'completed'
        )

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    @mock.patch(
        'fruit_app.detection._write_artifact',
        side_effect=OSError('disk full'),
    )
    def test_artifact_write_failure_never_claims_saved(
        self, _write, _execute
    ):
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        state = self._wait_for_detection()

        self.assertEqual(state['detection_status'], 'failed')
        self.assertFalse(state['detection_result']['complete'])
        self.assertEqual(
            state['detection_result']['reason'], 'artifact_save_failed'
        )
        run_dir = self.detection_root / fruit_id
        self.assertTrue((run_dir / 'img_01_original.jpg').is_file())
        self.assertTrue((run_dir / 'result.json').is_file())
        self.assertFalse((run_dir / 'img_01_masked_roi.jpg').exists())

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    @mock.patch(
        'fruit_app.detection._copy_original',
        side_effect=OSError('disk full'),
    )
    def test_original_write_failure_keeps_durable_failed_result(
        self, _copy, _execute
    ):
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        state = self._wait_for_detection()

        self.assertEqual(state['detection_status'], 'failed')
        self.assertFalse(state['detection_result']['complete'])
        self.assertEqual(
            state['detection_result']['reason'], 'artifact_save_failed'
        )
        result_path = self.detection_root / fruit_id / 'result.json'
        self.assertTrue(result_path.is_file())
        result = json.loads(result_path.read_text('utf-8'))
        self.assertNotIn('original', result['images']['img_01.jpg']['artifacts'])

    def test_existing_detection_output_reserves_fruit_id(self):
        (self.detection_root / 'fruit_001').mkdir(parents=True)
        started = self._start_detection().json()
        self.assertEqual(started['active_fruit_id'], 'fruit_002')

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_restart_during_detection_does_not_replay_or_claim_success(
        self, execute
    ):
        release = threading.Event()

        def blocked(image_paths, model_paths, **kwargs):
            release.wait(2)
            return self._external_result(image_paths, model_paths, **kwargs)

        execute.side_effect = blocked
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()
        self.assertEqual(state['detection_status'], 'failed')
        self.assertEqual(state['status'], 'detection_failed')
        self.assertIn('不自動重跑', state['message'])
        release.set()
        for thread in threading.enumerate():
            if thread.name == f'detection-{fruit_id}':
                thread.join(2)
        self.assertEqual(execute.call_count, 1)
        self.assertNotEqual(
            self.client.get('/api/state/').json()['detection_status'],
            'completed',
        )
        self.client.get('/api/camera/state/?camera_ready=1')
        next_started = self._post_json('/api/photo_capture/')
        self.assertEqual(next_started.status_code, 200)
        self.assertNotEqual(next_started.json()['active_fruit_id'], fruit_id)

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_restart_during_hardware_detection_keeps_gate3_recovery_lock(
        self, execute
    ):
        release = threading.Event()
        entered = threading.Event()

        def blocked(image_paths, model_paths, **kwargs):
            entered.set()
            release.wait(2)
            return self._external_result(image_paths, model_paths, **kwargs)

        execute.side_effect = blocked
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self.assertTrue(entered.wait(1))
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()
        self.assertEqual(state['work_mode'], 'detection')
        self.assertEqual(state['hardware_mode'], 'hardware')
        self.assertEqual(state['detection_status'], 'failed')
        self.assertEqual(state['status'], 'detection_failed')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertTrue(state['gate3_manual_removal_required'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertIn('不自動重跑', state['message'])

        release.set()
        for thread in threading.enumerate():
            if thread.name == f'detection-{fruit_id}':
                thread.join(2)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(
            self.client.get('/api/state/').json()['detection_status'], 'failed'
        )

    @mock.patch(
        'fruit_app.detection.save_interrupted_result',
        side_effect=OSError('read only'),
    )
    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_restart_result_write_failure_keeps_state_api_available(
        self, execute, _save
    ):
        release = threading.Event()

        def blocked(image_paths, model_paths, **kwargs):
            release.wait(2)
            return self._external_result(image_paths, model_paths, **kwargs)

        execute.side_effect = blocked
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        views.reset_runtime_state_for_tests()

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual(state['detection_status'], 'failed')
        self.assertEqual(
            state['detection_result']['reason'], 'service_restarted'
        )
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(self._post_json('/api/photo_capture/').status_code, 200)

        release.set()
        for thread in threading.enumerate():
            if thread.name == f'detection-{fruit_id}':
                thread.join(2)
