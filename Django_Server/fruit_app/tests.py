import csv
import json
import shutil
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from . import views


class DataCollectionFlowTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self.temp_dir.name)
        self.detection_root = self.dataset_root / 'detection_results'
        self.capture_timing_path = self.dataset_root / 'runtime_config' / 'capture_timing.json'
        self.motor_command_sequence_path = self.dataset_root / 'runtime_config' / 'motor_command_sequence.json'
        self.settings_override = override_settings(
            DATASET_ROOT=self.dataset_root,
            DETECTION_OUTPUT_ROOT=self.detection_root,
            CAPTURE_TIMING_CONFIG_PATH=self.capture_timing_path,
            MOTOR_COMMAND_SEQUENCE_PATH=self.motor_command_sequence_path,
        )
        self.settings_override.enable()
        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        self.client = Client()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()
        views.reset_runtime_state_for_tests()

    def test_three_station_flow_uploads_and_classifies_dataset(self):
        response = self._post_json('/api/set_counter/', {'start_id': 120})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_120')

        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['fruit_id'], 'fruit_120')
        self.assertEqual(payload['motor_command']['command'], 'start_sequence')
        self.assertFalse(payload['pending_capture'])
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_120').exists())

        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')
        self.assertEqual(command['station_index'], 1)
        self.assertEqual(command['release_angle'], 90)
        self.assertEqual(command['servo_settle_ms'], 200)
        self.assertEqual(command['fruit_settle_ms'], 350)

        station_1 = self._report('station_1_ready', station_index=1, command_id=command['command_id']).json()
        self.assertTrue(station_1['capture_requested'])
        self.assertEqual(station_1['station_index'], 1)

        response = self._upload_station('fruit_120', station_1['capture_token'], 1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_120' / 'img_01.jpg').exists())
        self.assertEqual(response.json()['motor_command']['command'], 'release_gate')

        command = self._esp32_command()
        self.assertEqual(command['command'], 'release_gate')
        self.assertEqual(command['station_index'], 1)
        station_2 = self._report('station_2_ready', station_index=2, command_id=command['command_id']).json()
        response = self._upload_station('fruit_120', station_2['capture_token'], 2)
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command()
        self.assertEqual(command['station_index'], 2)
        station_3 = self._report('station_3_ready', station_index=3, command_id=command['command_id']).json()
        response = self._upload_station('fruit_120', station_3['capture_token'], 3)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['status'], 'uploaded')
        self.assertEqual(response.json()['motor_command']['command'], 'none')
        self.assertIsNone(response.json()['active_station_index'])

        response = self._post_json('/api/classify/', {
            'label': '上等',
            'note': '表皮完整',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_120').exists())
        self.assertTrue((self.dataset_root / '上等' / 'fruit_120' / 'img_03.jpg').exists())
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_121')

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fruit_id'], 'fruit_120')
        self.assertEqual(rows[0]['label'], '上等')
        self.assertEqual(rows[0]['path'], '上等/fruit_120')
        self.assertEqual(rows[0]['capture_count'], '3')
        self.assertEqual(rows[0]['station_01_ok'], 'true')
        self.assertEqual(rows[0]['station_02_ok'], 'true')
        self.assertEqual(rows[0]['station_03_ok'], 'true')
        self.assertEqual(rows[0]['note'], '表皮完整')

        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 121)

    def test_manual_capture_api_is_removed(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 404)
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_001').exists())

    def test_hcsr04_report_creates_same_three_station_session(self):
        response = self._post_json('/api/set_counter/', {'start_id': 42})
        self.assertEqual(response.status_code, 200)

        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['fruit_id'], 'fruit_042')
        self.assertEqual(payload['source'], 'esp32')
        self.assertFalse(payload['pending_capture'])

        state_response = self.client.get('/api/state/')
        self.assertEqual(state_response.status_code, 200)
        state_payload = state_response.json()
        self.assertEqual(state_payload['active_fruit_id'], 'fruit_042')
        self.assertEqual(state_payload['status'], 'waiting_esp32_start')
        self.assertEqual(state_payload['motor_command']['command'], 'start_sequence')
        self.assertEqual(state_payload['motor_command']['station_index'], 1)
        trace_events = [entry['event'] for entry in state_payload['transition_trace']]
        self.assertIn('hcsr04_trigger_received', trace_events)
        self.assertIn('hcsr04_trigger_accepted', trace_events)

        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')
        self.assertEqual(command['station_index'], 1)
        self.assertEqual(command['fruit_id'], 'fruit_042')
        self.assertEqual(command['release_angle'], 90)
        self.assertEqual(command['servo_settle_ms'], 200)
        self.assertEqual(command['fruit_settle_ms'], 350)

    def test_hcsr04_trigger_is_ignored_when_active_fruit_exists(self):
        first_response = self._report('hcsr04_trigger')
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()['fruit_id'], 'fruit_001')
        first_command = self._esp32_command()
        self.assertEqual(first_command['command'], 'start_sequence')

        second_response = self._report('hcsr04_trigger')
        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.json()
        self.assertTrue(second_payload['ok'])
        self.assertTrue(second_payload['ignored'])
        self.assertEqual(second_payload['reason'], 'duplicate_trigger_waiting_start_sequence')
        self.assertEqual(second_payload['motor_command']['command'], 'start_sequence')
        self.assertEqual(second_payload['motor_command']['command_id'], first_command['command_id'])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())
        second_command = self._esp32_command()
        self.assertEqual(second_command['command_id'], first_command['command_id'])
        self.assertEqual(second_command['fruit_id'], 'fruit_001')

    def test_hcsr04_trigger_is_ordinary_ignored_after_start_sequence_consumed(self):
        first_response = self._report('hcsr04_trigger')
        self.assertEqual(first_response.status_code, 200)
        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')

        ready_response = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(ready_response.status_code, 200)

        second_response = self._report('hcsr04_trigger')
        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.json()
        self.assertTrue(second_payload['ok'])
        self.assertTrue(second_payload['ignored'])
        self.assertEqual(second_payload['reason'], 'active_fruit_exists')
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())

    def test_command_text_disables_auto_trigger_while_temp_fruit_exists(self):
        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')
        self.assertEqual(command['server_status'], 'idle')

        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fruit_id'], 'fruit_001')

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')
        self.assertEqual(command['server_status'], 'waiting_esp32_start')
        self.assertEqual(command['command'], 'start_sequence')
        self.assertEqual(command['release_angle'], '90')
        self.assertEqual(command['servo_settle_ms'], '200')
        self.assertEqual(command['fruit_settle_ms'], '350')

    def test_hcsr04_trigger_is_ignored_when_temp_fruit_exists(self):
        fruit_dir = self.dataset_root / 'temp' / 'fruit_001'
        fruit_dir.mkdir(parents=True)
        (fruit_dir / 'img_01.jpg').write_bytes(b'existing-temp-image')

        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['ignored'])
        self.assertEqual(payload['reason'], 'temp_fruit_exists')
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')

    def test_auto_trigger_stays_disabled_until_classification(self):
        self._complete_auto_session('fruit_001')

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')
        self.assertEqual(command['server_status'], 'uploaded')

        response = self._post_json('/api/classify/', {'label': views.LABELS[0]})
        self.assertEqual(response.status_code, 200)

        self._complete_sorter()

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')

    def test_all_labels_map_to_ascii_sorter_commands_without_hardware_fields(self):
        expected_codes = {
            '上等': 'high',
            '中等': 'medium',
            '下等': 'low',
            '加工': 'processing',
        }
        for index, (label, expected_code) in enumerate(expected_codes.items(), start=1):
            fruit_id = f'fruit_{index:03d}'
            self._complete_auto_session(fruit_id)
            response = self._post_json('/api/classify/', {'label': label})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload['data_classified'])
            self.assertTrue(payload['sorter_command_queued'])
            self.assertEqual(payload['sorter_status'], 'pending')

            command = self._esp32_command()
            self.assertEqual(command['command'], 'classify_fruit')
            self.assertEqual(command['classification_code'], expected_code)
            self.assertNotIn('station_index', command)
            self.assertNotIn('home_angle', command)
            self.assertNotIn('release_angle', command)
            self.assertNotIn('servo_settle_ms', command)
            self.assertNotIn('idle_command_poll_interval_ms', command)
            self.assertNotIn('gpio', command)
            text_command = self._esp32_command_text()
            self.assertEqual(text_command['classification_code'], expected_code)
            self.assertNotIn('station_index', text_command)
            self.assertNotIn('home_angle', text_command)
            self._complete_sorter()

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual([row['label'] for row in rows], list(expected_codes))

    def test_classification_requires_gate3_sorter_capability_before_dataset_commit(self):
        self._complete_auto_session('fruit_001')
        views.APP_STATE['esp32_sorter_capable'] = False

        state = self.client.get('/api/state/').json()
        self.assertFalse(state['can_classify'])
        self.assertEqual(state['classify_disabled_reason'], 'sorter_capability_missing')

        response = self._post_json('/api/classify/', {'label': '上等'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'sorter_capability_missing')
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / 'img_03.jpg').exists())
        self.assertFalse((self.dataset_root / '上等' / 'fruit_001').exists())
        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            self.assertEqual(list(csv.DictReader(csv_file)), [])

    def test_auto_run_requires_gate3_sorter_capability(self):
        self._prepare_auto_run(camera_ready=True)
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'sorter_capability_missing')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_esp32_restart_while_gate3_waits_blocks_classification_without_committing_data(self):
        self._complete_auto_session('fruit_001')

        restarted = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-after-gate3-restart',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self.assertEqual(restarted.status_code, 200)

        response = self._post_json('/api/classify/', {'label': '中等'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'gate3_boot_unconfirmed')
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / 'img_03.jpg').exists())
        self.assertFalse((self.dataset_root / '中等' / 'fruit_001').exists())

    def test_esp32_restart_during_sorter_never_resends_physical_command(self):
        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '加工'}).json()
        command = self._esp32_command()
        self.assertEqual(command['command'], 'classify_fruit')

        restarted = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-during-sorter-restart',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        }).json()

        self.assertEqual(restarted['command'], 'none')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['sorter_status'], 'failed')
        self.assertEqual(state['sorter_error'], 'esp32_restarted_during_sorter')
        self.assertFalse(state['auto_run_enabled'])
        self.assertTrue((self.dataset_root / '加工' / classified['fruit_id']).exists())

        late = self._report(
            'classification_sorter_completed',
            command_id=command['command_id'],
            classification_code=command['classification_code'],
        )
        self.assertEqual(late.status_code, 200)
        self.assertTrue(late.json()['ignored'])

    def test_esp32_restart_during_capture_never_replays_gate_command(self):
        self._mark_esp32_online()
        started = self._report('hcsr04_trigger').json()
        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')

        restarted = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-during-capture-restart',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        }).json()

        self.assertEqual(restarted['command'], 'none')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(
            state['operator_alert']['reason'],
            'esp32_restarted_during_capture',
        )
        self.assertTrue((self.dataset_root / 'temp' / started['fruit_id']).exists())

    def test_late_sorter_report_after_django_restart_is_ignored_without_next_feed(self):
        self._complete_auto_session('fruit_001')
        self._post_json('/api/classify/', {'label': '上等'})
        command = self._esp32_command()
        self.assertTrue(views._sorter_recovery_path().exists())

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        self.assertTrue(views.APP_STATE['sorter_recovery_required'])
        self._mark_esp32_online()
        views.APP_STATE['capture_timing']['feeder_calibrated'] = True
        views.APP_STATE['capture_timing_applied_revision'] = views.APP_STATE['capture_timing_revision']
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic()

        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'django_restarted_during_sorter')
        reset = self._post_json('/api/reset_dataset/')
        self.assertEqual(reset.status_code, 409)
        self.assertEqual(reset.json()['reason'], 'django_restarted_during_sorter')

        response = self._report(
            'classification_sorter_completed',
            command_id=command['command_id'],
            classification_code=command['classification_code'],
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ignored'])
        self.assertEqual(response.json()['reason'], 'sorter_terminal_state')
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertFalse(views.APP_STATE['auto_feed_pending'])
        self.assertTrue((self.dataset_root / '上等' / 'fruit_001').exists())
        self.assertTrue(views._sorter_recovery_path().exists())

        still_blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(still_blocked.status_code, 409)
        self.assertEqual(still_blocked.json()['reason'], 'django_restarted_during_sorter')

        recovered = self._post_json('/api/auto_run/', {
            'enabled': True,
            'recovery_confirmed': True,
        })
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()['motor_command']['command'], 'feed_one')
        self.assertFalse(views._sorter_recovery_path().exists())

    def test_django_restart_requires_gate3_recovery_before_classification(self):
        self._complete_auto_session('fruit_001')

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        self._mark_esp32_online()
        recovered = self.client.get('/api/state/').json()
        self.assertEqual(recovered['status'], 'uploaded')

        response = self._post_json('/api/classify/', {'label': '下等'})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'gate3_boot_unconfirmed')
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001').exists())
        self.assertFalse((self.dataset_root / '下等' / 'fruit_001').exists())

        discarded = self._post_json('/api/discard/').json()
        self.assertEqual(
            discarded['operator_alert']['reason'],
            'gate3_manual_removal_required',
        )
        self.assertIn('切斷伺服電源', discarded['operator_alert']['instruction'])

    def test_classification_offline_preserves_gate3_fruit_and_temp_dataset(self):
        self._complete_auto_session('fruit_001')
        views.APP_STATE['last_esp32_poll_monotonic'] = None

        response = self._post_json('/api/classify/', {'label': '加工'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'esp32_offline')
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001').exists())
        self.assertFalse((self.dataset_root / '加工' / 'fruit_001').exists())
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_data_stays_classified_when_sorter_slot_is_busy(self):
        self._complete_auto_session('fruit_001')
        with views.STATE_LOCK:
            views.APP_STATE['auto_run_enabled'] = True
            views.APP_STATE['motor_command'] = {
                'command': 'release_gate',
                'command_id': 999,
                'station_index': 3,
                'fruit_id': 'fruit_001',
            }

        response = self._post_json('/api/classify/', {'label': '下等'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['data_classified'])
        self.assertFalse(payload['sorter_command_queued'])
        self.assertEqual(payload['sorter_error'], 'sorter_command_slot_busy')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertFalse(views.APP_STATE['auto_feed_pending'])
        self.assertEqual(
            self.client.get('/api/state/').json()['operator_alert']['reason'],
            'sorter_command_slot_busy',
        )
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], 999)
        self.assertTrue((self.dataset_root / '下等' / 'fruit_001' / 'img_03.jpg').exists())

        self.assertTrue(views.APP_STATE['sorter_recovery_required'])
        self.assertTrue(views._sorter_recovery_path().exists())

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'django_restarted_during_sorter')

    def test_sorter_marker_update_failure_keeps_recovery_lock(self):
        self._complete_auto_session('fruit_001')

        with mock.patch.object(
            views,
            '_write_sorter_recovery_marker',
            side_effect=OSError('disk unavailable'),
        ):
            response = self._post_json('/api/classify/', {'label': '加工'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['data_classified'])
        self.assertFalse(payload['sorter_command_queued'])
        self.assertEqual(payload['sorter_error'], 'sorter_recovery_persist_failed')
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertTrue(views.APP_STATE['sorter_recovery_required'])
        self.assertTrue(views._sorter_recovery_path().exists())

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'django_restarted_during_sorter')

    def test_restart_after_dataset_commit_keeps_sorter_recovery_lock(self):
        self._complete_auto_session('fruit_001')

        with mock.patch.object(
            views,
            '_queue_sorter_command',
            side_effect=RuntimeError('simulated process stop'),
        ):
            with self.assertRaises(RuntimeError):
                self._post_json('/api/classify/', {'label': '中等'})

        self.assertFalse((self.dataset_root / 'temp' / 'fruit_001').exists())
        self.assertTrue((self.dataset_root / '中等' / 'fruit_001').exists())
        self.assertTrue(views._sorter_recovery_path().exists())

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        blocked = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'django_restarted_during_sorter')

    def test_failed_data_classification_never_creates_sorter_command(self):
        invalid = self._post_json('/api/classify/', {'label': '未知'})
        self.assertEqual(invalid.status_code, 400)
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(views.APP_STATE['sorter_status'], 'idle')

        partial_dir = self.dataset_root / 'temp' / 'fruit_001'
        partial_dir.mkdir(parents=True)
        (partial_dir / 'img_01.jpg').write_bytes(b'partial')
        for legacy_label in ('上中等', '廢棄'):
            legacy = self._post_json('/api/classify/', {'label': legacy_label})
            self.assertEqual(legacy.status_code, 400)
            self.assertEqual(legacy.json()['reason'], 'invalid_label')

        incomplete = self._post_json('/api/classify/', {'label': '上等'})
        self.assertEqual(incomplete.status_code, 409)
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(views.APP_STATE['sorter_status'], 'idle')

    def test_sorter_failure_timeout_and_late_report_never_rollback_dataset(self):
        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '中等'}).json()
        command = self._esp32_command()
        views.APP_STATE['auto_run_enabled'] = True
        failed = self._report(
            'classification_sorter_failed',
            command_id=command['command_id'],
            classification_code=command['classification_code'],
            message='classifier_attach_failed',
        )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()['sorter_status'], 'failed')
        failed_state = self.client.get('/api/state/').json()
        self.assertFalse(failed_state['auto_run_enabled'])
        self.assertFalse(views.APP_STATE['auto_feed_pending'])
        self.assertEqual(
            failed_state['operator_alert']['reason'],
            'classifier_attach_failed',
        )
        self.assertTrue((self.dataset_root / '中等' / classified['fruit_id']).exists())

        with views.STATE_LOCK:
            views._clear_sorter_recovery_marker()
            views._reset_sorter_state()
            views.APP_STATE['auto_run_recovery_reason'] = None
            views.APP_STATE['last_error_reason'] = None
            views.APP_STATE['status'] = 'idle'

        self._complete_auto_session('fruit_002')
        second = self._post_json('/api/classify/', {'label': '上等'}).json()
        timeout_command = views.APP_STATE['motor_command'].copy()
        views.APP_STATE['sorter_deadline_monotonic'] = 0
        timed_out = self.client.get('/api/state/').json()
        self.assertEqual(timed_out['sorter_status'], 'timeout')
        self.assertEqual(timed_out['sorter_error'], 'esp32_timeout')
        self.assertTrue((self.dataset_root / '上等' / second['fruit_id']).exists())

        late = self._report(
            'classification_sorter_completed',
            command_id=timeout_command['command_id'],
            classification_code=timeout_command['classification_code'],
            message='classification_sorter_completed',
        )
        self.assertEqual(late.status_code, 200)
        self.assertTrue(late.json()['ignored'])
        self.assertEqual(late.json()['sorter_status'], 'timeout')

    def test_running_sorter_timeout_uses_classifier_timeout_reason(self):
        self._complete_auto_session('fruit_001')
        self._post_json('/api/classify/', {'label': '加工'})
        command = self._esp32_command()
        self.assertEqual(views.APP_STATE['sorter_status'], 'running')
        views.APP_STATE['sorter_deadline_monotonic'] = 0

        state = self.client.get('/api/state/').json()
        self.assertEqual(state['sorter_status'], 'timeout')
        self.assertEqual(state['sorter_error'], 'classifier_timeout')
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['operator_alert']['reason'], 'classifier_timeout')
        self.assertEqual(state['operator_alert']['location'], 'MG996R classifier')
        self.assertEqual(state['operator_alert']['fruit_id'], 'fruit_001')
        self.assertEqual(state['operator_alert']['command'], 'classify_fruit')
        self.assertEqual(state['operator_alert']['command_id'], command['command_id'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertTrue((self.dataset_root / '加工' / 'fruit_001').exists())

        late = self._report(
            'classification_sorter_failed',
            command_id=command['command_id'],
            classification_code=command['classification_code'],
            message='classifier_timeout',
        )
        self.assertEqual(late.status_code, 200)
        self.assertTrue(late.json()['ignored'])

    def test_duplicate_classification_writes_one_row_and_one_sorter_command(self):
        self._complete_auto_session('fruit_001')
        first = self._post_json('/api/classify/', {'label': '上等'})
        second = self._post_json('/api/classify/', {'label': '上等'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], first.json()['sorter_command_id'])
        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            self.assertEqual(len(list(csv.DictReader(csv_file))), 1)

    def test_motor_command_id_persists_across_runtime_and_dataset_reset(self):
        with views.STATE_LOCK:
            first = views._set_motor_command('start_sequence', station_index=1).copy()
            views.APP_STATE['motor_command'] = None
        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        with views.STATE_LOCK:
            second = views._set_motor_command('start_sequence', station_index=1).copy()
            views.APP_STATE['motor_command'] = None
        self.assertGreater(second['command_id'], first['command_id'])

        reset = self._post_json('/api/reset_dataset/')
        self.assertEqual(reset.status_code, 200)
        with views.STATE_LOCK:
            third = views._set_motor_command('start_sequence', station_index=1).copy()
            views.APP_STATE['motor_command'] = None
        self.assertGreater(third['command_id'], second['command_id'])

    def test_corrupt_motor_command_sequence_refuses_new_command(self):
        self.motor_command_sequence_path.parent.mkdir(parents=True, exist_ok=True)
        self.motor_command_sequence_path.write_text('{broken', encoding='utf-8')
        with views.STATE_LOCK:
            with self.assertRaises(views.CaptureCommandError) as raised:
                views._set_motor_command('start_sequence', station_index=1)
        self.assertEqual(raised.exception.reason, 'motor_command_id_persist_failed')

    def test_auto_trigger_is_enabled_after_discard(self):
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')

    def test_discard_while_gate3_waits_requires_manual_power_off_removal(self):
        self._complete_auto_session('fruit_001')
        views.APP_STATE['auto_run_enabled'] = True

        response = self._post_json('/api/discard/')

        self.assertEqual(response.status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(state['operator_alert']['reason'], 'gate3_manual_removal_required')
        self.assertIn('斷電', state['message'])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_001').exists())

    def test_gate3_waiting_rejects_recapture_and_dataset_reset(self):
        self._complete_auto_session('fruit_001')
        views.APP_STATE['auto_run_enabled'] = True

        recapture = self._post_json('/api/recapture/')
        reset = self._post_json('/api/reset_dataset/')

        self.assertEqual(recapture.status_code, 409)
        self.assertEqual(recapture.json()['reason'], 'gate3_manual_removal_required')
        self.assertEqual(reset.status_code, 409)
        self.assertEqual(reset.json()['reason'], 'gate3_manual_removal_required')
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['can_recapture'])
        self.assertFalse(state['can_reset_dataset'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertEqual(state['image_total'], 3)
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_esp32_restart_during_third_upload_cannot_confirm_new_boot(self):
        self._mark_esp32_online()
        self._report('hcsr04_trigger')
        command = self._esp32_command()
        station_1 = self._report(
            'station_1_ready', station_index=1, command_id=command['command_id']
        ).json()
        self._upload_station('fruit_001', station_1['capture_token'], 1)
        command = self._esp32_command()
        station_2 = self._report(
            'station_2_ready', station_index=2, command_id=command['command_id']
        ).json()
        self._upload_station('fruit_001', station_2['capture_token'], 2)
        command = self._esp32_command()
        station_3 = self._report(
            'station_3_ready', station_index=3, command_id=command['command_id']
        ).json()
        save_image = views._save_station_image

        def save_then_restart(*args):
            save_image(*args)
            self.client.get('/api/esp32/command/', {
                'boot_id': 'boot-during-upload',
                'capability': 'feeder_v1',
                'sorter_capability': 'gate3_sorter_v1',
                'feeder_state': 'idle',
                'feeder_sensor_state': 'clear',
                'last_feed_command_id': '0',
            })

        with mock.patch.object(views, '_save_station_image', side_effect=save_then_restart):
            response = self._upload_station('fruit_001', station_3['capture_token'], 3)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'capture_state_changed_during_upload')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['last_error_reason'], 'esp32_restarted_during_capture')
        self.assertFalse(state['can_recapture'])
        self.assertFalse(state['can_reset_dataset'])
        self.assertIsNone(views.APP_STATE['gate3_waiting_boot_id'])
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / 'img_03.jpg').exists())

    def test_failed_upload_does_not_overwrite_restart_recovery(self):
        station_payload = self._start_capture_and_ready_station_1()
        fruit_id = station_payload['active_fruit_id']
        token = station_payload['capture_token']

        def restart_then_fail(*args):
            self.client.get('/api/esp32/command/', {
                'boot_id': 'boot-during-failed-upload',
                'capability': 'feeder_v1',
                'sorter_capability': 'gate3_sorter_v1',
                'feeder_state': 'idle',
                'feeder_sensor_state': 'clear',
                'last_feed_command_id': '0',
            })
            raise views.DatasetFileBusyError('write locked')

        with mock.patch.object(views, '_save_station_image', side_effect=restart_then_fail):
            response = self._upload_station(fruit_id, token, 1)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'capture_state_changed_during_upload')
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['last_error_reason'], 'esp32_restarted_during_capture')
        self.assertFalse(state['capture_requested'])

        retry = self._upload_station(fruit_id, token, 1)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.json()['reason'], 'capture_not_requested')

    def test_auto_trigger_is_enabled_after_reset_dataset(self):
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')

        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')

    def test_duplicate_station_ready_does_not_issue_new_capture_token(self):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()

        first_ready = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(first_ready.status_code, 200)
        first_payload = first_ready.json()
        first_token = first_payload['capture_token']

        duplicate_ready = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(duplicate_ready.status_code, 200)
        duplicate_payload = duplicate_ready.json()
        self.assertTrue(duplicate_payload['ignored'])
        self.assertTrue(duplicate_payload['duplicate'])
        self.assertEqual(duplicate_payload['reason'], 'duplicate_station_ready')
        self.assertEqual(duplicate_payload['capture_token'], first_token)

    def test_station_ready_rejects_jump_ahead(self):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()

        jump_response = self._report('station_2_ready', station_index=2, command_id=command['command_id'])
        self.assertEqual(jump_response.status_code, 409)
        self.assertEqual(jump_response.json()['reason'], 'unexpected_station_ready')

    def test_station_ready_requires_matching_command_id(self):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()

        missing_response = self._report('station_1_ready', station_index=1)
        self.assertEqual(missing_response.status_code, 409)
        self.assertEqual(missing_response.json()['reason'], 'command_id_mismatch')

        zero_response = self._report('station_1_ready', station_index=1, command_id=0)
        self.assertEqual(zero_response.status_code, 409)
        self.assertEqual(zero_response.json()['reason'], 'command_id_mismatch')

        wrong_response = self._report('station_1_ready', station_index=1, command_id=command['command_id'] + 1)
        self.assertEqual(wrong_response.status_code, 409)
        self.assertEqual(wrong_response.json()['reason'], 'command_id_mismatch')

        valid_response = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(valid_response.status_code, 200)

    def test_single_station_upload_rejects_wrong_station_and_token(self):
        station_payload = self._start_capture_and_ready_station_1()
        fruit_id = station_payload['active_fruit_id']
        token = station_payload['capture_token']

        wrong_station = self._upload_station(fruit_id, token, 2)
        self.assertEqual(wrong_station.status_code, 409)
        self.assertEqual(wrong_station.json()['reason'], 'station_mismatch')

        wrong_token = self._upload_station(fruit_id, token + 1, 1)
        self.assertEqual(wrong_token.status_code, 409)

        response = self._upload_station(fruit_id, token, 1)
        self.assertEqual(response.status_code, 200)

        duplicate = self._upload_station(fruit_id, token, 1)
        self.assertEqual(duplicate.status_code, 409)

    def test_capture_started_is_repeatable_and_returns_debug_on_conflict(self):
        station_payload = self._start_capture_and_ready_station_1()
        fruit_id = station_payload['active_fruit_id']
        token = station_payload['capture_token']

        response = self._post_json('/api/capture_started/', {
            'fruit_id': fruit_id,
            'capture_token': token,
            'station_index': 1,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['capture_still_requested'])

        repeat_response = self._post_json('/api/capture_started/', {
            'fruit_id': fruit_id,
            'capture_token': token,
            'station_index': 1,
        })
        self.assertEqual(repeat_response.status_code, 200)

        wrong_token = self._post_json('/api/capture_started/', {
            'fruit_id': fruit_id,
            'capture_token': token + 1,
            'station_index': 1,
        })
        self.assertEqual(wrong_token.status_code, 409)
        wrong_token_payload = wrong_token.json()
        self.assertEqual(wrong_token_payload['reason'], 'capture_command_mismatch')
        self.assertEqual(wrong_token_payload['expected_capture_token'], token)
        self.assertEqual(wrong_token_payload['received_capture_token'], token + 1)
        self.assertEqual(wrong_token_payload['expected_station_index'], 1)

        wrong_station = self._post_json('/api/capture_started/', {
            'fruit_id': fruit_id,
            'capture_token': token,
            'station_index': 2,
        })
        self.assertEqual(wrong_station.status_code, 409)
        wrong_station_payload = wrong_station.json()
        self.assertEqual(wrong_station_payload['reason'], 'station_mismatch')
        self.assertEqual(wrong_station_payload['expected_station_index'], 1)
        self.assertEqual(wrong_station_payload['received_station_index'], 2)

    def test_upload_failure_keeps_same_station_available_for_phone_retry(self):
        station_payload = self._start_capture_and_ready_station_1()
        fruit_id = station_payload['active_fruit_id']
        token = station_payload['capture_token']

        with mock.patch.object(
            views,
            '_save_station_image',
            side_effect=views.DatasetFileBusyError('write locked'),
        ):
            response = self._upload_station(fruit_id, token, 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'upload_failed')

        state_payload = self.client.get('/api/state/').json()
        self.assertEqual(state_payload['active_fruit_id'], fruit_id)
        self.assertEqual(state_payload['capture_token'], token)
        self.assertEqual(state_payload['status'], 'waiting_camera')
        self.assertEqual(state_payload['station_index'], 1)
        self.assertTrue(state_payload['capture_requested'])

    def test_waiting_states_timeout_with_clear_error_message(self):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)

        with views.STATE_LOCK:
            views.APP_STATE['wait_started_monotonic'] -= views.ESP32_START_TIMEOUT_SECONDS + 1

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['last_error_reason'], 'esp32_start_timeout')
        self.assertIn('ESP32', payload['message'])
        self.assertTrue(payload['can_discard'])
        self.assertTrue(payload['can_recapture'])

    def test_discard_removes_temp_without_metadata_or_counter_increment(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_005').exists())
        views.APP_STATE['auto_run_enabled'] = True

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        discard_payload = response.json()
        self.assertEqual(discard_payload['discarded_fruit_id'], 'fruit_005')
        self.assertEqual(discard_payload['status'], 'idle')
        self.assertIsNone(discard_payload['active_fruit_id'])
        self.assertEqual(discard_payload['capture_token'], 0)
        self.assertFalse(discard_payload['auto_run_enabled'])
        self.assertNotIn('can_manual_capture', discard_payload)
        self.assertFalse(discard_payload['can_discard'])
        self.assertFalse(discard_payload['can_classify'])
        self.assertEqual(discard_payload['latest_images'], [])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_005').exists())

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [views.METADATA_FIELDNAMES])

        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 5)

        refreshed = self.client.get('/api/state/').json()
        self.assertEqual(refreshed['status'], 'idle')
        self.assertNotIn('can_manual_capture', refreshed)

    def test_discard_stops_auto_run_before_file_deletion(self):
        self._mark_esp32_online()
        self._report('hcsr04_trigger')
        views.APP_STATE['auto_run_enabled'] = True

        with mock.patch.object(views, '_discard_temp_fruit', side_effect=OSError('delete failed')):
            response = self._post_json('/api/discard/')

        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.client.get('/api/state/').json()['auto_run_enabled'])

    def test_recapture_stops_auto_run_without_feeding(self):
        self._mark_esp32_online()
        started = self._report('hcsr04_trigger')
        self.assertEqual(started.status_code, 200)
        views.APP_STATE['auto_run_enabled'] = True

        recaptured = self._post_json('/api/recapture/')
        self.assertEqual(recaptured.status_code, 200)
        self.assertFalse(recaptured.json()['auto_run_enabled'])
        self.assertEqual(recaptured.json()['motor_command']['command'], 'start_sequence')

    def test_recapture_stops_auto_run_before_file_deletion(self):
        self._mark_esp32_online()
        self._report('hcsr04_trigger')
        views.APP_STATE['auto_run_enabled'] = True

        with mock.patch.object(
            views,
            '_clear_temp_images',
            side_effect=views.DatasetFileBusyError('delete failed'),
        ):
            response = self._post_json('/api/recapture/')

        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.client.get('/api/state/').json()['auto_run_enabled'])

    def test_discard_quarantines_busy_temp_folder_and_reenables_auto_trigger(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        self._mark_esp32_online()
        self._report('hcsr04_trigger')
        fruit_dir = self.dataset_root / 'temp' / 'fruit_005'
        (fruit_dir / 'img_01.jpg').write_bytes(b'image')

        with mock.patch.object(
            views,
            '_safe_rmtree',
            side_effect=views.DatasetFileBusyError('delete locked'),
        ):
            response = self._post_json('/api/discard/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['discard_mode'], 'quarantined')
        self.assertEqual(payload['status'], 'idle')
        self.assertNotIn('can_manual_capture', payload)
        self.assertFalse(fruit_dir.exists())
        self.assertTrue((self.dataset_root / '_delete_pending').exists())
        self.assertTrue((self.dataset_root / 'discard_state.json').exists())
        self.assertEqual(self._esp32_command()['auto_trigger_enabled'], 1)

    def test_discard_defers_locked_temp_folder_and_skips_its_fruit_id(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        self._mark_esp32_online()
        self._report('hcsr04_trigger')
        fruit_dir = self.dataset_root / 'temp' / 'fruit_005'
        (fruit_dir / 'img_01.jpg').write_bytes(b'image')

        with mock.patch.object(
            views,
            '_safe_rmtree',
            side_effect=views.DatasetFileBusyError('delete locked'),
        ), mock.patch.object(
            views,
            '_safe_move',
            side_effect=views.DatasetFileBusyError('move locked'),
        ):
            response = self._post_json('/api/discard/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['discard_mode'], 'deferred_cleanup')
        self.assertEqual(payload['status'], 'idle')
        self.assertEqual(payload['next_fruit_id'], 'fruit_006')
        self.assertTrue(fruit_dir.exists())
        self.assertNotIn('can_manual_capture', payload)
        self.assertEqual(self._esp32_command()['auto_trigger_enabled'], 1)

        with (self.dataset_root / 'discard_state.json').open('r', encoding='utf-8') as state_file:
            discard_state = json.load(state_file)
        self.assertEqual(discard_state['pending_paths'][0]['source_path'], 'temp/fruit_005')
        self.assertTrue(discard_state['pending_paths'][0]['blocked_in_temp'])

    def test_idle_state_retries_and_cleans_deferred_discard(self):
        fruit_dir = self.dataset_root / 'temp' / 'fruit_009'
        fruit_dir.mkdir(parents=True)
        (fruit_dir / 'img_01.jpg').write_bytes(b'image')
        views._write_discard_state({'pending_paths': [{
            'fruit_id': 'fruit_009',
            'source_path': 'temp/fruit_009',
            'cleanup_path': 'temp/fruit_009',
            'blocked_in_temp': True,
            'recorded_at': '2026-07-10 00:00:00',
        }]})
        views.APP_STATE['discard_cleanup_last_monotonic'] = None

        response = self.client.get('/api/state/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(fruit_dir.exists())
        self.assertEqual(response.json()['status'], 'idle')
        self.assertNotIn('can_manual_capture', response.json())
        self.assertEqual(views._read_discard_state()['pending_paths'], [])

    def test_capture_timing_defaults_persist_and_are_acknowledged_by_esp32(self):
        initial = self.client.get('/api/state/').json()
        self.assertEqual(initial['capture_timing'], views.CAPTURE_TIMING_RECOMMENDED)
        self.assertEqual(initial['capture_timing_revision'], 1)

        configured = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 350,
            'servo_settle_ms': 400,
            'fruit_settle_ms': 450,
            'final_gate_return_delay_ms': 300,
            'idle_command_poll_interval_ms': 400,
        })
        self.assertEqual(configured.status_code, 200)
        payload = configured.json()
        self.assertEqual(payload['capture_timing_revision'], 2)
        self.assertEqual(payload['capture_timing']['fruit_settle_ms'], 450)
        self.assertEqual(payload['capture_timing_status'], 'waiting_esp32')

        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            saved = json.load(timing_file)
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['capture_timing']['servo_settle_ms'], 400)
        self.assertEqual(saved['capture_timing']['idle_command_poll_interval_ms'], 400)

        command = self._esp32_command_text()
        self.assertEqual(command['timing_revision'], '2')
        self.assertEqual(command['first_station_settle_ms'], '350')
        self.assertEqual(command['servo_settle_ms'], '400')
        self.assertEqual(command['fruit_settle_ms'], '450')
        self.assertEqual(command['final_gate_return_delay_ms'], '300')
        self.assertEqual(command['idle_command_poll_interval_ms'], '400')

        acknowledged = self._report('timing_config_applied', timing_revision=2)
        self.assertEqual(acknowledged.status_code, 200)
        acknowledged_payload = acknowledged.json()
        self.assertEqual(acknowledged_payload['capture_timing_status'], 'applied')
        self.assertEqual(acknowledged_payload['capture_timing_applied_revision'], 2)

        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        reloaded = self.client.get('/api/state/').json()
        self.assertEqual(reloaded['capture_timing_revision'], 2)
        self.assertEqual(reloaded['capture_timing']['servo_settle_ms'], 400)

    def test_capture_timing_migrates_legacy_dataset_file_to_single_runtime_path(self):
        self.capture_timing_path.unlink()
        legacy_path = self.dataset_root / 'capture_timing.json'
        legacy_payload = {
            'revision': 10,
            'capture_timing': {
                'first_station_settle_ms': 200,
                'servo_settle_ms': 250,
                'fruit_settle_ms': 300,
                'final_gate_return_delay_ms': 350,
            },
        }
        legacy_path.write_text(json.dumps(legacy_payload), encoding='utf-8')
        views.reset_runtime_state_for_tests()

        views._ensure_dataset_structure()

        self.assertFalse(legacy_path.exists())
        self.assertTrue(self.capture_timing_path.exists())
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            migrated = json.load(timing_file)
        self.assertEqual(migrated['revision'], 11)
        self.assertEqual(migrated['capture_timing'], {
            **legacy_payload['capture_timing'],
            'idle_command_poll_interval_ms': 250,
            'feeder_stop_us': 1500,
            'feeder_drive_us': 1300,
            'feeder_max_run_ms': 5000,
            'feeder_calibrated': False,
        })
        self.assertEqual(views.APP_STATE['capture_timing_revision'], 11)

    def test_capture_timing_upgrades_existing_four_field_runtime_file(self):
        legacy_runtime_timing = {
            'first_station_settle_ms': 450,
            'servo_settle_ms': 500,
            'fruit_settle_ms': 550,
            'final_gate_return_delay_ms': 600,
        }
        self.capture_timing_path.write_text(json.dumps({
            'revision': 7,
            'capture_timing': legacy_runtime_timing,
        }), encoding='utf-8')
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()

        self.assertEqual(state['capture_timing_revision'], 8)
        self.assertEqual(state['capture_timing'], {
            **legacy_runtime_timing,
            'idle_command_poll_interval_ms': 250,
            'feeder_stop_us': 1500,
            'feeder_drive_us': 1300,
            'feeder_max_run_ms': 5000,
            'feeder_calibrated': False,
        })
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            saved = json.load(timing_file)
        self.assertEqual(saved['revision'], 8)
        self.assertEqual(saved['capture_timing'], state['capture_timing'])

    def test_capture_timing_migrates_old_feeder_fields_without_reusing_old_run_time(self):
        self.capture_timing_path.write_text(json.dumps({
            'revision': 12,
            'capture_timing': {
                **views.CAPTURE_TIMING_RECOMMENDED,
                'feeder_stop_us': 1520,
                'feeder_drive_us': 1250,
                'feeder_run_ms': 320,
                'fruit_arrival_warning_ms': 9000,
                'feeder_calibrated': True,
            },
        }), encoding='utf-8')
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()

        self.assertEqual(state['capture_timing_revision'], 13)
        self.assertEqual(state['capture_timing']['feeder_stop_us'], 1520)
        self.assertEqual(state['capture_timing']['feeder_drive_us'], 1250)
        self.assertEqual(state['capture_timing']['feeder_max_run_ms'], 5000)
        self.assertFalse(state['capture_timing']['feeder_calibrated'])
        self.assertNotIn('feeder_run_ms', state['capture_timing'])
        self.assertNotIn('fruit_arrival_warning_ms', state['capture_timing'])
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            saved = json.load(timing_file)
        self.assertEqual(saved['capture_timing'], state['capture_timing'])

    def test_corrupt_runtime_profile_falls_back_with_explicit_warning(self):
        self.capture_timing_path.write_text('{broken', encoding='utf-8')
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()

        self.assertEqual(state['capture_timing'], views.CAPTURE_TIMING_RECOMMENDED)
        self.assertIn('損壞或驗證失敗', state['capture_timing_warning'])
        self.assertFalse(state['capture_timing']['feeder_calibrated'])

    def test_existing_short_feeder_timeout_is_upgraded_and_uncalibrated(self):
        self.capture_timing_path.write_text(json.dumps({
            'revision': 21,
            'capture_timing': {
                **views.CAPTURE_TIMING_RECOMMENDED,
                'feeder_stop_us': 1520,
                'feeder_drive_us': 1250,
                'feeder_max_run_ms': 150,
                'feeder_calibrated': True,
            },
        }), encoding='utf-8')
        views.reset_runtime_state_for_tests()

        state = self.client.get('/api/state/').json()

        self.assertEqual(state['capture_timing_revision'], 22)
        self.assertEqual(state['capture_timing']['feeder_stop_us'], 1520)
        self.assertEqual(state['capture_timing']['feeder_drive_us'], 1250)
        self.assertEqual(state['capture_timing']['feeder_max_run_ms'], 5000)
        self.assertFalse(state['capture_timing']['feeder_calibrated'])

    def test_idle_command_poll_interval_validates_range_and_step(self):
        base_timing = {
            'first_station_settle_ms': 300,
            'servo_settle_ms': 200,
            'fruit_settle_ms': 350,
            'final_gate_return_delay_ms': 300,
        }
        for invalid_value in (50, 125, 5050):
            response = self._post_json('/api/capture_timing/', {
                **base_timing,
                'idle_command_poll_interval_ms': invalid_value,
            })
            self.assertEqual(response.status_code, 400)
        valid = self._post_json('/api/capture_timing/', {
            **base_timing,
            'idle_command_poll_interval_ms': 5000,
        })
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()['capture_timing']['idle_command_poll_interval_ms'], 5000)

    def test_capture_timing_reuses_one_runtime_file_for_multiple_updates(self):
        first = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 250,
            'servo_settle_ms': 250,
            'fruit_settle_ms': 250,
            'final_gate_return_delay_ms': 250,
        })
        self.assertEqual(first.status_code, 200)

        second = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 300,
            'servo_settle_ms': 350,
            'fruit_settle_ms': 400,
            'final_gate_return_delay_ms': 450,
        })
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['capture_timing_revision'], 3)
        self.assertFalse((self.dataset_root / 'capture_timing.json').exists())
        self.assertEqual(
            sorted(path.name for path in self.capture_timing_path.parent.iterdir()),
            ['capture_timing.json'],
        )
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            saved = json.load(timing_file)
        self.assertEqual(saved['capture_timing']['final_gate_return_delay_ms'], 450)

    def test_capture_timing_rejects_invalid_step_and_active_session_update(self):
        invalid = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 325,
            'servo_settle_ms': 300,
            'fruit_settle_ms': 300,
            'final_gate_return_delay_ms': 300,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['reason'], 'capture_timing_invalid_step')

        self._report('hcsr04_trigger')
        blocked = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 350,
            'servo_settle_ms': 350,
            'fruit_settle_ms': 350,
            'final_gate_return_delay_ms': 350,
        })
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['reason'], 'capture_timing_update_requires_idle')

    def test_operator_can_save_ack_and_run_one_calibration_feed(self):
        timing = {
            **views.CAPTURE_TIMING_RECOMMENDED,
            'feeder_stop_us': 1510,
            'feeder_drive_us': 1310,
            'feeder_max_run_ms': 20000,
            'feeder_calibrated': False,
        }
        configured = self._post_json('/api/capture_timing/', timing)
        self.assertEqual(configured.status_code, 200)
        self.assertEqual(configured.json()['capture_timing_revision'], 2)

        command_response = self.client.get(
            '/api/esp32/command/',
            {
                'format': 'text',
                'boot_id': 'boot-a',
                'capability': 'feeder_v1',
                'feeder_state': 'idle',
                'feeder_sensor_state': 'clear',
                'last_feed_command_id': '0',
            },
        )
        self.assertEqual(command_response.status_code, 200)
        command_text = dict(
            line.split('=', 1)
            for line in command_response.content.decode('utf-8').splitlines()
            if '=' in line
        )
        self.assertEqual(command_text['feeder_stop_us'], '1510')
        self.assertEqual(command_text['feeder_drive_us'], '1310')
        self.assertEqual(command_text['feeder_max_run_ms'], '20000')

        acknowledged = self._report('timing_config_applied', timing_revision=2)
        self.assertEqual(acknowledged.status_code, 200)
        self.assertTrue(acknowledged.json()['can_test_feeder'])

        started = self._post_json('/api/feeder/test/')
        self.assertEqual(started.status_code, 200)
        payload = started.json()
        self.assertIsNone(payload['active_fruit_id'])
        self.assertEqual(payload['motor_command']['command'], 'feed_one')
        self.assertEqual(payload['motor_command']['feed_context'], 'calibration')
        self.assertEqual(payload['motor_command']['feeder_max_run_ms'], 20000)

        completed = self._report(
            'feed_cycle_completed',
            command_id=payload['motor_command']['command_id'],
            feeder_elapsed_ms=84,
            feeder_max_run_ms=20000,
            feeder_stop_reason='hcsr04',
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()['status'], 'idle')
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertIsNone(views.APP_STATE['active_fruit_id'])
        self.assertEqual(completed.json()['feeder_test_result']['elapsed_ms'], 84)
        self.assertTrue(completed.json()['can_confirm_feeder_calibration'])

        command_sequence = views.APP_STATE['motor_command_id']
        confirmed = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 2,
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['capture_timing_revision'], 2)
        self.assertTrue(confirmed.json()['capture_timing']['feeder_calibrated'])
        self.assertEqual(views.APP_STATE['motor_command_id'], command_sequence)
        self.assertEqual(confirmed.json()['motor_command']['command'], 'none')

        views.reset_runtime_state_for_tests()
        reloaded_confirmation = self.client.get('/api/state/').json()
        self.assertEqual(reloaded_confirmation['capture_timing_revision'], 2)
        self.assertTrue(reloaded_confirmation['capture_timing']['feeder_calibrated'])

        changed = self._post_json('/api/capture_timing/', {
            **timing,
            'feeder_max_run_ms': 19500,
            'feeder_calibrated': True,
        })
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()['capture_timing']['feeder_calibrated'])
        self.assertFalse(changed.json()['can_confirm_feeder_calibration'])

        views.reset_runtime_state_for_tests()
        reloaded = self.client.get('/api/state/').json()
        self.assertEqual(reloaded['capture_timing']['feeder_stop_us'], 1510)
        self.assertFalse(reloaded['capture_timing']['feeder_calibrated'])

    def test_feeder_profile_validation_and_confirmation_rules(self):
        profile = {
            **views.CAPTURE_TIMING_RECOMMENDED,
            'feeder_stop_us': 1510,
            'feeder_drive_us': 1310,
            'feeder_max_run_ms': 5000,
            'feeder_calibrated': False,
        }
        mechanical_change = self._post_json('/api/capture_timing/', profile)
        self.assertEqual(mechanical_change.status_code, 200)
        self.assertFalse(mechanical_change.json()['capture_timing']['feeder_calibrated'])

        ignored_confirmation = self._post_json('/api/capture_timing/', {
            **profile,
            'feeder_calibrated': True,
        })
        self.assertEqual(ignored_confirmation.status_code, 200)
        self.assertFalse(ignored_confirmation.json()['capture_timing']['feeder_calibrated'])

        too_close = self._post_json('/api/capture_timing/', {
            **profile,
            'feeder_stop_us': 1510,
            'feeder_drive_us': 1510,
        })
        self.assertEqual(too_close.status_code, 400)
        self.assertEqual(too_close.json()['reason'], 'feeder_drive_matches_stop')

        reverse_direction = self._post_json('/api/capture_timing/', {
            **profile,
            'feeder_drive_us': 1520,
        })
        self.assertEqual(reverse_direction.status_code, 200)

        invalid_step = self._post_json('/api/capture_timing/', {
            **profile,
            'feeder_max_run_ms': 5250,
        })
        self.assertEqual(invalid_step.status_code, 400)
        self.assertEqual(invalid_step.json()['reason'], 'capture_timing_invalid_step')

        fractional = self._post_json('/api/capture_timing/', {
            **profile,
            'feeder_max_run_ms': 1000.9,
        })
        self.assertEqual(fractional.status_code, 400)
        self.assertEqual(fractional.json()['reason'], 'capture_timing_invalid_value')

        for valid_max_run_ms in (1000, 20000):
            response = self._post_json('/api/capture_timing/', {
                **profile,
                'feeder_max_run_ms': valid_max_run_ms,
            })
            self.assertEqual(response.status_code, 200)

        for invalid_max_run_ms in (500, 1250, 20500):
            response = self._post_json('/api/capture_timing/', {
                **profile,
                'feeder_max_run_ms': invalid_max_run_ms,
            })
            self.assertEqual(response.status_code, 400)

    def test_feeder_test_requires_capability_latest_ack_and_safe_idle(self):
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-a',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        missing_capability = self._post_json('/api/feeder/test/')
        self.assertEqual(missing_capability.status_code, 409)
        self.assertEqual(missing_capability.json()['reason'], 'feeder_capability_missing')

        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-a',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'blocked',
            'last_feed_command_id': '0',
        })
        waiting_ack = self._post_json('/api/feeder/test/')
        self.assertEqual(waiting_ack.status_code, 409)
        self.assertEqual(waiting_ack.json()['reason'], 'feeder_sensor_not_clear')

        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-a',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'unavailable',
            'last_feed_command_id': '0',
        })
        unavailable = self._post_json('/api/feeder/test/')
        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(unavailable.json()['reason'], 'feeder_sensor_unavailable')

        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-a',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        waiting_ack = self._post_json('/api/feeder/test/')
        self.assertEqual(waiting_ack.status_code, 409)
        self.assertEqual(waiting_ack.json()['reason'], 'feeder_timing_not_applied')

        self._report('timing_config_applied', timing_revision=1)
        active_dir = self.dataset_root / 'temp' / 'fruit_001'
        active_dir.mkdir(parents=True)
        (active_dir / 'img_01.jpg').write_bytes(b'partial-image')
        views.APP_STATE['active_fruit_id'] = 'fruit_001'
        active_fruit = self._post_json('/api/feeder/test/')
        self.assertEqual(active_fruit.status_code, 409)
        self.assertEqual(active_fruit.json()['reason'], 'active_fruit_exists')

        shutil.rmtree(active_dir)
        views.APP_STATE['active_fruit_id'] = None
        views.APP_STATE['sorter_status'] = 'pending'
        sorter_busy = self._post_json('/api/feeder/test/')
        self.assertEqual(sorter_busy.status_code, 409)
        self.assertEqual(sorter_busy.json()['reason'], 'classifier_busy')

        views.APP_STATE['sorter_status'] = 'idle'
        views.APP_STATE['auto_run_enabled'] = True
        auto_run_active = self._post_json('/api/feeder/test/')
        self.assertEqual(auto_run_active.status_code, 409)
        self.assertEqual(auto_run_active.json()['reason'], 'auto_run_must_be_stopped')

    def test_feeder_calibration_confirmation_rejects_unready_and_failed_tests(self):
        waiting_ack = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 1,
        })
        self.assertEqual(waiting_ack.status_code, 409)
        self.assertEqual(waiting_ack.json()['reason'], 'feeder_timing_not_applied')

        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-confirm',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self._report('timing_config_applied', timing_revision=1)

        untested = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 1,
        })
        self.assertEqual(untested.status_code, 409)
        self.assertEqual(untested.json()['reason'], 'feeder_test_required')

        stale = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 2,
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['reason'], 'stale_timing_revision')

        timeout_started = self._post_json('/api/feeder/test/').json()['motor_command']
        self._report(
            'feeder_max_run_timeout',
            command_id=timeout_started['command_id'],
            feeder_elapsed_ms=5000,
            feeder_max_run_ms=5000,
            feeder_stop_reason='max_run_timeout',
        )
        timeout = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 1,
        })
        self.assertEqual(timeout.status_code, 409)
        self.assertEqual(timeout.json()['reason'], 'feeder_test_required')

        failed_started = self._post_json('/api/feeder/test/').json()['motor_command']
        self._report(
            'feeder_sensor_unavailable',
            command_id=failed_started['command_id'],
            feeder_elapsed_ms=39,
            feeder_max_run_ms=5000,
            feeder_stop_reason='feeder_sensor_unavailable',
        )
        failed = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': 1,
        })
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(failed.json()['reason'], 'feeder_test_required')

    def test_feeder_calibration_confirmation_rolls_back_on_persist_failure(self):
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-confirm',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self._report('timing_config_applied', timing_revision=1)
        started = self._post_json('/api/feeder/test/').json()['motor_command']
        completed = self._report(
            'feed_cycle_completed',
            command_id=started['command_id'],
            feeder_elapsed_ms=84,
            feeder_max_run_ms=5000,
            feeder_stop_reason='hcsr04',
        )
        self.assertTrue(completed.json()['can_confirm_feeder_calibration'])

        with mock.patch.object(views.capture_timing, 'write', side_effect=OSError('disk full')):
            response = self._post_json('/api/feeder/calibration/confirm/', {
                'timing_revision': 1,
            })

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['reason'], 'capture_timing_persist_failed')
        self.assertFalse(views.APP_STATE['capture_timing']['feeder_calibrated'])
        self.assertTrue(response.json()['state']['can_confirm_feeder_calibration'])
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            self.assertFalse(json.load(timing_file)['capture_timing']['feeder_calibrated'])

    def test_starting_another_feeder_test_revokes_saved_confirmation(self):
        self._prepare_auto_run()
        self.assertTrue(views.APP_STATE['capture_timing']['feeder_calibrated'])

        started = self._post_json('/api/feeder/test/')

        self.assertEqual(started.status_code, 200)
        self.assertFalse(started.json()['capture_timing']['feeder_calibrated'])
        self.assertIsNone(started.json()['feeder_test_passed_revision'])
        with self.capture_timing_path.open('r', encoding='utf-8') as timing_file:
            saved = json.load(timing_file)
        self.assertFalse(saved['capture_timing']['feeder_calibrated'])
        self.assertEqual(saved['revision'], started.json()['capture_timing_revision'])

    def test_saved_feeder_confirmation_survives_reload_and_allows_start(self):
        self._prepare_auto_run(camera_ready=True)
        revision = views.APP_STATE['capture_timing_revision']
        views.reset_runtime_state_for_tests()

        reloaded = self.client.get('/api/state/').json()
        self.assertTrue(reloaded['capture_timing']['feeder_calibrated'])
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-after-reload',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self._report('timing_config_applied', timing_revision=revision)
        self.client.get('/api/camera/state/', {'camera_ready': '1'})

        started = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json()['auto_run_enabled'])
        self.assertEqual(started.json()['motor_command']['command'], 'feed_one')
        self.assertEqual(started.json()['motor_command']['feed_context'], 'production')

    def test_feeder_profile_update_requires_stopped_auto_run_and_idle_sorter(self):
        views.APP_STATE['auto_run_enabled'] = True
        auto_run_active = self._post_json('/api/capture_timing/', views.CAPTURE_TIMING_RECOMMENDED)
        self.assertEqual(auto_run_active.status_code, 409)
        self.assertEqual(auto_run_active.json()['reason'], 'capture_timing_update_requires_idle')

        views.APP_STATE['auto_run_enabled'] = False
        views.APP_STATE['sorter_status'] = 'pending'
        sorter_busy = self._post_json('/api/capture_timing/', views.CAPTURE_TIMING_RECOMMENDED)
        self.assertEqual(sorter_busy.status_code, 409)
        self.assertEqual(sorter_busy.json()['reason'], 'capture_timing_update_requires_idle')

    def test_feeder_timeout_stops_test_without_creating_capture_session(self):
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-test',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self._report('timing_config_applied', timing_revision=1)
        started = self._post_json('/api/feeder/test/').json()
        result = self._report(
            'feeder_max_run_timeout',
            command_id=started['motor_command']['command_id'],
            feeder_elapsed_ms=5000,
            feeder_max_run_ms=5000,
            feeder_stop_reason='max_run_timeout',
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'idle')
        self.assertEqual(result.json()['feeder_test_result']['stop_reason'], 'max_run_timeout')
        self.assertIsNone(views.APP_STATE['active_fruit_id'])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_001').exists())

    def test_production_feeder_timeout_stops_auto_run_and_reports_metrics(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        command = started['motor_command']

        response = self._report(
            'feeder_max_run_timeout',
            command_id=command['command_id'],
            feeder_elapsed_ms=5000,
            feeder_max_run_ms=command['feeder_max_run_ms'],
            feeder_stop_reason='max_run_timeout',
        )

        self.assertEqual(response.status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(state['auto_run_recovery_reason'], 'feeder_max_run_timeout')
        self.assertEqual(state['operator_alert']['reason'], 'feeder_max_run_timeout')
        self.assertEqual(state['operator_alert']['feeder_elapsed_ms'], 5000)
        self.assertEqual(
            state['operator_alert']['feeder_max_run_ms'],
            command['feeder_max_run_ms'],
        )
        self.assertEqual(state['operator_alert']['feeder_stop_reason'], 'max_run_timeout')
        self.assertIsNone(state['active_fruit_id'])

    def test_late_fruit_after_timeout_finishes_without_next_feed(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        feed_command = started['motor_command']
        self._report(
            'feeder_max_run_timeout',
            command_id=feed_command['command_id'],
            feeder_elapsed_ms=feed_command['feeder_max_run_ms'],
            feeder_max_run_ms=feed_command['feeder_max_run_ms'],
            feeder_stop_reason='max_run_timeout',
        )
        waiting = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'awaiting_fruit',
            'feeder_sensor_state': 'blocked',
            'last_feed_command_id': str(feed_command['command_id']),
        }).json()
        self.assertEqual(waiting['auto_trigger_enabled'], 1)

        station_1 = self._report(
            'hcsr04_station_1_ready',
            trigger_id='late-after-timeout-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        ).json()
        fruit_id = station_1['fruit_id']
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertEqual(
            self._upload_station(fruit_id, station_1['capture_token'], 1).status_code,
            200,
        )

        command = self._esp32_command()
        station_2 = self._report(
            'station_2_ready',
            station_index=2,
            command_id=command['command_id'],
        ).json()
        self.assertEqual(
            self._upload_station(fruit_id, station_2['capture_token'], 2).status_code,
            200,
        )

        command = self._esp32_command()
        station_3 = self._report(
            'station_3_ready',
            station_index=3,
            command_id=command['command_id'],
        ).json()
        self.assertEqual(
            self._upload_station(fruit_id, station_3['capture_token'], 3).status_code,
            200,
        )

        classified = self._post_json('/api/classify/', {'label': '上等'})
        self.assertTrue(classified.json()['sorter_command_queued'])
        self._complete_sorter()
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_production_feeder_sensor_not_clear_stops_auto_run(self):
        state = self._report_production_feed_failure(
            'feeder_sensor_not_clear',
            stop_reason='feeder_sensor_not_clear',
        )

        self.assertEqual(state['auto_run_recovery_reason'], 'feeder_sensor_not_clear')
        self.assertIn('大於 8.0 cm', state['operator_alert']['instruction'])

    def test_production_feeder_sensor_unavailable_stops_auto_run(self):
        state = self._report_production_feed_failure(
            'feeder_sensor_unavailable',
            stop_reason='feeder_sensor_unavailable',
        )

        self.assertEqual(state['auto_run_recovery_reason'], 'feeder_sensor_unavailable')
        self.assertIn('Echo 分壓', state['operator_alert']['instruction'])
        command = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'unavailable',
            'last_feed_command_id': '0',
        }).json()
        self.assertEqual(command['auto_trigger_enabled'], 0)

    def test_non_feeder_alert_omits_stale_feeder_metrics(self):
        views.APP_STATE['feeder_test_result'] = {
            'elapsed_ms': 5000,
            'max_run_ms': 5000,
            'stop_reason': 'max_run_timeout',
        }
        views.APP_STATE['last_error_reason'] = 'camera_upload_timeout'

        alert = self.client.get('/api/state/').json()['operator_alert']

        self.assertEqual(alert['reason'], 'camera_upload_timeout')
        self.assertIsNone(alert['feeder_elapsed_ms'])
        self.assertIsNone(alert['feeder_max_run_ms'])
        self.assertIsNone(alert['feeder_stop_reason'])

    def test_auto_run_stays_disabled_until_esp32_is_online(self):
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['can_start_auto_run'])
        self.assertEqual(
            state['auto_run_disabled_reason'],
            'esp32_offline',
        )
        response = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()['reason'],
            'esp32_offline',
        )

    def test_auto_run_requires_feeder_capability(self):
        self._prepare_auto_run(camera_ready=True)
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': '',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'feeder_capability_missing')

    def test_auto_run_requires_clear_feeder_sensor(self):
        self._prepare_auto_run(camera_ready=True)
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'blocked',
            'last_feed_command_id': '0',
        })

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'feeder_sensor_not_clear')

    def test_auto_run_rejects_unavailable_feeder_sensor(self):
        self._prepare_auto_run(camera_ready=True)
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'unavailable',
            'last_feed_command_id': '0',
        })

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'feeder_sensor_unavailable')

    def test_auto_run_requires_latest_timing_revision_ack(self):
        self._prepare_auto_run(camera_ready=True)
        updated = self._post_json('/api/capture_timing/', {
            **views.CAPTURE_TIMING_RECOMMENDED,
            'first_station_settle_ms': 350,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json()['capture_timing']['feeder_calibrated'])

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'feeder_timing_not_applied')

        self._report(
            'timing_config_applied',
            timing_revision=updated.json()['capture_timing_revision'],
        )
        started = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()['motor_command']['feed_context'], 'production')

    def test_auto_run_requires_current_feeder_calibration(self):
        self._prepare_auto_run(camera_ready=True)
        retest = self._post_json('/api/feeder/test/').json()['motor_command']
        self._report(
            'feed_cycle_completed',
            command_id=retest['command_id'],
            feeder_elapsed_ms=84,
            feeder_max_run_ms=retest['feeder_max_run_ms'],
            feeder_stop_reason='hcsr04',
        )

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'feeder_calibration_required')

    def test_auto_run_requires_capture_and_sorter_idle(self):
        self._prepare_auto_run(camera_ready=True)
        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '上等'})
        self.assertTrue(classified.json()['sorter_command_queued'])

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'classifier_busy')

    def test_auto_run_rejects_active_fruit(self):
        self._prepare_auto_run(camera_ready=True)
        triggered = self._report('hcsr04_trigger')
        self.assertEqual(triggered.status_code, 200)

        response = self._post_json('/api/auto_run/', {'enabled': True})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'active_fruit_exists')

    def test_auto_run_requires_fresh_live_camera_and_queues_production_feed(self):
        self._prepare_auto_run()

        with mock.patch.object(views.time, 'monotonic', return_value=100):
            heartbeat = self.client.get('/api/camera/state/', {'camera_ready': '1'})
            self.assertTrue(heartbeat.json()['camera_ready'])

        with mock.patch.object(views.time, 'monotonic', return_value=106):
            stale = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['reason'], 'camera_not_ready')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertEqual(
            self.client.get('/api/state/').json()['operator_alert']['location'],
            'camera station',
        )

        with mock.patch.object(views.time, 'monotonic', return_value=107):
            self.client.get('/api/camera/state/', {'camera_ready': '1'})
            started = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(started.status_code, 200)
        self.assertTrue(started.json()['auto_run_enabled'])
        self.assertEqual(started.json()['motor_command']['command'], 'feed_one')
        self.assertEqual(started.json()['motor_command']['feed_context'], 'production')

    def test_sorter_completion_is_the_only_boundary_that_queues_next_feed(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        feed_command_id = started['motor_command']['command_id']

        completed = self._report_feed_success(started['motor_command'])
        self.assertEqual(completed.status_code, 200)
        self.assertIsNone(views.APP_STATE['motor_command'])
        waiting = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'feeder_state': 'awaiting_fruit',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': str(feed_command_id),
        })
        self.assertEqual(waiting.status_code, 200)
        self.assertTrue(views.APP_STATE['auto_run_enabled'])

        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '上等'})
        self.assertEqual(classified.status_code, 200)
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'classify_fruit')

        views.APP_STATE['camera_last_live_frame_monotonic'] = (
            views.time.monotonic() - views.CAMERA_READY_WINDOW_SECONDS - 1
        )
        sorter_completed = self._complete_sorter()
        self.assertEqual(sorter_completed['event'], 'classification_sorter_completed')
        self.assertTrue(views.APP_STATE['auto_run_enabled'])
        self.assertTrue(views.APP_STATE['auto_feed_pending'])
        self.assertIsNone(views.APP_STATE['motor_command'])

        recovered = self.client.get('/api/camera/state/', {'camera_ready': '1'})
        self.assertTrue(recovered.json()['camera_ready'])
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'feed_one')
        self.assertEqual(views.APP_STATE['motor_command']['feed_context'], 'production')

    def test_esp32_restart_during_feed_stops_auto_run_without_resending(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        feed_command_id = started['motor_command']['command_id']

        restarted = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-restarted',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self.assertEqual(restarted.status_code, 200)
        self.assertEqual(restarted.json()['command'], 'none')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertEqual(views.APP_STATE['last_error_reason'], 'esp32_restarted_during_feed')
        self.assertNotEqual(views.APP_STATE['esp32_last_feed_command_id'], feed_command_id)
        alert = self.client.get('/api/state/').json()['operator_alert']
        self.assertEqual(alert['reason'], 'esp32_restarted_during_feed')
        self.assertEqual(alert['command_id'], feed_command_id)

        recovered = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(recovered.status_code, 200)
        self.assertTrue(recovered.json()['auto_run_enabled'])
        self.assertEqual(recovered.json()['motor_command']['command'], 'feed_one')

    def test_hcsr04_event_remains_available_after_esp32_restarts_during_feed(self):
        self._prepare_auto_run(camera_ready=True)
        self._post_json('/api/auto_run/', {'enabled': True})
        self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-restarted',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'last_feed_command_id': '0',
        })

        detected = self._report('hcsr04_trigger')
        self.assertEqual(detected.status_code, 200)
        self.assertEqual(detected.json()['active_fruit_id'], 'fruit_001')
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'start_sequence')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])

    def test_awaiting_fruit_poll_rebuilds_unconfirmed_feed_without_resending(self):
        self._prepare_auto_run(camera_ready=True)
        response = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-after-django-restart',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'awaiting_fruit',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '17',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['command'], 'none')
        self.assertEqual(response.json()['auto_trigger_enabled'], 1)
        self.assertEqual(views.APP_STATE['status'], 'waiting_fruit')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertEqual(views.APP_STATE['esp32_last_feed_command_id'], 17)
        self.assertEqual(
            self.client.get('/api/state/').json()['operator_alert']['command_id'],
            17,
        )

        stale = self._report('feed_cycle_completed', command_id=17)
        self.assertEqual(stale.status_code, 200)
        self.assertTrue(stale.json()['ignored'])
        self.assertEqual(views.APP_STATE['status'], 'waiting_fruit')
        self.assertIsNone(views.APP_STATE['motor_command'])

        recovered = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(recovered.status_code, 200)
        recovery_command_id = recovered.json()['motor_command']['command_id']
        dispatch = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-after-django-restart',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'awaiting_fruit',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '17',
        })
        self.assertEqual(dispatch.json()['command_id'], recovery_command_id)
        self.assertTrue(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['auto_run_recovery_reason'])

    def test_removed_fruit_arrival_delayed_report_is_rejected(self):
        response = self._report('fruit_arrival_delayed', command_id=0)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['reason'], 'unknown_event')
        self.assertIsNone(views.APP_STATE['last_error_reason'])
        self.assertNotIn(
            'fruit_arrival_delayed',
            [entry['event'] for entry in views.APP_STATE['transition_trace']],
        )

    def test_graceful_pause_keeps_current_feed_and_stops_after_current_fruit(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        feed_command = dict(started['motor_command'])

        paused = self._post_json('/api/auto_run/', {'enabled': False})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()['auto_run_enabled'])
        self.assertEqual(views.APP_STATE['motor_command'], feed_command)

        self._report_feed_success(feed_command)
        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '中等'})
        sorter_command = dict(views.APP_STATE['motor_command'])
        self.assertTrue(classified.json()['sorter_command_queued'])
        self.assertEqual(sorter_command['command'], 'classify_fruit')

        sorter_completed = self._complete_sorter()
        self.assertEqual(sorter_completed['event'], 'classification_sorter_completed')
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_graceful_pause_during_capture_does_not_replace_capture_command(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        self._report_feed_success(started['motor_command'])
        triggered = self._report('hcsr04_trigger')
        self.assertEqual(triggered.status_code, 200)
        capture_command = dict(views.APP_STATE['motor_command'])

        paused = self._post_json('/api/auto_run/', {'enabled': False})
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(views.APP_STATE['motor_command'], capture_command)
        self.assertEqual(capture_command['command'], 'start_sequence')

    def test_graceful_pause_after_classification_keeps_sorter_command_and_stops_next_feed(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        self._report_feed_success(started['motor_command'])
        self._complete_auto_session('fruit_001')
        classified = self._post_json('/api/classify/', {'label': '加工'})
        self.assertTrue(classified.json()['sorter_command_queued'])
        sorter_command = dict(views.APP_STATE['motor_command'])

        paused = self._post_json('/api/auto_run/', {'enabled': False})
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()['auto_run_finishing'])
        self.assertEqual(views.APP_STATE['motor_command'], sorter_command)

        self._complete_sorter()
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertFalse(views.APP_STATE['auto_run_finishing'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_graceful_pause_after_gate_three_still_allows_mg996r_sorting(self):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        self._report_feed_success(started['motor_command'])
        self._complete_auto_session('fruit_001')
        self.assertEqual(views.APP_STATE['status'], 'uploaded')

        paused = self._post_json('/api/auto_run/', {'enabled': False})
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()['auto_run_finishing'])

        classified = self._post_json('/api/classify/', {'label': '下等'})
        self.assertTrue(classified.json()['sorter_command_queued'])
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'classify_fruit')
        self._complete_sorter()
        self.assertFalse(views.APP_STATE['auto_run_finishing'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_empty_temp_folder_does_not_lock_auto_trigger(self):
        stale_dir = self.dataset_root / 'temp' / 'fruit_001'
        stale_dir.mkdir(parents=True)

        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(stale_dir.exists())
        self.assertIsNone(payload['active_fruit_id'])
        self.assertEqual(payload['image_total'], 0)
        self.assertNotIn('can_manual_capture', payload)
        self.assertFalse(payload['can_discard'])
        self.assertFalse(payload['can_classify'])

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
        self.assertNotIn('can_manual_capture', payload)
        self.assertTrue(payload['can_discard'])
        self.assertFalse(payload['can_classify'])

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(partial_dir.exists())
        self.assertNotIn('can_manual_capture', response.json())

    def test_reset_dataset_clears_all_data_and_counter(self):
        response = self._post_json('/api/set_counter/', {'start_id': 7})
        self.assertEqual(response.status_code, 200)
        self._complete_auto_session('fruit_007')
        response = self._post_json('/api/classify/', {'label': '加工', 'note': 'reset test'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / '加工' / 'fruit_007').exists())

        self._complete_sorter()

        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['reset_done'])
        self.assertEqual(payload['next_fruit_id'], 'fruit_001')
        self.assertNotIn('can_manual_capture', payload)
        self.assertFalse((self.dataset_root / '加工' / 'fruit_007').exists())

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [views.METADATA_FIELDNAMES])
        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 1)

    def test_metadata_header_is_upgraded_from_old_schema(self):
        metadata_path = self.dataset_root / 'metadata.csv'
        metadata_path.write_text(
            'fruit_id,label,capture_time,path,note\nfruit_001,上等,2026-07-06 10:00:00,上等/fruit_001,old note\n',
            encoding='utf-8-sig',
        )

        views._reset_dataset_caches()
        views._ensure_dataset_structure()

        with metadata_path.open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual(rows[0]['fruit_id'], 'fruit_001')
        self.assertIn('capture_count', rows[0])
        self.assertEqual(rows[0]['note'], 'old note')

    def test_webrtc_signaling_round_trip(self):
        offer = {'type': 'offer', 'sdp': 'v=0\r\n'}
        answer = {'type': 'answer', 'sdp': 'v=0\r\n'}
        dashboard_candidate = {'candidate': 'candidate:dashboard', 'sdpMid': '0', 'sdpMLineIndex': 0}
        camera_candidate = {'candidate': 'candidate:camera', 'sdpMid': '0', 'sdpMLineIndex': 0}
        camera_candidate_2 = {'candidate': 'candidate:camera-2', 'sdpMid': '0', 'sdpMLineIndex': 0}

        offer_response = self._post_json('/api/webrtc/offer', {'offer': offer})
        self.assertEqual(offer_response.status_code, 200)
        self.assertEqual(offer_response.json()['offer_id'], 1)
        self.assertEqual(self._post_json('/api/webrtc/ice', {
            'role': 'dashboard',
            'candidate': dashboard_candidate,
        }).status_code, 200)
        answer_response = self._post_json('/api/webrtc/answer', {'answer': answer})
        self.assertEqual(answer_response.status_code, 200)
        self.assertEqual(answer_response.json()['answer_id'], 1)
        self.assertEqual(self._post_json('/api/webrtc/ice', {
            'role': 'camera',
            'candidate': camera_candidate,
        }).status_code, 200)

        response = self.client.get('/api/webrtc/state')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['offer'], offer)
        self.assertEqual(payload['offer_id'], 1)
        self.assertEqual(payload['answer'], answer)
        self.assertEqual(payload['answer_id'], 1)
        self.assertEqual(payload['dashboard_ice'], [dashboard_candidate])
        self.assertEqual(payload['camera_ice'], [camera_candidate])
        self.assertEqual(payload['dashboard_ice_total'], 1)
        self.assertEqual(payload['camera_ice_total'], 1)
        self.assertTrue(payload['offer_present'])
        self.assertTrue(payload['answer_present'])

        self.assertEqual(self._post_json('/api/webrtc/ice', {
            'role': 'camera',
            'candidate': camera_candidate_2,
        }).status_code, 200)
        response = self.client.get(
            '/api/webrtc/state?dashboard_ice_from=1&camera_ice_from=1&known_offer_id=1&known_answer_id=1'
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload['offer'])
        self.assertIsNone(payload['answer'])
        self.assertEqual(payload['dashboard_ice'], [])
        self.assertEqual(payload['camera_ice'], [camera_candidate_2])
        self.assertEqual(payload['dashboard_ice_total'], 1)
        self.assertEqual(payload['camera_ice_total'], 2)

        new_offer = {'type': 'offer', 'sdp': 'v=0\r\nnew\r\n'}
        new_offer_response = self._post_json('/api/webrtc/offer', {'offer': new_offer})
        self.assertEqual(new_offer_response.status_code, 200)
        self.assertEqual(new_offer_response.json()['offer_id'], 2)
        response = self.client.get('/api/webrtc/state')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['offer'], new_offer)
        self.assertEqual(payload['offer_id'], 2)
        self.assertIsNone(payload['answer'])
        self.assertEqual(payload['answer_id'], 0)
        self.assertEqual(payload['dashboard_ice'], [])
        self.assertEqual(payload['camera_ice'], [])
        self.assertFalse(payload['answer_present'])

    def test_pages_render(self):
        home_response = self.client.get('/')
        self.assertEqual(home_response.status_code, 200)
        self.assertContains(home_response, '電腦 Dashboard')
        self.assertContains(home_response, '手機 Camera')

        dashboard_response = self.client.get('/dashboard/')
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, '不要開啟 /dashboard/')
        self.assertContains(dashboard_response, '三張原圖預覽')
        self.assertContains(dashboard_response, 'id="hardware-mode"')
        self.assertContains(dashboard_response, '<option value="detection">', html=False)
        self.assertContains(dashboard_response, 'id="detection-results"', html=False)
        for label in ('上等', '中等', '下等', '加工'):
            self.assertContains(dashboard_response, f'data-label="{label}"')
        self.assertNotContains(dashboard_response, 'data-label="上中等"')
        self.assertNotContains(dashboard_response, 'data-label="廢棄"')
        self.assertEqual(dashboard_response['Cache-Control'], 'no-store, max-age=0')

        camera_response = self.client.get('/camera/')
        self.assertEqual(camera_response.status_code, 200)
        self.assertContains(camera_response, '開啟相機')
        self.assertContains(camera_response, '安全來源')
        self.assertContains(camera_response, '/static/fruit_app/js/camera.js')
        self.assertEqual(camera_response['Cache-Control'], 'no-store, max-age=0')

    def test_dashboard_renders_calibrated_timing_defaults(self):
        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="timing-first-station" type="number" min="50" max="3000" step="50" inputmode="numeric" value="300"')
        self.assertContains(response, 'id="timing-servo" type="number" min="50" max="3000" step="50" inputmode="numeric" value="200"')
        self.assertContains(response, 'id="timing-fruit" type="number" min="50" max="3000" step="50" inputmode="numeric" value="350"')
        self.assertContains(response, 'id="timing-final-return" type="number" min="0" max="3000" step="50" inputmode="numeric" value="300"')
        self.assertContains(response, 'id="timing-idle-command-poll" type="number" min="100" max="5000" step="50" inputmode="numeric" value="250"')
        self.assertContains(response, '推薦：300 ms', count=2)
        self.assertContains(response, '推薦：200 ms', count=1)
        self.assertContains(response, '推薦：350 ms', count=1)

    def test_fast_station_one_report_opens_camera_without_start_sequence(self):
        response = self._report(
            'hcsr04_station_1_ready',
            trigger_id='fast-001',
            gates_home='1',
            station_settled='true',
            station_index=1,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['accepted'])
        self.assertTrue(payload['fast_path'])
        self.assertEqual(payload['status'], 'waiting_camera')
        self.assertTrue(payload['capture_requested'])
        self.assertEqual(payload['station_index'], 1)
        self.assertEqual(payload['motor_command']['command'], 'none')
        self.assertTrue((self.dataset_root / 'temp' / payload['fruit_id']).exists())

        command = self._esp32_command()
        self.assertEqual(command['command'], 'none')
        self.assertEqual(command['fruit_id'], payload['fruit_id'])
        upload_response = self._upload_station(payload['fruit_id'], payload['capture_token'], 1)
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()['motor_command']['command'], 'release_gate')

    def test_fast_station_one_retry_reuses_same_fruit_and_capture_token(self):
        first = self._report(
            'hcsr04_station_1_ready',
            trigger_id='retry-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()

        retry = self._report(
            'hcsr04_station_1_ready',
            trigger_id='retry-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        )
        self.assertEqual(retry.status_code, 200)
        retry_payload = retry.json()
        self.assertTrue(retry_payload['ok'])
        self.assertTrue(retry_payload['duplicate'])
        self.assertEqual(retry_payload['status'], 'waiting_camera')
        self.assertTrue(retry_payload['capture_requested'])
        self.assertEqual(retry_payload['fruit_id'], first_payload['fruit_id'])
        self.assertEqual(retry_payload['capture_token'], first_payload['capture_token'])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())

    def test_fast_station_one_rejects_unsafe_report_with_legacy_fallback(self):
        response = self._report(
            'hcsr04_station_1_ready',
            trigger_id='unsafe-001',
            gates_home=0,
            station_settled=1,
            station_index=1,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['fallback_to_legacy'])
        self.assertEqual(payload['reason'], 'fast_path_unsafe')
        self.assertIsNone(payload['active_fruit_id'])

        legacy = self._report('hcsr04_trigger')
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json()['motor_command']['command'], 'start_sequence')

    def test_fast_station_one_requires_explicit_station_one(self):
        response = self._report(
            'hcsr04_station_1_ready',
            trigger_id='missing-station-001',
            gates_home=1,
            station_settled=1,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['fallback_to_legacy'])
        self.assertEqual(payload['reason'], 'fast_path_unsafe')
        self.assertIsNone(payload['active_fruit_id'])

    def test_fast_station_one_respects_feature_switch(self):
        with override_settings(ENABLE_AUTO_STATION_1_FAST_PATH=False):
            response = self._report(
                'hcsr04_station_1_ready',
                trigger_id='disabled-001',
                gates_home=1,
                station_settled=1,
                station_index=1,
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['fallback_to_legacy'])
        self.assertEqual(payload['reason'], 'fast_path_disabled')

    def test_fast_station_one_is_ignored_when_auto_trigger_is_locked(self):
        first = self._report(
            'hcsr04_station_1_ready',
            trigger_id='locked-first-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()

        locked = self._report(
            'hcsr04_station_1_ready',
            trigger_id='locked-second-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        )
        self.assertEqual(locked.status_code, 200)
        locked_payload = locked.json()
        self.assertTrue(locked_payload['ok'])
        self.assertTrue(locked_payload['ignored'])
        self.assertFalse(locked_payload['fallback_to_legacy'])
        self.assertEqual(locked_payload['reason'], 'active_fruit_exists')
        self.assertEqual(locked_payload['active_fruit_id'], first_payload['fruit_id'])
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())

    def test_camera_state_is_minimal_no_store_and_uses_revision(self):
        fast = self._report(
            'hcsr04_station_1_ready',
            trigger_id='camera-state-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        ).json()

        response = self.client.get('/api/camera/state/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')
        payload = response.json()
        self.assertEqual(payload['fruit_id'], fast['fruit_id'])
        self.assertEqual(payload['active_fruit_id'], fast['fruit_id'])
        self.assertEqual(payload['capture_token'], fast['capture_token'])
        self.assertEqual(payload['station_index'], 1)
        self.assertTrue(payload['capture_requested'])
        self.assertEqual(payload['capture']['token'], fast['capture_token'])
        self.assertGreater(payload['revision'], 0)
        self.assertNotIn('latest_images', payload)

    def test_transition_trace_keeps_sensor_to_atomic_save_timeline(self):
        fast = self._report(
            'hcsr04_station_1_ready',
            trigger_id='trace-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        ).json()
        capture_started = self._post_json('/api/capture_started/', {
            'fruit_id': fast['fruit_id'],
            'capture_token': fast['capture_token'],
            'station_index': 1,
            'client_timing': {
                'request_received_at_ms': 2,
                'frame_drawn_at_ms': 12,
                'blob_ready_at_ms': 20,
                'frame_to_blob_ready_ms': 8,
            },
        })
        self.assertEqual(capture_started.status_code, 200)

        upload = self._upload_station(fast['fruit_id'], fast['capture_token'], 1)
        self.assertEqual(upload.status_code, 200)
        payload = upload.json()
        events = [entry['event'] for entry in payload['transition_trace']]
        self.assertIn('hcsr04_station_1_ready_received', events)
        self.assertIn('fast_path_station_1_capture_requested', events)
        self.assertIn('phone_capture_started', events)
        self.assertIn('station_image_saved', events)
        self.assertIn('motor_command_issued', events)
        self.assertEqual(payload['timing']['client_timing']['frame_drawn_at_ms'], 12.0)
        self.assertEqual(payload['timing']['client_timing']['frame_to_blob_ready_ms'], 8.0)

    def test_late_capture_started_telemetry_does_not_duplicate_release_command(self):
        fast = self._report(
            'hcsr04_station_1_ready',
            trigger_id='late-telemetry-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        ).json()
        upload = self._upload_station(fast['fruit_id'], fast['capture_token'], 1)
        self.assertEqual(upload.status_code, 200)
        release = upload.json()['motor_command']
        self.assertEqual(release['command'], 'release_gate')

        late = self._post_json('/api/capture_started/', {
            'fruit_id': fast['fruit_id'],
            'capture_token': fast['capture_token'],
            'station_index': 1,
            'client_timing': {'upload_started_at_ms': 25},
        })
        self.assertEqual(late.status_code, 200)
        command_after_late_telemetry = self._esp32_command()
        self.assertEqual(command_after_late_telemetry['command'], 'release_gate')
        self.assertEqual(command_after_late_telemetry['command_id'], release['command_id'])

    def test_camera_state_hot_path_does_not_reconcile_or_read_dataset_files(self):
        self.client.get('/api/camera/state/')
        with (
            mock.patch.object(views, '_sync_active_state_with_filesystem') as sync_state,
            mock.patch.object(views, '_read_counter') as read_counter,
            mock.patch.object(views, '_ensure_metadata_header') as ensure_metadata,
            mock.patch.object(views, '_retry_deferred_discards_if_due') as retry_cleanup,
        ):
            response = self.client.get('/api/camera/state/')

        self.assertEqual(response.status_code, 200)
        sync_state.assert_not_called()
        read_counter.assert_not_called()
        ensure_metadata.assert_not_called()
        retry_cleanup.assert_not_called()

    def test_esp32_report_payload_is_compact_and_firmware_compatible(self):
        response = self._report(
            'hcsr04_station_1_ready',
            trigger_id='compact-001',
            gates_home=1,
            station_settled=1,
            station_index=1,
        )
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['capture_requested'])
        self.assertEqual(payload['fruit_id'], 'fruit_001')
        self.assertEqual(payload['server_status'], 'waiting_camera')
        self.assertNotIn('latest_images', payload)
        self.assertNotIn('transition_trace', payload)
        self.assertNotIn('labels', payload)
        self.assertNotIn('timing', payload)

    def test_dataset_operation_releases_state_lock_and_rejects_duplicate_mutation(self):
        self._mark_esp32_online()
        fruit_id = 'fruit_001'
        fruit_dir = self.dataset_root / 'temp' / fruit_id
        fruit_dir.mkdir(parents=True)
        for filename in views.IMAGE_FILENAMES:
            (fruit_dir / filename).write_bytes(b'jpeg')
        with views.STATE_LOCK:
            views.APP_STATE['active_fruit_id'] = fruit_id
            views.APP_STATE['status'] = 'uploaded'
            views.APP_STATE['capture_time'] = views._now_string()
            views.APP_STATE['gate3_waiting_boot_id'] = views.APP_STATE['esp32_boot_id']

        operation_started = threading.Event()
        allow_operation_to_finish = threading.Event()
        result = {}

        def slow_move(src, dest):
            shutil.move(str(src), str(dest))
            operation_started.set()
            allow_operation_to_finish.wait(timeout=2)

        def classify_in_thread():
            client = Client()
            result['response'] = client.post(
                '/api/classify/',
                data=json.dumps({'label': '上等'}),
                content_type='application/json',
            )

        with mock.patch.object(views, '_safe_move', side_effect=slow_move):
            worker = threading.Thread(target=classify_in_thread)
            worker.start()
            self.assertTrue(operation_started.wait(timeout=1))

            camera_response = self.client.get('/api/camera/state/')
            command_payload = self._esp32_command()
            duplicate_response = self._post_json('/api/discard/')

            allow_operation_to_finish.set()
            worker.join(timeout=2)

        self.assertEqual(camera_response.status_code, 200)
        self.assertEqual(command_payload['command'], 'none')
        self.assertEqual(command_payload['auto_trigger_enabled'], 0)
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()['reason'], 'dataset_busy')
        self.assertEqual(result['response'].status_code, 200)
        self.assertIsNone(views.APP_STATE.get('dataset_operation'))

    def test_dataset_image_streams_with_cache_validator(self):
        fruit_dir = self.dataset_root / 'temp' / 'fruit_001'
        fruit_dir.mkdir(parents=True)
        image_path = fruit_dir / 'img_01.jpg'
        image_path.write_bytes(b'jpeg-content')

        response = self.client.get('/api/image/fruit_001/img_01.jpg/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn('ETag', response)
        self.assertIn('Last-Modified', response)
        self.assertEqual(response['Cache-Control'], 'private, max-age=3600')
        self.assertEqual(b''.join(response.streaming_content), b'jpeg-content')
        response.close()

        cached = self.client.get(
            '/api/image/fruit_001/img_01.jpg/',
            HTTP_IF_NONE_MATCH=response['ETag'],
        )
        self.assertEqual(cached.status_code, 304)
        cached.close()

    def test_pages_load_external_static_assets(self):
        camera = self.client.get('/camera/').content.decode('utf-8')
        dashboard = self.client.get('/dashboard/').content.decode('utf-8')
        self.assertIn('/static/fruit_app/js/camera.js', camera)
        self.assertIn('/static/fruit_app/css/camera.css', camera)
        self.assertIn('/static/fruit_app/js/dashboard.js', dashboard)
        self.assertIn('/static/fruit_app/css/dashboard.css', dashboard)
        self.assertNotIn('<style>', camera)
        self.assertNotIn('<style>', dashboard)

    def test_firmware_classifier_contract_is_non_blocking_and_isolated(self):
        firmware_root = Path(__file__).resolve().parents[2] / 'firmware' / 'Three_Gate_Data_Collection'
        config = (firmware_root / 'Config.h').read_text(encoding='utf-8')
        classifier_header = (firmware_root / 'ClassifierController.h').read_text(encoding='utf-8')
        classifier_source = (firmware_root / 'ClassifierController.cpp').read_text(encoding='utf-8')
        sequence_header = (firmware_root / 'ClassificationSequence.h').read_text(encoding='utf-8')
        sequence_source = (firmware_root / 'ClassificationSequence.cpp').read_text(encoding='utf-8')
        api_source = (firmware_root / 'DjangoApiClient.cpp').read_text(encoding='utf-8')
        capture_source = (firmware_root / 'CaptureController.cpp').read_text(encoding='utf-8')

        for contract in (
            'kGatePins[kGateCount] = {18, 19, 21}',
            'kClassifierPin = 25',
            'kIdleCommandPollIntervalMS = 250UL',
        ):
            self.assertIn(contract, config)
        for code in ('high', 'medium', 'low', 'processing', 'high_medium', 'discard'):
            self.assertIn(f'classificationCode, "{code}"', sequence_source)
        self.assertIn('classificationCode, "high_medium"', sequence_source)
        self.assertIn('classificationCode, "discard"', sequence_source)
        for state in (
            'kUninitialized',
            'kBootHomeSettling',
            'kIdleHome',
            'kRunning',
            'kPermanentInitializationError',
        ):
            self.assertIn(state, classifier_header)
        self.assertIn('GateController', classifier_header + classifier_source)
        self.assertIn('kSorterPositionSettleMS = 500UL', sequence_header)
        self.assertIn('kFruitDropHoldMS = 1000UL', sequence_header)
        self.assertIn('kJointHomeSettleMS = 500UL', sequence_header)
        self.assertIn('kSorterHomeAngle = 85', sequence_header)
        for angle in ('return 55;', 'return 70;', 'return 100;', 'return 115;'):
            self.assertIn(angle, sequence_source)
        self.assertIn('Action::kReleaseGate3', sequence_source)
        self.assertIn('Action::kHomeAll', sequence_source)
        self.assertNotIn('delay(', classifier_source)
        self.assertNotIn('delay(', sequence_source)
        self.assertIn('readTextValue(body, "station_index").toInt()', api_source)
        self.assertIn('classification_sorter_completed', capture_source)
        self.assertIn('classification_sorter_failed', capture_source)
        self.assertIn('&sorter_capability=gate3_sorter_v1', api_source)
        self.assertIn('classifier_.available()', capture_source)
        self.assertIn('return appliedTiming_.idleCommandPollIntervalMS;', capture_source)
        self.assertIn('timing.idleCommandPollIntervalMS >= 100UL', capture_source)
        self.assertIn('timing.idleCommandPollIntervalMS <= 5000UL', capture_source)
        self.assertIn('readTextValue(body, "idle_command_poll_interval_ms")', api_source)
        self.assertIn('command.feedContext == "production"', capture_source)
        self.assertIn('feederSensorState_', capture_source)
        self.assertIn('"&feeder_sensor_state="', api_source)
        self.assertNotIn('"fruit_arrival_delayed"', capture_source)

    def test_dashboard_has_sorter_status_and_single_in_flight_guard(self):
        project_root = Path(__file__).resolve().parents[2]
        dashboard_js = (
            project_root / 'Django_Server' / 'fruit_app' / 'static' / 'fruit_app' / 'js' / 'dashboard.js'
        ).read_text(encoding='utf-8')
        dashboard_html = (
            project_root / 'Django_Server' / 'fruit_app' / 'templates' / 'dashboard.html'
        ).read_text(encoding='utf-8')
        self.assertIn('let classificationInFlight = false', dashboard_js)
        self.assertIn('if (classificationInFlight)', dashboard_js)
        self.assertIn('data.sorter_busy', dashboard_js)
        self.assertIn("pending: '等待 ESP32'", dashboard_js)
        self.assertIn("running: 'MG996R 執行中'", dashboard_js)
        self.assertIn('id="sorter-status"', dashboard_html)
        self.assertIn('id="sorter-error"', dashboard_html)
        self.assertIn('role="alert"', dashboard_html)
        self.assertIn("'優雅暫停'", dashboard_js)
        self.assertIn("'正在完成目前果實'", dashboard_js)
        self.assertIn("'開始執行'", dashboard_js)
        self.assertIn('data.can_start_auto_run', dashboard_js)
        self.assertIn('data.operator_alert', dashboard_js)
        self.assertIn('自行暫停、排除狀況後重新開始', dashboard_js)
        self.assertIn('alert.feeder_elapsed_ms', dashboard_js)
        self.assertIn('alert.feeder_max_run_ms', dashboard_js)
        self.assertIn('alert.feeder_stop_reason', dashboard_js)
        self.assertIn('feeder_max_run_ms: 5000', dashboard_js)
        self.assertIn("1000,\n                20000,\n                500,", dashboard_js)
        self.assertIn(
            'id="timing-feeder-max-run" type="number" min="1000" max="20000" step="500"',
            dashboard_html,
        )
        self.assertIn('推薦：5000 ms', dashboard_html)
        self.assertIn('警告 : 若設定過長將導致連續送料之情形發生', dashboard_html)
        self.assertIn('送料結果', dashboard_html)
        self.assertIn('我已目視確認本次測試恰好送出一顆', dashboard_html)
        self.assertNotIn('待實機驗證', dashboard_js)
        self.assertNotIn('待實機驗證', dashboard_html)
        self.assertNotIn('順時針且單次約 90°', dashboard_html)

    def test_dashboard_saves_feeder_confirmation_once_and_preserves_video_ratio(self):
        project_root = Path(__file__).resolve().parents[2]
        static_root = project_root / 'Django_Server' / 'fruit_app' / 'static' / 'fruit_app'
        dashboard_js = (static_root / 'js' / 'dashboard.js').read_text(encoding='utf-8')
        dashboard_css = (static_root / 'css' / 'dashboard.css').read_text(encoding='utf-8')
        dashboard_html = (
            project_root / 'Django_Server' / 'fruit_app' / 'templates' / 'dashboard.html'
        ).read_text(encoding='utf-8')

        confirmation_handler = dashboard_js[
            dashboard_js.index('async function confirmFeederCalibration()'):
            dashboard_js.index('function transitionTraceFromState')
        ]
        timing_payload = dashboard_js[
            dashboard_js.index('function captureTimingPayloadFromInputs()'):
            dashboard_js.index('function restoreRecommendedTiming()')
        ]
        self.assertIn("postJson('/api/feeder/calibration/confirm/'", confirmation_handler)
        self.assertNotIn("postJson('/api/feeder/test/'", confirmation_handler)
        self.assertNotIn('feeder_calibrated:', timing_payload)
        self.assertIn("feederCalibratedInput.addEventListener('change'", dashboard_js)
        self.assertIn('id="feeder-help-button"', dashboard_html)
        self.assertIn('id="feeder-calibration-status"', dashboard_html)
        self.assertIn('勾選會立即保存，不用再次測試或再次套用', dashboard_html)
        self.assertIn('我已目視確認本次測試恰好送出一顆', dashboard_html)
        self.assertNotIn('aspect-ratio: 16 / 9', dashboard_css)
        self.assertIn('align-items: start;', dashboard_css)
        self.assertIn('align-content: start;', dashboard_css)
        self.assertIn('video {', dashboard_css)
        self.assertIn('max-width: 100%;', dashboard_css)
        self.assertIn('height: auto;', dashboard_css)
        self.assertIn('max-height: min(52vh, 520px);', dashboard_css)
        self.assertIn('.remote-video-wrap.is-streaming', dashboard_css)

    def test_dashboard_presents_accessible_operator_workspace(self):
        project_root = Path(__file__).resolve().parents[2]
        static_root = project_root / 'Django_Server' / 'fruit_app' / 'static' / 'fruit_app'
        dashboard_js = (static_root / 'js' / 'dashboard.js').read_text(encoding='utf-8')
        dashboard_css = (static_root / 'css' / 'dashboard.css').read_text(encoding='utf-8')
        dashboard_html = (
            project_root / 'Django_Server' / 'fruit_app' / 'templates' / 'dashboard.html'
        ).read_text(encoding='utf-8')

        self.assertIn('百香果資料蒐集控制台', dashboard_html)
        self.assertIn('id="camera-ready-status"', dashboard_html)
        self.assertIn('id="operator-alert" class="operator-alert" role="alert" hidden', dashboard_html)
        self.assertIn('id="message" class="global-message" role="status"', dashboard_html)
        self.assertIn('<label for="note">分類備註</label>', dashboard_html)
        self.assertIn('<label for="counter-input">下一筆資料起始 ID</label>', dashboard_html)
        self.assertIn('<fieldset>', dashboard_html)
        self.assertIn('<legend id="classification-title">人工分類</legend>', dashboard_html)
        for station_index in range(1, 4):
            self.assertIn(f'id="station-card-{station_index}"', dashboard_html)
            self.assertIn(f'id="station-image-{station_index}"', dashboard_html)
        for details_id in ('capture-settings', 'diagnostics', 'dataset-management'):
            self.assertIn(f'<details id="{details_id}"', dashboard_html)
        self.assertIn('class="danger-zone"', dashboard_html)

        for variable in (
            '--background: #f3f6f4',
            '--surface: #ffffff',
            '--primary: #176b4a',
            '--warning: #8a4b08',
            '--danger: #b42318',
            '--focus: #0b6edc',
        ):
            self.assertIn(variable, dashboard_css)
        self.assertIn('button:focus-visible', dashboard_css)
        self.assertIn('@media (max-width: 640px)', dashboard_css)
        self.assertIn('.station-media img {', dashboard_css)
        self.assertIn('object-fit: contain;', dashboard_css)

        self.assertIn("camera_ready ? '可拍攝' : '未就緒'", dashboard_js)
        self.assertIn('function renderReadiness(data)', dashboard_js)
        self.assertIn(
            'data.esp32_online && data.esp32_feeder_capable && data.esp32_sorter_capable',
            dashboard_js,
        )
        self.assertIn('gate3_sorter_v1', dashboard_js)
        self.assertIn('function renderOperatorAlert(alert)', dashboard_js)
        self.assertIn("idle: '閒置'", dashboard_js)
        self.assertIn("unavailable: { label: '無有效回音', tone: 'error' }", dashboard_js)
        self.assertIn('fingerprint === lastAlertFingerprint', dashboard_js)
        self.assertIn('fingerprint === lastTraceFingerprint', dashboard_js)
        self.assertIn("beginControlAction('auto-run', autoRunButton)", dashboard_js)
        self.assertIn('stationImages.forEach((imageElement, index)', dashboard_js)
        self.assertIn('await sleep(120)', dashboard_js)

    def test_firmware_boot_id_uses_per_boot_hardware_randomness(self):
        project_root = Path(__file__).resolve().parents[2]
        api_source = (
            project_root
            / 'firmware'
            / 'Three_Gate_Data_Collection'
            / 'DjangoApiClient.cpp'
        ).read_text(encoding='utf-8')
        self.assertIn('esp_random()', api_source)

    def test_firmware_feeder_uses_hcsr04_with_max_timeout_priority(self):
        source = (
            Path(__file__).resolve().parents[2]
            / 'firmware'
            / 'Three_Gate_Data_Collection'
            / 'CaptureController.cpp'
        ).read_text(encoding='utf-8')
        tick = source[source.index('void CaptureController::tick()'):source.index(
            'void CaptureController::advanceMotion'
        )]
        self.assertLess(tick.index('advanceMotion(currentTime)'), tick.index('handleSensor(currentTime)'))
        self.assertIn('stopFeeder("feeder_max_run_timeout"', source)
        self.assertIn('stopFeeder("feed_cycle_completed", "hcsr04"', source)
        self.assertIn('feederSensorState_ = distanceCM <= 0.0F', source)
        start_feeder = source[source.index('void CaptureController::startFeeder'):source.index(
            'void CaptureController::stopFeeder'
        )]
        self.assertLess(
            start_feeder.index('sensor_.readCentimeters()'),
            start_feeder.index('feeder_.writeMicroseconds(activeTiming_.feederDriveUS)'),
        )
        self.assertLess(
            start_feeder.index('sensor_.readCentimeters()'),
            start_feeder.index('lastSensorReadAt_ = millis();'),
        )
        self.assertLess(
            start_feeder.index('lastSensorReadAt_ = millis();'),
            start_feeder.index('feeder_.writeMicroseconds(activeTiming_.feederDriveUS)'),
        )
        self.assertIn('if (stopReason == "hcsr04")', source)
        self.assertIn('triggerArmed_ = false;', source)
        self.assertIn('latchAutoTrigger(stoppedAt)', source)
        self.assertIn(
            'awaitingFruitAfterTimeout_ ? "awaiting_fruit" : "idle"',
            source,
        )
        self.assertIn(
            'awaitingFruitAfterTimeout_ &&\n      !autoTrigger_.active()',
            source,
        )
        self.assertIn('const bool feederHandoff =', source)
        self.assertIn('if (!autoTrigger_.active()) {\n      clearActiveTiming();', source)
        sensor_branch = source[
            source.index('if (motionPhase_ == MotionPhase::kFeederDriving)'):
            source.index('String blockedReason;')
        ]
        self.assertLess(
            sensor_branch.index('timeReached(sensorReadFinishedAt, phaseDeadlineAt_)'),
            sensor_branch.index('feederSensorState_ == "unavailable"'),
        )
        start_feeder = source[
            source.index('void CaptureController::startFeeder'):
            source.index('void CaptureController::stopFeeder')
        ]
        self.assertLess(
            start_feeder.index('feeder_.writeMicroseconds(activeTiming_.feederDriveUS)'),
            start_feeder.index('feederStartedAt_ = millis()'),
        )
        config = (
            Path(__file__).resolve().parents[2]
            / 'firmware'
            / 'Three_Gate_Data_Collection'
            / 'Config.h'
        ).read_text(encoding='utf-8')
        self.assertIn('kFeederMaxRunMS = 5000UL', config)
        self.assertIn('timing.feederMaxRunMS >= 1000UL', source)
        self.assertIn('timing.feederMaxRunMS <= 20000UL', source)
        self.assertIn('timing.feederMaxRunMS % 500UL == 0', source)

    def _post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type='application/json',
        )

    def _mark_esp32_online(self):
        response = self.client.get('/api/esp32/command/', {
            'boot_id': views.APP_STATE.get('esp32_boot_id') or 'boot-test',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': views.APP_STATE.get('esp32_last_feed_command_id', 0),
        })
        self.assertEqual(response.status_code, 200)

    def _prepare_auto_run(self, *, camera_ready=False):
        profile = {
            **views.CAPTURE_TIMING_RECOMMENDED,
            'feeder_calibrated': False,
        }
        configured = self._post_json('/api/capture_timing/', profile)
        self.assertEqual(configured.status_code, 200)
        revision = configured.json()['capture_timing_revision']
        response = self.client.get('/api/esp32/command/', {
            'boot_id': 'boot-auto',
            'capability': 'feeder_v1',
            'sorter_capability': 'gate3_sorter_v1',
            'feeder_state': 'idle',
            'feeder_sensor_state': 'clear',
            'last_feed_command_id': '0',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._report('timing_config_applied', timing_revision=revision).status_code,
            200,
        )
        feeder_test = self._post_json('/api/feeder/test/')
        self.assertEqual(feeder_test.status_code, 200)
        feed_command = feeder_test.json()['motor_command']
        completed = self._report(
            'feed_cycle_completed',
            command_id=feed_command['command_id'],
            feeder_elapsed_ms=84,
            feeder_max_run_ms=feed_command['feeder_max_run_ms'],
            feeder_stop_reason='hcsr04',
        )
        self.assertEqual(completed.status_code, 200)
        confirmed = self._post_json('/api/feeder/calibration/confirm/', {
            'timing_revision': revision,
        })
        self.assertEqual(confirmed.status_code, 200)
        if camera_ready:
            self.client.get('/api/camera/state/', {'camera_ready': '1'})

    def _esp32_command(self):
        response = self.client.get('/api/esp32/command/', self._esp32_poll_params())
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _esp32_command_text(self):
        params = {**self._esp32_poll_params(), 'format': 'text'}
        response = self.client.get('/api/esp32/command/', params)
        self.assertEqual(response.status_code, 200)
        lines = response.content.decode('utf-8').splitlines()
        return dict(line.split('=', 1) for line in lines if '=' in line)

    def _esp32_poll_params(self):
        if not views.APP_STATE.get('esp32_boot_id'):
            return {}
        return {
            'boot_id': views.APP_STATE['esp32_boot_id'],
            'capability': 'feeder_v1' if views.APP_STATE['esp32_feeder_capable'] else '',
            'sorter_capability': (
                'gate3_sorter_v1' if views.APP_STATE['esp32_sorter_capable'] else ''
            ),
            'feeder_state': (
                'idle'
                if views.APP_STATE.get('active_fruit_id')
                else views.APP_STATE.get('esp32_feeder_state') or ''
            ),
            'feeder_sensor_state': views.APP_STATE.get('esp32_feeder_sensor_state') or '',
            'last_feed_command_id': views.APP_STATE.get('esp32_last_feed_command_id', 0),
        }

    def _report(self, event, station_index=None, command_id=None, **extra):
        payload = {'event': event}
        if station_index is not None:
            payload['station_index'] = station_index
        if command_id is not None:
            payload['command_id'] = command_id
        payload.update(extra)
        return self.client.post('/api/esp32/report/', payload)

    def _report_feed_success(self, command):
        return self._report(
            'feed_cycle_completed',
            command_id=command['command_id'],
            feeder_elapsed_ms=84,
            feeder_max_run_ms=command['feeder_max_run_ms'],
            feeder_stop_reason='hcsr04',
        )

    def _report_production_feed_failure(self, event, *, stop_reason):
        self._prepare_auto_run(camera_ready=True)
        started = self._post_json('/api/auto_run/', {'enabled': True}).json()
        command = started['motor_command']
        response = self._report(
            event,
            command_id=command['command_id'],
            feeder_elapsed_ms=0,
            feeder_max_run_ms=command['feeder_max_run_ms'],
            feeder_stop_reason=stop_reason,
        )
        self.assertEqual(response.status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(state['operator_alert']['reason'], event)
        self.assertEqual(state['operator_alert']['feeder_stop_reason'], stop_reason)
        return state

    def _fake_image(self, station_index):
        return SimpleUploadedFile(
            f'source_{station_index:02d}.jpg',
            f'image-{station_index}'.encode('utf-8'),
            content_type='image/jpeg',
        )

    def _upload_station(self, fruit_id, capture_token, station_index):
        return self.client.post('/api/upload_images/', {
            'fruit_id': fruit_id,
            'capture_token': str(capture_token),
            'station_index': str(station_index),
            'capture_meta': json.dumps({
                'station_index': station_index,
            }),
            'image': self._fake_image(station_index),
        })

    def _start_capture_and_ready_station_1(self):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()
        response = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _complete_auto_session(self, expected_fruit_id):
        self._mark_esp32_online()
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fruit_id'], expected_fruit_id)

        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')
        self.assertEqual(command['station_index'], 1)
        station_1 = self._report('station_1_ready', station_index=1, command_id=command['command_id']).json()
        self.assertEqual(self._upload_station(expected_fruit_id, station_1['capture_token'], 1).status_code, 200)

        command = self._esp32_command()
        station_2 = self._report('station_2_ready', station_index=2, command_id=command['command_id']).json()
        self.assertEqual(self._upload_station(expected_fruit_id, station_2['capture_token'], 2).status_code, 200)

        command = self._esp32_command()
        station_3 = self._report('station_3_ready', station_index=3, command_id=command['command_id']).json()
        response = self._upload_station(expected_fruit_id, station_3['capture_token'], 3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'uploaded')

    def _complete_sorter(self, event='classification_sorter_completed', message=None):
        command = self._esp32_command()
        self.assertEqual(command['command'], 'classify_fruit')
        response = self._report(
            event,
            command_id=command['command_id'],
            classification_code=command['classification_code'],
            message=message or event,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()
