"""Automatic-detection contracts through HTTP and real temporary files."""

import csv
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


class SummaryReplaceRetryTests(SimpleTestCase):
    def test_brief_lock_retries_and_long_lock_names_the_file(self):
        from tempfile import TemporaryDirectory
        from . import dataset_store, detection

        real_replace = Path.replace
        attempts = []

        def locked_for(times):
            def replace(staging, target):
                attempts.append(target)
                if len(attempts) <= times:
                    raise PermissionError(13, 'locked')
                return real_replace(staging, target)
            return replace

        with TemporaryDirectory() as tmp, mock.patch.object(dataset_store, 'sleep'):
            path = Path(tmp) / 'detection_summary.csv'
            with mock.patch.object(Path, 'replace', locked_for(2)):
                detection.reset_summary(path)
            self.assertTrue(path.is_file())
            self.assertEqual(len(attempts), 3)

            attempts.clear()
            with mock.patch.object(Path, 'replace', locked_for(99)), \
                    self.assertRaisesRegex(PermissionError, 'detection_summary.csv'):
                detection.reset_summary(path)
            self.assertEqual(len(attempts), dataset_store.REPLACE_ATTEMPTS)
            self.assertEqual(list(Path(tmp).iterdir()), [path])  # No staging file left behind.


class DetectionFlowTests(SimpleTestCase):
    def setUp(self):
        existing.DataCollectionFlowTests.setUp(self)
        self.dataset_root = self.dataset_base / 'detection'
        model_root = self.dataset_root / 'models'
        paths = {
            role: model_root / f'{role}.pt'
            for role in ('roi', 'color', 'wrinkle', 'defect')
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.detection_settings = override_settings(
            DETECTION_MODEL_PATHS={
                role: str(path) for role, path in paths.items()
            },
        )
        self.detection_settings.enable()
        # Rounds advance on the next camera poll; interval tests restore 1 s.
        self.photo_interval = mock.patch.object(
            views, 'PHOTO_DETECTION_INTERVAL_SECONDS', 0,
        )
        self.photo_interval.start()

    def tearDown(self):
        self._wait_for_detection()
        self.photo_interval.stop()
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
            no_defects = dict.fromkeys(
                ('abrasion', 'anthracnose', 'insect_bite', 'insect_track'), 0.0,
            )
            reports.append({
                'ok': True, 'source': str(path),
                'roi': {'status': 'ok', 'confidence': 0.98,
                        'roi_area_ratio': 0.25},
                'models': {
                    'color': {'status': 'ok', 'class_name': 'good_color',
                              'confidence': 0.95},
                    'wrinkle': {'status': 'ok',
                                'class_name': 'false(沒皺褶)',
                                'confidence': 0.9},
                    'defect': {'status': 'ok', 'detections': [],
                               'area_ratios': dict(no_defects),
                               'max_confidences': dict(no_defects)},
                },
            })
            image = np.full((24, 32, 3), 128, dtype=np.uint8)
            artifacts.append({
                'roi_annotated': image, 'masked_roi': image,
                'wrinkle_gray': image[:, :, 0],
                'defect_annotated': image,
                'defect_marked': image,
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
    def test_hardware_detection_saves_then_classifies_without_feeding(self, execute):
        fruit_id = self._start_hardware_detection()
        third = self._upload_hardware_stations(fruit_id)
        self.assertIn(third.json()['detection_status'], ('pending', 'running'))

        state = self._wait_for_detection()
        self.assertEqual(state['detection_status'], 'completed')
        self.assertIsNone(state['active_fruit_id'])
        self.assertFalse(state['gate3_manual_removal_required'])
        self.assertTrue(state['auto_run_enabled'])
        command = state['motor_command']
        self.assertEqual(command['command'], 'classify_fruit')
        self.assertEqual(command['fruit_id'], fruit_id)
        self.assertEqual(command['classification_code'],
                         state['detection_result']['classification']['code'])
        self.assertEqual(self._saved_result(fruit_id)['storage']['status'], 'saved')
        self.assertEqual(len(self._summary_rows()), 1)
        self.assertEqual(state['detection_result']['sorting']['status'], 'pending')
        issued = [entry['details']['command'] for entry in state['transition_trace']
                  if entry['event'] == 'motor_command_issued']
        self.assertEqual(issued, ['start_sequence', 'release_gate', 'release_gate',
                                  'classify_fruit'])
        self.assertEqual(execute.call_count, 1)

        duplicate = self._report('capture_sequence_finished')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], command['command_id'])
        self.assertEqual(execute.call_count, 1)
        restarted = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(restarted.status_code, 200)
        self.assertEqual(restarted.json()['motor_command']['command_id'], command['command_id'])
        self.assertEqual(
            self._post_json('/api/collection_options/', {
                'work_mode': 'detection', 'hardware_mode': 'photo_only',
            }).json()['reason'],
            'auto_run_active',
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
        self.assertTrue(state['can_manual_grade'])
        self.assertTrue(state['gate3_manual_removal_required'])
        self.assertTrue(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')

    def test_hardware_detection_recovery_requires_explicit_confirmation(self):
        fruit_id, _ = self._hardware_needs_review_round()

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
        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertTrue((self.detection_root / fruit_id / 'result.json').is_file())
        self.assertEqual(len(self._summary_rows()), 1)
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
    def test_restart_after_committed_hardware_detection_never_replays_sorting(
        self, _execute
    ):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        before = self._wait_for_detection()
        command = before['motor_command']
        self.assertEqual(before['detection_status'], 'completed')

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        state = self.client.get('/api/state/').json()

        self.assertEqual(state['detection_status'], 'completed')
        self.assertIsNone(state['active_fruit_id'])
        self.assertTrue(state['sorter_recovery_required'])
        self.assertTrue(state['detection_result']['sorting']['recovery_required'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['motor_command']['command'], 'none')
        report = self._report('classification_sorter_completed',
                              command_id=command['command_id'],
                              classification_code=command['classification_code'])
        self.assertTrue(report.json()['duplicate'])
        self.assertEqual(views.APP_STATE['motor_command'], None)
        self.assertTrue(self._saved_result(fruit_id)['complete'])
        self.assertEqual(len(self._summary_rows()), 1)

    def test_restart_after_discard_keeps_persisted_gate3_recovery_lock(self):
        fruit_id, _ = self._hardware_needs_review_round()
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
        self.assertTrue(result['capture_time'])
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
                 'wrinkle_gray', 'defect_annotated', 'defect_marked'],
            )

        self.client.get('/api/camera/state/?camera_ready=1')
        next_started = self._post_json('/api/photo_capture/').json()
        self.assertNotEqual(next_started['active_fruit_id'], fruit_id)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_saved_photo_detection_clears_temp_and_keeps_result(
        self, _execute
    ):
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        state = self._wait_for_detection()

        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertTrue((self.detection_root / fruit_id / 'result.json').is_file())
        self.assertEqual(state['detection_result']['fruit_id'], fruit_id)
        self.assertFalse(state['can_discard'])
        self.assertIsNone(state['discardable_fruit_id'])
        original = self.client.get(
            state['detection_result']['images']['img_01.jpg']['artifact_urls']['original']
        )
        self.assertEqual(original.status_code, 200)
        original.close()
        response = self._post_json('/api/discard/', {'fruit_id': fruit_id})
        self.assertEqual(response.status_code, 409)
        self.assertTrue((self.detection_root / fruit_id / 'result.json').is_file())
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_farmer_artifact_absence_or_write_failure_keeps_completed_result(self, execute):
        from . import detection

        write_artifact = detection._write_artifact

        def fail_farmer_only(path, image):
            if path.name.endswith(detection.DEFECT_MARKED_SUFFIX):
                raise OSError('farmer image unavailable')
            write_artifact(path, image)

        for failure in ('absent', 'write_failed'):
            with self.subTest(failure=failure):
                def execute_batch(paths, models, **kwargs):
                    reports, artifacts = self._external_result(paths, models, **kwargs)
                    if failure == 'absent':
                        for artifact in artifacts:
                            artifact.pop('defect_marked')
                    return reports, artifacts

                execute.side_effect = execute_batch
                with mock.patch('fruit_app.detection._write_artifact',
                                side_effect=fail_farmer_only):
                    fruit_id, state = self._run_photo_round()
                result = self._saved_result(fruit_id)
                self.assertEqual(state['detection_status'], 'completed')
                self.assertTrue(result['complete'])
                self.assertEqual(result['inference']['status'], 'completed')
                self.assertEqual(result['classification']['status'], 'graded')
                self.assertEqual(result['save_errors'], [])
                for image in result['images'].values():
                    self.assertEqual(image['models']['defect']['status'], 'ok')
                    self.assertIn('defect_annotated', image['artifacts'])
                    self.assertNotIn('defect_marked', image['artifacts'])

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_restart_clears_leftover_temp_of_saved_photo_detection(self, _execute):
        with mock.patch.object(views, '_clear_saved_photo_input', return_value=''):
            fruit_id, _ = self._run_photo_round()
        self.assertTrue((self.dataset_root / 'temp' / fruit_id).exists())

        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()

        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertTrue((self.detection_root / fruit_id / 'result.json').is_file())
        self.assertFalse(state['can_discard'])
        self.assertEqual(state['next_fruit_id'], 'fruit_002')

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
        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result_status'], 'failed')
        self.assertEqual(rows[0]['reason'], 'artifact_save_failed')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    @mock.patch(
        'fruit_app.detection._atomic_json', side_effect=OSError('read only'),
    )
    def test_result_json_failure_still_records_failed_summary_row(
        self, _json, _execute
    ):
        fruit_id, state = self._run_photo_round()

        self.assertEqual(state['detection_status'], 'failed')
        self.assertIn('read only', state['detection_error'])
        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result_status'], 'failed')
        self.assertEqual(rows[0]['result_json'], '')
        self.assertTrue(rows[0]['reason'].startswith('result_json_save_failed'), rows[0])
        # An unsaved result keeps its originals in temp so the round can be discarded.
        self.assertTrue((self.dataset_root / 'temp' / fruit_id / 'img_01.jpg').is_file())
        self.assertTrue(state['can_discard'])
        response = self._post_json('/api/discard/', {'fruit_id': fruit_id})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse((self.dataset_root / 'temp' / fruit_id).exists())
        self.assertFalse((self.detection_root / fruit_id).exists())
        self.assertEqual(self._summary_rows(), [])

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

    # --- Two-stage XGBoost grading and durable result contract (#30) ---

    def _summary_rows(self):
        path = self.dataset_root / 'detection_summary.csv'
        if not path.exists():
            return []
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def _run_photo_round(self):
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        return fruit_id, self._wait_for_detection()

    @classmethod
    def _worst_second_view(cls, image_paths, model_paths, **kwargs):
        reports, artifacts = cls._external_result(image_paths, model_paths, **kwargs)
        reports[1]['roi']['roi_area_ratio'] = 0.1
        defect = reports[1]['models']['defect']
        defect['area_ratios']['anthracnose'] = 0.3
        defect['max_confidences']['anthracnose'] = 0.85
        defect['detections'] = [{
            'class_name': 'anthracnose', 'class_index': 1,
            'confidence': 0.85, 'mask_area_ratio': 0.3,
        }]
        return reports, artifacts

    def test_photo_round_grades_by_worst_view_and_saves_json_and_csv(self):
        with mock.patch(
            'fruit_app.detection.execute_model_batch',
            side_effect=self._worst_second_view,
        ):
            fruit_id, state = self._run_photo_round()

        self.assertEqual(state['detection_status'], 'completed')
        result = state['detection_result']
        self.assertEqual(result['contract_version'], 1)
        self.assertEqual(result['inference']['status'], 'completed')
        grades = {name: image['grade']['grade']
                  for name, image in result['images'].items()}
        self.assertEqual(grades['img_02.jpg'], 0)
        self.assertEqual(grades['img_01.jpg'], 3)
        classification = result['classification']
        self.assertEqual(classification['status'], 'graded')
        self.assertEqual(classification['grade'], 0)
        self.assertEqual(classification['label'], '加工')
        self.assertEqual(classification['code'], 'processing')
        self.assertEqual(classification['source'], 'model')
        self.assertEqual(classification['worst_view'], 'img_02.jpg')
        self.assertEqual(result['storage'], {
            'status': 'saved', 'reason': None,
            'result_json': f'results/{fruit_id}/result.json',
            'summary_csv': 'detection_summary.csv',
        })
        self.assertEqual(result['sorting']['status'], 'skipped')
        self.assertEqual(
            result['model']['grading']['stage1']['file'], 'GradeStage1.json',
        )
        features = result['images']['img_02.jpg']['grade']['features']
        self.assertEqual(features['anthracnose_area_ratio'], 0.3)
        self.assertEqual(features['color_confidence'], 0.95)
        self.assertEqual(features['local_defect_type_count'], 1)

        saved = json.loads(
            (self.detection_root / fruit_id / 'result.json').read_text('utf-8')
        )
        self.assertEqual(saved['result_id'], result['result_id'])
        self.assertEqual(saved['classification'], classification)
        self.assertTrue(saved['capture_time'])

        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['result_id'], result['result_id'])
        self.assertEqual(row['fruit_id'], fruit_id)
        self.assertEqual(
            [row[f'view_{index:02d}_grade'] for index in (1, 2, 3)],
            ['3', '0', '3'],
        )
        self.assertEqual(row['final_grade'], '0')
        self.assertEqual(row['final_label'], '加工')
        self.assertEqual(row['classification_source'], 'model')
        self.assertEqual(row['result_status'], 'completed')
        self.assertEqual(row['classification_status'], 'graded')
        self.assertEqual(row['result_json'], f'results/{fruit_id}/result.json')
        self.assertIn('stage1:', row['model_version'])

        self.assertEqual(state['motor_command']['command'], 'none')

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_missing_roi_needs_manual_review_without_guessing_grade(self, execute):
        reports, artifacts = self._external_result(
            [Path(f'img_{index:02d}.jpg') for index in range(1, 4)], {}
        )
        reports[1]['roi'] = {'status': 'error', 'reason': 'roi_not_detected'}
        reports[1]['models'] = {
            role: {'status': 'skipped', 'reason': 'roi_unavailable'}
            for role in ('color', 'wrinkle', 'defect')
        }
        artifacts[1] = {}
        execute.return_value = reports, artifacts

        fruit_id, state = self._run_photo_round()

        self.assertEqual(state['detection_status'], 'failed')
        result = state['detection_result']
        self.assertEqual(result['inference']['status'], 'failed')
        self.assertEqual(result['classification']['status'], 'needs_review')
        self.assertIsNone(result['classification']['grade'])
        self.assertIsNone(result['classification']['source'])
        view = result['images']['img_02.jpg']['grade']
        self.assertEqual(view['status'], 'unavailable')
        self.assertEqual(view['reason'], 'roi:roi_not_detected')
        self.assertIsNone(view['features'])
        self.assertEqual(result['images']['img_01.jpg']['grade']['status'], 'ok')
        self.assertIn('original', result['images']['img_02.jpg']['artifacts'])
        self.assertIn('待人工處理', state['message'])

        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result_status'], 'failed')
        self.assertEqual(rows[0]['classification_status'], 'needs_review')
        self.assertEqual(rows[0]['final_grade'], '')
        self.assertEqual(rows[0]['final_label'], '')
        self.assertEqual(rows[0]['view_02_grade'], '')
        self.assertEqual(rows[0]['reason'], 'detection_incomplete')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_missing_grade_model_needs_review_and_keeps_yolo_result(self, _execute):
        with override_settings(GRADE_MODEL_PATHS={
            'stage1': str(self.dataset_base / 'missing1.json'),
            'stage2': str(self.dataset_base / 'missing2.json'),
        }):
            _fruit_id, state = self._run_photo_round()

        self.assertEqual(state['detection_status'], 'failed')
        result = state['detection_result']
        self.assertEqual(result['inference']['status'], 'completed')
        self.assertEqual(result['classification']['status'], 'needs_review')
        self.assertTrue(
            result['classification']['reason'].startswith('grade_model_failed')
        )
        self.assertIn(
            'defect_annotated', result['images']['img_01.jpg']['artifacts'],
        )
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_summary_csv_failure_is_not_reported_as_saved(self, _execute):
        with mock.patch(
            'fruit_app.detection._write_summary_rows',
            side_effect=OSError('disk full'),
        ):
            fruit_id, state = self._run_photo_round()

        self.assertEqual(state['detection_status'], 'failed')
        result = state['detection_result']
        self.assertEqual(result['classification']['status'], 'graded')
        self.assertEqual(result['storage']['status'], 'failed')
        self.assertIn('disk full', result['storage']['reason'])
        self.assertIn('保存失敗', state['message'])
        saved = json.loads(
            (self.detection_root / fruit_id / 'result.json').read_text('utf-8')
        )
        self.assertEqual(saved['storage']['status'], 'failed')

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_rounds_and_reset_never_reuse_result_ids_or_duplicate_rows(self, execute):
        first_id, first = self._run_photo_round()
        duplicate = self._upload_station(first_id, 3, 3)
        self.assertEqual(duplicate.status_code, 409)
        self.client.get('/api/state/')
        second_id, second = self._run_photo_round()

        self.assertEqual(execute.call_count, 2)
        rows = self._summary_rows()
        self.assertEqual([row['fruit_id'] for row in rows], [first_id, second_id])
        result_ids = {first['detection_result']['result_id'],
                      second['detection_result']['result_id']}
        self.assertEqual(len(result_ids), 2)

        reset = self._post_json('/api/reset_dataset/')
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self._summary_rows(), [])
        third_id, third = self._run_photo_round()
        self.assertEqual(third_id, first_id)
        self.assertNotIn(third['detection_result']['result_id'], result_ids)
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_saved_detections_are_not_discarded_from_the_dashboard(self, _execute):
        first_id, _ = self._run_photo_round()
        second_id, _ = self._run_photo_round()
        response = self._post_json('/api/discard/', {'fruit_id': second_id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            [row['fruit_id'] for row in self._summary_rows()],
            [first_id, second_id],
        )

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_restart_records_one_needs_review_row(self, execute):
        release = threading.Event()

        def blocked(image_paths, model_paths, **kwargs):
            release.wait(2)
            return self._external_result(image_paths, model_paths, **kwargs)

        execute.side_effect = blocked
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.client.get('/api/state/')

        result = state['detection_result']
        self.assertEqual(result['classification']['status'], 'needs_review')
        self.assertEqual(result['classification']['reason'], 'service_restarted')
        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result_id'], result['result_id'])
        self.assertEqual(rows[0]['reason'], 'service_restarted')
        release.set()
        for thread in threading.enumerate():
            if thread.name == f'detection-{fruit_id}':
                thread.join(2)
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch(
        'fruit_app.detection.execute_model_batch', side_effect=_external_result
    )
    def test_photo_detection_waits_one_second_after_each_saved_photo(self, _execute):
        clock = [time.monotonic()]
        with mock.patch.object(views, 'PHOTO_DETECTION_INTERVAL_SECONDS', 1.0), \
                mock.patch('fruit_app.views.time.monotonic', side_effect=lambda: clock[0]):
            fruit_id = self._start_detection().json()['active_fruit_id']
            for station in (1, 2):
                token = self.client.get('/api/camera/state/').json()['capture_token']
                saved = self._upload_station(fruit_id, token, station)
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()['status'], 'photo_interval')
                self.assertEqual(saved.json()['motor_command']['command'], 'none')

                clock[0] += 0.999
                waiting = self.client.get('/api/camera/state/').json()
                self.assertFalse(waiting['capture_requested'])
                self.assertEqual(waiting['capture_token'], token)
                early = self._upload_station(fruit_id, token, station + 1)
                self.assertEqual(early.status_code, 409)
                self.assertEqual(early.json()['reason'], 'station_mismatch')
                state = self.client.get('/api/state/').json()
                self.assertEqual(state['status'], 'photo_interval')
                self.assertEqual(state['image_total'], station)

                clock[0] += 0.001
                if station == 2:
                    # The dashboard poll alone also releases the due request.
                    released = self.client.get('/api/state/').json()
                    self.assertTrue(released['capture_requested'])
                    self.assertEqual(released['status'], 'waiting_camera')
                due = self.client.get('/api/camera/state/').json()
                self.assertTrue(due['capture_requested'])
                self.assertEqual(due['station_index'], station + 1)
                self.assertNotEqual(due['capture_token'], token)
                stale = self._upload_station(fruit_id, token, station + 1)
                self.assertEqual(stale.status_code, 409)

            token = self.client.get('/api/camera/state/').json()['capture_token']
            last = self._upload_station(fruit_id, token, 3)
            self.assertEqual(last.status_code, 200)
            self.assertIn(last.json()['detection_status'], ('pending', 'running'))
            duplicate = self._upload_station(fruit_id, token, 3)
            self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self._wait_for_detection()['detection_status'], 'completed')
        self.assertFalse(self.motor_command_sequence_path.exists())

    def test_hardware_detection_has_no_photo_interval(self):
        with mock.patch.object(views, 'PHOTO_DETECTION_INTERVAL_SECONDS', 1.0):
            fruit_id = self._start_hardware_detection()
            command = self._esp32_command()
            ready = self._report(
                'station_1_ready', station_index=1,
                command_id=command['command_id'],
            )
            uploaded = self._upload_station(
                fruit_id, ready.json()['capture_token'], 1,
            )
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()['status'], 'waiting_motor')
        self.assertEqual(uploaded.json()['motor_command']['command'], 'release_gate')

    def test_discard_during_photo_interval_ends_round_without_next_request(self):
        clock = [time.monotonic()]
        with mock.patch.object(views, 'PHOTO_DETECTION_INTERVAL_SECONDS', 1.0), \
                mock.patch('fruit_app.views.time.monotonic', side_effect=lambda: clock[0]):
            fruit_id = self._start_detection().json()['active_fruit_id']
            token = self.client.get('/api/camera/state/').json()['capture_token']
            self.assertEqual(self._upload_station(fruit_id, token, 1).status_code, 200)
            discarded = self._post_json('/api/discard/', {
                'fruit_id': fruit_id, 'capture_token': token,
            })
            self.assertEqual(discarded.status_code, 200, discarded.content)
            clock[0] += 5
            camera = self.client.get('/api/camera/state/').json()
        self.assertFalse(camera['capture_requested'])
        self.assertIsNone(camera['active_fruit_id'])

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_running_detection_publishes_real_stage_artifacts(self, execute):
        roi_done = threading.Event()
        release = threading.Event()

        def staged(image_paths, model_paths, *, on_stage=None, **kwargs):
            reports, artifacts = self._external_result(image_paths, model_paths)
            partial_reports = [
                {**report, 'models': {}} for report in reports
            ]
            partial_reports[1]['roi'] = {
                'status': 'error', 'reason': 'roi_not_detected',
            }
            partial_artifacts = [
                {'roi_annotated': item['roi_annotated']}
                for item in artifacts
            ]
            partial_artifacts[1] = {}
            on_stage('roi', partial_reports, partial_artifacts)
            roi_done.set()
            release.wait(2)
            reports[1]['roi'] = partial_reports[1]['roi']
            reports[1]['models'] = {
                role: {'status': 'skipped', 'reason': 'roi_unavailable'}
                for role in ('color', 'wrinkle', 'defect')
            }
            artifacts[1] = {}
            return reports, artifacts

        execute.side_effect = staged
        fruit_id = self._start_detection().json()['active_fruit_id']
        self._upload_three(fruit_id)
        self.assertTrue(roi_done.wait(2))

        state = self.client.get('/api/state/').json()
        self.assertEqual(state['detection_status'], 'running')
        self.assertIsNone(state['detection_result'])
        progress = state['detection_progress']
        self.assertEqual(progress['stage'], 'roi')
        first = progress['images']['img_01.jpg']
        self.assertEqual(first['roi']['status'], 'ok')
        self.assertIn('roi_annotated', first['artifact_urls'])
        self.assertNotIn('masked_roi', first['artifact_urls'])
        artifact = self.client.get(first['artifact_urls']['roi_annotated'])
        self.assertEqual(artifact.status_code, 200)
        artifact.close()
        second = progress['images']['img_02.jpg']
        self.assertEqual(second['roi']['reason'], 'roi_not_detected')
        self.assertNotIn('roi_annotated', second['artifact_urls'])
        self.assertIn('original', second['artifact_urls'])

        release.set()
        state = self._wait_for_detection()
        self.assertEqual(state['detection_status'], 'failed')
        self.assertFalse(state['detection_result']['complete'])
        self.assertIsNone(state['detection_progress'])
        self.assertEqual(execute.call_count, 1)

    # --- Manual takeover of a needs-review round (#32) ---

    def _hardware_needs_review_round(self):
        reports, artifacts = self._external_result(
            [Path(f'img_{index:02d}.jpg') for index in range(1, 4)], {},
        )
        reports[0]['models']['defect'] = {'status': 'failed', 'reason': 'inference_failed'}
        artifacts[0].pop('defect_annotated')
        with mock.patch('fruit_app.detection.execute_model_batch', return_value=(reports, artifacts)):
            fruit_id = self._start_hardware_detection()
            self._upload_hardware_stations(fruit_id)
            return fruit_id, self._wait_for_detection()

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_matching_sorter_completion_feeds_once_and_continues_detection(self, _execute):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        state = self._wait_for_detection()
        command = state['motor_command']
        self.assertEqual(command['command'], 'classify_fruit')
        self.assertFalse(views.APP_STATE['auto_feed_pending'])
        self.assertEqual(self._esp32_command()['command_id'], command['command_id'])
        self.assertEqual(self.client.get('/api/state/').json()['detection_result']['sorting']['status'], 'running')
        fields = {'command_id': command['command_id'],
                  'classification_code': command['classification_code'], 'fruit_id': fruit_id}
        for wrong, reason in (
            ({'command_id': command['command_id'] + 10}, 'command_id_mismatch'),
            ({'classification_code': 'invalid'}, 'classification_code_mismatch'),
            ({'fruit_id': 'fruit_999'}, 'fruit_id_mismatch'),
        ):
            with self.subTest(reason=reason):
                report = self._report('classification_sorter_completed', **{**fields, **wrong})
                self.assertEqual(report.status_code, 409)
                self.assertEqual(report.json()['reason'], reason)
                self.assertEqual(views.APP_STATE['motor_command']['command_id'], command['command_id'])
        accepted = self._report('classification_sorter_completed', **fields)
        self.assertEqual(accepted.status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['detection_result']['sorting']['status'], 'completed')
        self.assertFalse(state['detection_result']['sorting']['recovery_required'])
        next_feed = state['motor_command']
        self.assertEqual(next_feed['command'], 'feed_one')
        duplicate = self._report('classification_sorter_completed', **fields)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], next_feed['command_id'])
        self.assertEqual(self._report_feed_success(next_feed).status_code, 200)
        next_fruit = self._report('hcsr04_trigger').json()['fruit_id']
        self.assertEqual(next_fruit, 'fruit_002')
        self.assertIsNone(self.client.get('/api/state/').json()['detection_result'])
        duplicate = self._report('classification_sorter_completed', **fields)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'start_sequence')
        self._upload_hardware_stations(next_fruit)
        self.assertEqual(self._wait_for_detection()['motor_command']['command'], 'classify_fruit')
        self.assertEqual(len(self._summary_rows()), 2)

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_pause_during_detection_finishes_sorting_without_next_feed(self, execute):
        fruit_id = self._start_hardware_detection()
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(2)
            return self._external_result(*args, **kwargs)
        execute.side_effect = blocked
        self._upload_hardware_stations(fruit_id)
        self.assertTrue(entered.wait(2))
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertEqual(state['detection_status'], 'running')
        self.assertEqual(self._post_json('/api/auto_run/', {'enabled': False}).status_code, 200)
        release.set()
        self.assertEqual(self._wait_for_detection()['motor_command']['command'], 'classify_fruit')
        command = self._esp32_command()
        self._report('classification_sorter_completed', command_id=command['command_id'],
                     classification_code=command['classification_code'])
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['sorter_status'], 'completed')
        self.assertFalse(state['auto_run_enabled'])
        self.assertFalse(state['auto_run_finishing'])
        self.assertEqual(state['motor_command']['command'], 'none')

    def test_manual_pause_finishes_sorting_without_next_feed(self):
        fruit_id, state = self._hardware_needs_review_round()
        self.assertEqual(state['motor_command']['command'], 'none')
        self._post_json('/api/auto_run/', {'enabled': False})
        self.assertEqual(self._manual_grade(state, '加工').status_code, 200)
        command = self._esp32_command()
        self._report('classification_sorter_completed', command_id=command['command_id'],
                     classification_code='processing')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertEqual(state['detection_result']['sorting']['status'], 'completed')
        self.assertEqual(self._saved_result(fruit_id)['classification']['source'], 'manual')

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_hardware_json_failure_never_queues_sorting(self, _execute):
        fruit_id = self._start_hardware_detection()
        with mock.patch('fruit_app.detection._atomic_json', side_effect=OSError('read only')):
            self._upload_hardware_stations(fruit_id)
            state = self._wait_for_detection()
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertEqual(state['detection_result']['storage']['status'], 'failed')
        self.assertIn('result_json_save_failed', state['detection_result']['storage']['reason'])
        self.assertFalse(state['can_manual_grade'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertTrue((self.dataset_root / 'temp' / fruit_id).is_dir())

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_hardware_csv_failure_never_queues_sorting(self, _execute):
        fruit_id = self._start_hardware_detection()
        with mock.patch('fruit_app.detection._write_summary_rows', side_effect=OSError('disk full')):
            self._upload_hardware_stations(fruit_id)
            state = self._wait_for_detection()
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertEqual(state['detection_result']['storage']['status'], 'failed')
        self.assertIn('summary_save_failed', state['detection_result']['storage']['reason'])
        self.assertFalse(self._saved_result(fruit_id)['complete'])
        self.assertFalse(state['can_manual_grade'])

    def test_hardware_manual_save_failure_never_queues_sorting(self):
        fruit_id, state = self._hardware_needs_review_round()
        original = self._saved_result(fruit_id)
        with mock.patch('fruit_app.detection._write_summary_rows', side_effect=OSError('disk full')):
            response = self._manual_grade(state, '下等')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['reason'], 'summary_save_failed')
        self.assertEqual(self._saved_result(fruit_id), original)
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertTrue(self.client.get('/api/state/').json()['can_manual_grade'])
        self.assertEqual(self._manual_grade(state, '下等').status_code, 200)
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'classify_fruit')

    def test_hardware_manual_grade_cannot_bypass_safety_conditions(self):
        fruit_id, state = self._hardware_needs_review_round()
        original = self._saved_result(fruit_id)
        for fields, reason in (
            ({'last_esp32_poll_monotonic': None}, 'esp32_offline'),
            ({'esp32_sorter_capable': False}, 'sorter_capability_missing'),
            ({'gate3_waiting_boot_id': None}, 'gate3_boot_unconfirmed'),
            ({'sorter_recovery_required': True}, 'sorter_recovery_required'),
            ({'network_recovery': {'reason': 'esp32_connection_lost'}}, 'network_recovery_required'),
        ):
            with self.subTest(reason=reason):
                previous = {key: views.APP_STATE.get(key) for key in fields}
                views.APP_STATE.update(fields)
                self.assertFalse(self.client.get('/api/state/').json()['can_manual_grade'])
                response = self._manual_grade(state, '中等')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['reason'],
                                 'esp32_connection_lost' if reason == 'network_recovery_required' else reason)
                self.assertIsNone(views.APP_STATE['motor_command'])
                self.assertEqual(self._saved_result(fruit_id), original)
                views.APP_STATE.update(previous)

    def test_hardware_abandon_requires_safe_discard(self):
        fruit_id, state = self._hardware_needs_review_round()
        self.assertFalse(state['can_manual_abandon'])
        response = self._manual_grade(state, None, abandon=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'hardware_abandon_requires_discard')
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(self._saved_result(fruit_id)['classification']['status'], 'needs_review')

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_sorter_failure_keeps_committed_detection_and_requires_recovery(self, _execute):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self._wait_for_detection()
        before = self._saved_result(fruit_id)
        command = self._esp32_command()
        self._report('classification_sorter_failed', command_id=command['command_id'],
                     classification_code=command['classification_code'], message='motor_failed')
        state = self.client.get('/api/state/').json()
        self.assertEqual(self._saved_result(fruit_id), before)
        self.assertEqual(len(self._summary_rows()), 1)
        self.assertEqual(state['detection_result']['sorting']['status'], 'failed')
        self.assertEqual(state['detection_result']['sorting']['reason'], 'motor_failed')
        self.assertTrue(state['detection_result']['sorting']['recovery_required'])
        self.assertEqual(state['motor_command']['command'], 'none')
        self.assertFalse(state['auto_run_enabled'])

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_sorter_timeout_and_late_report_keep_committed_detection(self, _execute):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self._wait_for_detection()
        command = self._esp32_command()
        views.APP_STATE['sorter_deadline_monotonic'] = 0
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['detection_result']['sorting']['status'], 'timeout')
        self.assertTrue(state['detection_result']['sorting']['recovery_required'])
        report = self._report('classification_sorter_completed', command_id=command['command_id'],
                              classification_code=command['classification_code'])
        self.assertTrue(report.json()['duplicate'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertTrue(self._saved_result(fruit_id)['complete'])
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch('fruit_app.detection.execute_model_batch', side_effect=_external_result)
    def test_sorter_completion_waits_for_feeder_conditions_without_replaying(self, _execute):
        fruit_id = self._start_hardware_detection()
        self._upload_hardware_stations(fruit_id)
        self._wait_for_detection()
        command = self._esp32_command()
        views.APP_STATE['camera_last_live_frame_monotonic'] = None
        report = self._report('classification_sorter_completed', command_id=command['command_id'],
                              classification_code=command['classification_code'])
        self.assertEqual(report.status_code, 200)
        self.assertIsNone(views.APP_STATE['motor_command'])
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic()
        for fields in (
            {'esp32_feeder_capable': False}, {'esp32_sorter_capable': False},
            {'esp32_feeder_sensor_state': 'blocked'},
            {'last_esp32_poll_monotonic': None},
            {'camera_last_live_frame_monotonic': None}, {'sorter_recovery_required': True},
        ):
            with self.subTest(fields=fields):
                previous = {key: views.APP_STATE.get(key) for key in fields}
                views.APP_STATE.update(fields)
                self.client.get('/api/esp32/heartbeat/')
                duplicate = self._report('classification_sorter_completed', command_id=command['command_id'],
                                         classification_code=command['classification_code'])
                self.assertTrue(duplicate.json()['duplicate'])
                self.assertIsNone(views.APP_STATE['motor_command'])
                views.APP_STATE.update(previous)
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'feed_one')

    def test_real_disconnect_locks_manual_takeover_across_restart(self):
        fruit_id, state = self._hardware_needs_review_round()
        original = self._saved_result(fruit_id)
        self.safety_monitor_patcher.stop()
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6.1
        locked = self.client.get('/api/state/').json()
        self.assertEqual(locked['auto_run_recovery_reason'], 'esp32_connection_lost')
        self.assertFalse(locked['can_manual_grade'])
        self.assertTrue(views._network_recovery_path().is_file())
        response = self._manual_grade(state, '上等')
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(self._saved_result(fruit_id), original)
        views.reset_runtime_state_for_tests()
        locked = self.client.get('/api/state/').json()
        self.assertFalse(locked['can_manual_grade'])
        self.assertEqual(locked['motor_command']['command'], 'none')

    def test_disconnect_during_manual_save_keeps_grade_without_releasing(self):
        from . import detection
        fruit_id, state = self._hardware_needs_review_round()
        original = self._saved_result(fruit_id)
        write_rows = detection._write_summary_rows
        def disconnect_then_save(*args, **kwargs):
            self.safety_monitor_patcher.stop()
            views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6.1
            self.client.get('/api/state/')
            return write_rows(*args, **kwargs)
        with mock.patch('fruit_app.detection._write_summary_rows', side_effect=disconnect_then_save):
            response = self._manual_grade(state, '中等')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._saved_result(fruit_id)['classification']['source'], 'manual')
        self.assertEqual(self._saved_result(fruit_id)['model_classification'], original['classification'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertTrue(response.json()['detection_result']['sorting']['recovery_required'])
        self.assertFalse(response.json()['auto_run_enabled'])

    def test_restart_of_needs_review_requires_recovery_before_manual_grade(self):
        fruit_id, state = self._hardware_needs_review_round()
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['can_manual_grade'])
        self.assertTrue(state['detection_result']['sorting']['recovery_required'])
        # A fresh poll cannot re-establish the old Gate 3 confirmation.
        self.client.get('/api/esp32/command/', {'boot_id': 'boot-new', 'capability': 'feeder_v1',
                                             'sorter_capability': 'gate3_sorter_v1',
                                             'feeder_state': 'idle', 'feeder_sensor_state': 'clear'})
        response = self._manual_grade(state, '加工')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'gate3_boot_unconfirmed')
        self.assertEqual(self._saved_result(fruit_id)['classification']['status'], 'needs_review')

    def _run_needs_review_round(self):
        reports, artifacts = self._external_result(
            [Path(f'img_{index:02d}.jpg') for index in range(1, 4)], {}
        )
        reports[1]['roi'] = {'status': 'error', 'reason': 'roi_not_detected'}
        reports[1]['models'] = {
            role: {'status': 'skipped', 'reason': 'roi_unavailable'}
            for role in ('color', 'wrinkle', 'defect')
        }
        artifacts[1] = {}
        with mock.patch(
            'fruit_app.detection.execute_model_batch',
            return_value=(reports, artifacts),
        ):
            fruit_id, state = self._run_photo_round()
        self.assertEqual(
            state['detection_result']['classification']['status'], 'needs_review',
        )
        return fruit_id, state

    def _manual_grade(self, state, label, **overrides):
        result = state['detection_result']
        return self._post_json('/api/detection/manual_grade/', {
            'fruit_id': result['fruit_id'],
            'result_id': result['result_id'],
            'label': label,
            **overrides,
        })

    def _saved_result(self, fruit_id):
        return json.loads(
            (self.detection_root / fruit_id / 'result.json').read_text('utf-8')
        )

    def test_manual_grade_completes_needs_review_round_with_failure_evidence(self):
        expected = {'上等': (3, 'high'), '中等': (2, 'medium'),
                    '下等': (1, 'low'), '加工': (0, 'processing')}
        fruit_ids = []
        for label, (grade, code) in expected.items():
            with self.subTest(label=label):
                fruit_id, state = self._run_needs_review_round()
                fruit_ids.append(fruit_id)
                self.assertTrue(state['can_manual_grade'])
                original = state['detection_result']

                response = self._manual_grade(state, label)

                self.assertEqual(response.status_code, 200, response.content)
                self.assertFalse(response.json()['duplicate'])
                saved = self._saved_result(fruit_id)
                self.assertEqual(saved['result_id'], original['result_id'])
                classification = saved['classification']
                self.assertEqual(classification['status'], 'graded')
                self.assertEqual(classification['source'], 'manual')
                self.assertEqual(classification['grade'], grade)
                self.assertEqual(classification['label'], label)
                self.assertEqual(classification['code'], code)
                self.assertTrue(classification['decided_at'])
                # The model's own outcome and failure evidence stay intact.
                self.assertEqual(
                    saved['model_classification'], original['classification'],
                )
                self.assertEqual(saved['inference'], original['inference'])
                self.assertEqual(saved['inference']['status'], 'failed')
                self.assertEqual(saved['images'], {
                    name: {key: value for key, value in image.items()
                           if key != 'artifact_urls'}
                    for name, image in original['images'].items()
                })
                self.assertEqual(
                    saved['images']['img_02.jpg']['grade']['reason'],
                    'roi:roi_not_detected',
                )
                self.assertTrue(
                    (self.detection_root / fruit_id / 'img_02_original.jpg').is_file()
                )
                self.assertFalse(saved['complete'])
                self.assertEqual(saved['status'], 'manual_completed')
                self.assertEqual(saved['reason'], 'detection_incomplete')
                self.assertEqual(saved['storage']['status'], 'saved')

                state = self.client.get('/api/state/').json()
                self.assertEqual(state['detection_result']['classification'],
                                 classification)
                self.assertFalse(state['can_manual_grade'])
                self.assertEqual(
                    state['manual_grade_disabled_reason'], 'already_manual_graded',
                )
                self.assertIsNone(state['active_fruit_id'])
                self.assertFalse(state['capture_requested'])
                self.assertEqual(state['motor_command']['command'], 'none')

        rows = {row['fruit_id']: row for row in self._summary_rows()}
        self.assertEqual(list(rows), fruit_ids)
        for fruit_id, (label, (grade, code)) in zip(fruit_ids, expected.items()):
            row = rows[fruit_id]
            self.assertEqual(row['classification_source'], 'manual')
            self.assertEqual(row['classification_status'], 'graded')
            self.assertEqual(row['result_status'], 'manual_completed')
            self.assertEqual(row['final_grade'], str(grade))
            self.assertEqual(row['final_label'], label)
            self.assertEqual(row['final_code'], code)
            self.assertEqual(row['reason'], 'detection_incomplete')
            self.assertEqual(row['view_02_grade'], '')
        collection = self.dataset_base / 'collection'
        self.assertEqual(list(collection.rglob('fruit_*')), [])

    def test_manual_grade_rejects_stale_duplicate_and_ineligible_requests(self):
        fruit_id, state = self._run_needs_review_round()

        invalid = self._manual_grade(state, '特等')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['reason'], 'invalid_label')
        stale_id = self._manual_grade(state, '中等', result_id='0' * 32)
        self.assertEqual(stale_id.status_code, 409)
        self.assertEqual(stale_id.json()['reason'], 'stale_detection_result')
        wrong_fruit = self._manual_grade(state, '中等', fruit_id='fruit_999')
        self.assertEqual(wrong_fruit.status_code, 409)
        self.assertEqual(wrong_fruit.json()['reason'], 'stale_detection_result')
        stale_mode = self._manual_grade(state, '中等', dataset_mode='collection')
        self.assertEqual(stale_mode.json()['reason'], 'stale_dataset_mode')
        self.assertEqual(
            self._saved_result(fruit_id)['classification']['status'],
            'needs_review',
        )

        self.assertEqual(self._manual_grade(state, '中等').status_code, 200)
        repeat = self._manual_grade(state, '中等')
        self.assertEqual(repeat.status_code, 200)
        self.assertTrue(repeat.json()['duplicate'])
        changed = self._manual_grade(state, '加工')
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()['reason'], 'already_manual_graded')
        self.assertEqual(self._saved_result(fruit_id)['classification']['label'], '中等')
        self.assertEqual(len(self._summary_rows()), 1)

        # A newer round makes the earlier result stale for this screen.
        with mock.patch(
            'fruit_app.detection.execute_model_batch',
            side_effect=self._external_result,
        ):
            second_id, second = self._run_photo_round()
        self.assertEqual(second['detection_result']['classification']['source'],
                         'model')
        self.assertFalse(second['can_manual_grade'])
        self.assertEqual(second['manual_grade_disabled_reason'], 'not_needs_review')
        model_graded = self._manual_grade(second, '加工')
        self.assertEqual(model_graded.status_code, 409)
        self.assertEqual(model_graded.json()['reason'], 'not_needs_review')
        old = self._manual_grade(state, '加工')
        self.assertEqual(old.status_code, 409)
        self.assertEqual(old.json()['reason'], 'stale_detection_result')
        self.assertEqual(
            self._saved_result(second_id)['classification']['source'], 'model',
        )
        self.assertEqual(len(self._summary_rows()), 2)

    def test_manual_grade_save_failures_keep_round_needs_review(self):
        fruit_id, state = self._run_needs_review_round()
        before_rows = self._summary_rows()

        with mock.patch(
            'fruit_app.detection._write_summary_rows',
            side_effect=OSError('disk full'),
        ):
            csv_failed = self._manual_grade(state, '下等')
        self.assertEqual(csv_failed.status_code, 503)
        self.assertEqual(csv_failed.json()['reason'], 'summary_save_failed')
        self.assertIn('disk full', csv_failed.json()['message'])
        self.assertEqual(
            self._saved_result(fruit_id)['classification']['status'],
            'needs_review',
        )
        self.assertEqual(self._summary_rows(), before_rows)

        with mock.patch(
            'fruit_app.detection._atomic_json',
            side_effect=OSError('read-only'),
        ):
            json_failed = self._manual_grade(state, '下等')
        self.assertEqual(json_failed.status_code, 503)
        self.assertEqual(json_failed.json()['reason'], 'result_json_save_failed')
        self.assertEqual(self._summary_rows(), before_rows)

        state = self.client.get('/api/state/').json()
        self.assertTrue(state['can_manual_grade'])
        self.assertEqual(
            state['detection_result']['classification']['status'], 'needs_review',
        )
        self.assertEqual(self._manual_grade(state, '下等').status_code, 200)
        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['classification_source'], 'manual')

    def test_abandon_keeps_evidence_without_grade_or_next_round(self):
        fruit_id, state = self._run_needs_review_round()
        original = state['detection_result']

        response = self._post_json('/api/detection/manual_grade/', {
            'fruit_id': fruit_id, 'result_id': original['result_id'],
            'abandon': True,
        })

        self.assertEqual(response.status_code, 200, response.content)
        saved = self._saved_result(fruit_id)
        classification = saved['classification']
        self.assertEqual(classification['status'], 'abandoned')
        self.assertEqual(classification['source'], 'manual')
        self.assertIsNone(classification['grade'])
        self.assertIsNone(classification['label'])
        self.assertEqual(classification['reason'], 'operator_abandoned')
        self.assertEqual(saved['model_classification'], original['classification'])
        self.assertEqual(saved['inference'], original['inference'])
        self.assertEqual(saved['status'], 'manual_abandoned')
        self.assertFalse(saved['complete'])
        self.assertEqual(saved['reason'], 'detection_incomplete')
        self.assertTrue(
            (self.detection_root / fruit_id / 'img_01_original.jpg').is_file()
        )
        rows = self._summary_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['result_status'], 'manual_abandoned')
        self.assertEqual(rows[0]['classification_status'], 'abandoned')
        self.assertEqual(rows[0]['classification_source'], 'manual')
        self.assertEqual(rows[0]['final_grade'], '')

        state = self.client.get('/api/state/').json()
        self.assertFalse(state['can_manual_grade'])
        self.assertEqual(state['manual_grade_disabled_reason'], 'already_manual_graded')
        self.assertIsNone(state['active_fruit_id'])
        self.assertEqual(state['motor_command']['command'], 'none')
        repeat = self._post_json('/api/detection/manual_grade/', {
            'fruit_id': fruit_id, 'result_id': original['result_id'],
            'abandon': True,
        })
        self.assertTrue(repeat.json()['duplicate'])
        graded = self._manual_grade(state, '上等')
        self.assertEqual(graded.status_code, 409)
        self.assertEqual(graded.json()['reason'], 'already_manual_graded')
        self.assertEqual(len(self._summary_rows()), 1)

    @mock.patch('fruit_app.detection.execute_model_batch')
    def test_hardware_manual_grade_saves_evidence_then_classifies_once(
        self, execute,
    ):
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

        self.assertTrue(state['can_manual_grade'])
        original = self._saved_result(fruit_id)
        response = self._manual_grade(state, '上等')
        self.assertEqual(response.status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['motor_command']['command'], 'classify_fruit')
        self.assertEqual(state['motor_command']['classification_code'], 'high')
        command_id = state['motor_command']['command_id']
        saved = self._saved_result(fruit_id)
        self.assertEqual(saved['model_classification'], original['classification'])
        self.assertEqual(saved['inference'], original['inference'])
        self.assertEqual(saved['images'], original['images'])
        self.assertEqual(saved['classification']['source'], 'manual')
        self.assertEqual(len(self._summary_rows()), 1)
        repeated = self._manual_grade(state, '上等')
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()['duplicate'])
        self.assertEqual(repeated.json()['motor_command']['command_id'], command_id)
        self.assertEqual(len(self._summary_rows()), 1)
