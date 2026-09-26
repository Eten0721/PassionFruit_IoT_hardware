"""Automatic-detection contracts through HTTP and real temporary files."""

import json
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import SimpleTestCase, override_settings

from . import tests as existing
from . import views


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

    def test_detection_requires_photo_only_and_hides_classification(self):
        response = self._start_detection('hardware')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reason'], 'hardware_mode_unavailable')
        response = self._post_json('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['work_mode'], 'detection')
        self.assertFalse(response.json()['can_classify'])

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
        deadline = time.monotonic() + 2
        while execute.call_count and time.monotonic() < deadline:
            if (self.detection_root / fruit_id / 'result.json').exists():
                break
            time.sleep(0.01)
        self.assertEqual(execute.call_count, 1)
        self.assertNotEqual(
            self.client.get('/api/state/').json()['detection_status'],
            'completed',
        )
