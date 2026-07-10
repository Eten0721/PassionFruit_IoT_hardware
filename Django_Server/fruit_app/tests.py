import csv
import json
import shutil
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from . import dataset_store, views


class DataCollectionFlowTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self.temp_dir.name)
        self.capture_timing_path = self.dataset_root / 'runtime_config' / 'capture_timing.json'
        self.settings_override = override_settings(
            DATASET_ROOT=self.dataset_root,
            CAPTURE_TIMING_CONFIG_PATH=self.capture_timing_path,
        )
        self.settings_override.enable()
        views.reset_runtime_state_for_tests()
        views._ensure_dataset_structure()
        self.client = Client()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()
        views.reset_runtime_state_for_tests()

    def test_manual_three_station_flow_uploads_and_classifies_dataset(self):
        response = self._post_json('/api/set_counter/', {'start_id': 120})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_120')

        self._mark_esp32_online()
        response = self._post_json('/api/manual_capture/')
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
        self.assertEqual(command['fruit_settle_ms'], 200)

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

        command = self._esp32_command()
        self.assertEqual(command['station_index'], 3)
        response = self._report('capture_sequence_finished', command_id=command['command_id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'uploaded')

        response = self._post_json('/api/classify/', {
            'label': '上中等',
            'note': '表皮完整',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_120').exists())
        self.assertTrue((self.dataset_root / '上中等' / 'fruit_120' / 'img_03.jpg').exists())
        self.assertEqual(response.json()['next_fruit_id'], 'fruit_121')

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.DictReader(csv_file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fruit_id'], 'fruit_120')
        self.assertEqual(rows[0]['label'], '上中等')
        self.assertEqual(rows[0]['path'], '上中等/fruit_120')
        self.assertEqual(rows[0]['capture_count'], '3')
        self.assertEqual(rows[0]['station_01_ok'], 'true')
        self.assertEqual(rows[0]['station_02_ok'], 'true')
        self.assertEqual(rows[0]['station_03_ok'], 'true')
        self.assertEqual(rows[0]['note'], '表皮完整')

        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 121)

    def test_manual_capture_requires_recent_esp32_poll(self):
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload['reason'], 'esp32_offline')
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
        self.assertEqual(command['fruit_settle_ms'], 200)

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
        self.assertEqual(command['fruit_settle_ms'], '200')

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

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')

    def test_auto_trigger_is_enabled_after_discard(self):
        response = self._report('hcsr04_trigger')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '0')

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)

        command = self._esp32_command_text()
        self.assertEqual(command['auto_trigger_enabled'], '1')

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
        response = self._post_json('/api/manual_capture/')
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
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()

        jump_response = self._report('station_2_ready', station_index=2, command_id=command['command_id'])
        self.assertEqual(jump_response.status_code, 409)
        self.assertEqual(jump_response.json()['reason'], 'unexpected_station_ready')

    def test_station_ready_requires_matching_command_id(self):
        self._mark_esp32_online()
        response = self._post_json('/api/manual_capture/')
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
        station_payload = self._start_manual_and_ready_station_1()
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
        station_payload = self._start_manual_and_ready_station_1()
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
        station_payload = self._start_manual_and_ready_station_1()
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
        response = self._post_json('/api/manual_capture/')
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
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_005').exists())

        response = self._post_json('/api/discard/')
        self.assertEqual(response.status_code, 200)
        discard_payload = response.json()
        self.assertEqual(discard_payload['discarded_fruit_id'], 'fruit_005')
        self.assertEqual(discard_payload['status'], 'idle')
        self.assertIsNone(discard_payload['active_fruit_id'])
        self.assertEqual(discard_payload['capture_token'], 0)
        self.assertTrue(discard_payload['can_manual_capture'])
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
        self.assertTrue(refreshed['can_manual_capture'])

    def test_discard_quarantines_busy_temp_folder_and_reenables_auto_trigger(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        self._mark_esp32_online()
        self._post_json('/api/manual_capture/')
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
        self.assertTrue(payload['can_manual_capture'])
        self.assertFalse(fruit_dir.exists())
        self.assertTrue((self.dataset_root / '_delete_pending').exists())
        self.assertTrue((self.dataset_root / 'discard_state.json').exists())
        self.assertEqual(self._esp32_command()['auto_trigger_enabled'], 1)

    def test_discard_defers_locked_temp_folder_and_skips_its_fruit_id(self):
        self._post_json('/api/set_counter/', {'start_id': 5})
        self._mark_esp32_online()
        self._post_json('/api/manual_capture/')
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
        self.assertTrue(payload['can_manual_capture'])
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
        self.assertTrue(response.json()['can_manual_capture'])
        self.assertEqual(views._read_discard_state()['pending_paths'], [])

    def test_capture_timing_defaults_persist_and_are_acknowledged_by_esp32(self):
        initial = self.client.get('/api/state/').json()
        self.assertEqual(initial['capture_timing'], {
            'first_station_settle_ms': 200,
            'servo_settle_ms': 200,
            'fruit_settle_ms': 200,
            'final_gate_return_delay_ms': 200,
        })
        self.assertEqual(initial['capture_timing_revision'], 1)

        configured = self._post_json('/api/capture_timing/', {
            'first_station_settle_ms': 350,
            'servo_settle_ms': 400,
            'fruit_settle_ms': 450,
            'final_gate_return_delay_ms': 300,
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

        command = self._esp32_command_text()
        self.assertEqual(command['timing_revision'], '2')
        self.assertEqual(command['first_station_settle_ms'], '350')
        self.assertEqual(command['servo_settle_ms'], '400')
        self.assertEqual(command['fruit_settle_ms'], '450')
        self.assertEqual(command['final_gate_return_delay_ms'], '300')

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
        self.assertEqual(migrated, legacy_payload)
        self.assertEqual(views.APP_STATE['capture_timing_revision'], 10)

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

    def test_reset_dataset_clears_all_data_and_counter(self):
        response = self._post_json('/api/set_counter/', {'start_id': 7})
        self.assertEqual(response.status_code, 200)
        self._complete_auto_session('fruit_007')
        response = self._post_json('/api/classify/', {'label': '加工', 'note': 'reset test'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.dataset_root / '加工' / 'fruit_007').exists())

        response = self._post_json('/api/reset_dataset/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['reset_done'])
        self.assertEqual(payload['next_fruit_id'], 'fruit_001')
        self.assertTrue(payload['can_manual_capture'])
        self.assertFalse((self.dataset_root / '加工' / 'fruit_007').exists())

        with (self.dataset_root / 'metadata.csv').open('r', encoding='utf-8-sig', newline='') as csv_file:
            rows = list(csv.reader(csv_file))
        self.assertEqual(rows, [views.METADATA_FIELDNAMES])
        with (self.dataset_root / 'counter.json').open('r', encoding='utf-8') as counter_file:
            self.assertEqual(json.load(counter_file)['next_id'], 1)

    def test_metadata_header_is_upgraded_from_old_schema(self):
        metadata_path = self.dataset_root / 'metadata.csv'
        metadata_path.write_text(
            'fruit_id,label,capture_time,path,note\nfruit_001,上中等,2026-07-06 10:00:00,上中等/fruit_001,old note\n',
            encoding='utf-8-sig',
        )

        dataset_store.RUNTIME_CACHE.reset(self.dataset_root)
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
        self.assertContains(dashboard_response, '三站照片預覽')
        self.assertEqual(dashboard_response['Cache-Control'], 'no-store, max-age=0')

        camera_response = self.client.get('/camera/')
        self.assertEqual(camera_response.status_code, 200)
        self.assertContains(camera_response, '開啟相機')
        self.assertContains(camera_response, '安全來源')
        self.assertContains(camera_response, '/static/fruit_app/js/camera.js')
        self.assertEqual(camera_response['Cache-Control'], 'no-store, max-age=0')

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
        fruit_id = 'fruit_001'
        fruit_dir = self.dataset_root / 'temp' / fruit_id
        fruit_dir.mkdir(parents=True)
        for filename in views.IMAGE_FILENAMES:
            (fruit_dir / filename).write_bytes(b'jpeg')
        with views.STATE_LOCK:
            views.APP_STATE['active_fruit_id'] = fruit_id
            views.APP_STATE['status'] = 'uploaded'
            views.APP_STATE['capture_time'] = views._now_string()

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
                data=json.dumps({'label': '上中等'}),
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

    def _post_json(self, url, payload=None):
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type='application/json',
        )

    def _mark_esp32_online(self):
        response = self.client.get('/api/esp32/command/')
        self.assertEqual(response.status_code, 200)

    def _esp32_command(self):
        response = self.client.get('/api/esp32/command/')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _esp32_command_text(self):
        response = self.client.get('/api/esp32/command/?format=text')
        self.assertEqual(response.status_code, 200)
        lines = response.content.decode('utf-8').splitlines()
        return dict(line.split('=', 1) for line in lines if '=' in line)

    def _report(self, event, station_index=None, command_id=None, **extra):
        payload = {'event': event}
        if station_index is not None:
            payload['station_index'] = station_index
        if command_id is not None:
            payload['command_id'] = command_id
        payload.update(extra)
        return self.client.post('/api/esp32/report/', payload)

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
                'timestamps_ms': [10 * station_index],
                'intervals_ms': [],
            }),
            'image': self._fake_image(station_index),
        })

    def _start_manual_and_ready_station_1(self):
        self._mark_esp32_online()
        response = self._post_json('/api/manual_capture/')
        self.assertEqual(response.status_code, 200)
        command = self._esp32_command()
        response = self._report('station_1_ready', station_index=1, command_id=command['command_id'])
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _complete_auto_session(self, expected_fruit_id):
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
        self.assertEqual(self._upload_station(expected_fruit_id, station_3['capture_token'], 3).status_code, 200)

        command = self._esp32_command()
        response = self._report('capture_sequence_finished', command_id=command['command_id'])
        self.assertEqual(response.status_code, 200)
