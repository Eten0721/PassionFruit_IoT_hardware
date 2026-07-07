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

        command = self._esp32_command()
        self.assertEqual(command['command'], 'start_sequence')
        self.assertEqual(command['station_index'], 1)
        self.assertEqual(command['fruit_id'], 'fruit_042')

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
        self.assertEqual(second_payload['reason'], 'active_fruit_exists')
        self.assertFalse((self.dataset_root / 'temp' / 'fruit_002').exists())
        second_command = self._esp32_command()
        self.assertEqual(second_command['command_id'], first_command['command_id'])
        self.assertEqual(second_command['fruit_id'], 'fruit_001')

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
        self.assertIsNone(discard_payload['active_fruit_id'])
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
        self.assertContains(camera_response, 'isSecureContext')
        self.assertEqual(camera_response['Cache-Control'], 'no-store, max-age=0')

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

    def _report(self, event, station_index=None, command_id=None):
        payload = {'event': event}
        if station_index is not None:
            payload['station_index'] = station_index
        if command_id is not None:
            payload['command_id'] = command_id
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
