"""No unexpected error or hung job may leave Django refusing every dataset action."""

import json
from unittest import mock

from django.test import SimpleTestCase

from . import dataset_store, local_network, views
from . import tests as existing
from . import test_network_safety as safety
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

    def _write_marker(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'command_id': 1, 'fruit_id': 'fruit_001', 'label': '上等',
            'classification_code': views.CLASSIFICATION_CODES['上等'], 'phase': 'pending',
        }), encoding='utf-8')

    def test_restart_with_sorter_or_gate3_recovery_still_allows_network_selection(self):
        for marker in (views._sorter_recovery_path(), views._gate3_recovery_path()):
            with self.subTest(marker=marker.name):
                self._write_marker(marker)
                local_network.NETWORK.clear_selection()
                views.reset_runtime_state()  # Django restart: selection gone, markers restored.
                views._ensure_dataset_structure()
                self.assertEqual(views.APP_STATE['status'], 'error')

                state = self.client.get('/api/state/').json()
                self.assertTrue(state['network']['selection_allowed'])
                response = self._post_json('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(marker.exists())
                self.assertEqual(views.APP_STATE['status'], 'error')
                marker.unlink()

    def test_unselected_network_can_be_selected_with_leftover_temp_fruit(self):
        self._uploaded_fruit(status='incomplete')
        local_network.NETWORK.clear_selection()
        response = self._post_json('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(views.APP_STATE['active_fruit_id'], 'fruit_001')

    def test_runtime_reset_clears_sorter_and_gate3_recovery(self):
        markers = (views.NETWORK_RECOVERY.path(), views._sorter_recovery_path(), views._gate3_recovery_path())
        for marker in markers:
            self._write_marker(marker)
        views.reset_runtime_state()
        views._ensure_dataset_structure()
        self.assertTrue(views.APP_STATE['sorter_recovery_required'])

        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        # The unreadable network marker restored generation 1; the reset keeps it, lock-free.
        self.assertEqual(json.loads(markers[0].read_text(encoding='utf-8')),
                         {'phase': 'clear', 'generation': 1, 'receipt': None, 'auto_trigger_paused': False})
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertEqual([marker.exists() for marker in markers[1:]], [False, False])
        self.assertFalse(views.APP_STATE['sorter_recovery_required'])
        self.assertIsNone(views.APP_STATE['auto_run_recovery_reason'])
        self.assertEqual(views.APP_STATE['status'], 'idle')
        response = self._post_json('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'})
        self.assertEqual(response.status_code, 200)


class DeviceDeadlockTests(SimpleTestCase):
    """Waits between Django and the ESP32 that neither side could end alone."""
    setUp = safety.NetworkSafetyTests.setUp
    tearDown = safety.NetworkSafetyTests.tearDown
    post = safety.NetworkSafetyTests.post
    select = safety.NetworkSafetyTests.select
    device = safety.NetworkSafetyTests.device
    prepare = safety.NetworkSafetyTests.prepare
    lock = safety.NetworkSafetyTests.lock

    def _marker(self):
        return json.loads(views.NETWORK_RECOVERY.path().read_text(encoding='utf-8'))

    def _recover(self):
        """Run the whole homing handshake and return the final ACK."""
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).status_code, 200)
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'],
                          network_interlocked='1')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        ack['event'] = 'network_recovery_committed'
        return ack

    def test_runtime_reset_keeps_generation_so_unlocked_board_reconnects(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertTrue(self.post('/api/esp32/report/', self._recover()).json()['recovery_committed'])
        generation = views.APP_STATE['recovery_generation']
        self.assertGreaterEqual(generation, 1)

        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        self.assertEqual(views.APP_STATE['recovery_generation'], generation)
        self.assertEqual(self._marker(), {'phase': 'clear', 'generation': generation, 'receipt': None,
                                          'auto_trigger_paused': False})
        self.assertEqual(self.select().status_code, 200)
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=generation))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['control_allowed'])

    def test_held_boot_then_runtime_reset_leaves_both_sides_unlocked(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertIsNotNone(views.APP_STATE['network_recovery'])
        generation = views.APP_STATE['recovery_generation']

        # BOOT held 10 s clears the board's NVS lock and keeps its generation.
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=generation))
        self.assertFalse(response.json()['control_allowed'])
        self.assertEqual(response.json()['recovery_generation'], generation)  # Same value: the board stays unlocked.

        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        self.assertEqual(self.select().status_code, 200)
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=generation))
        self.assertTrue(response.json()['control_allowed'])
        self.assertIsNone(views.APP_STATE.get('network_recovery'))

    def test_runtime_reset_without_any_recovery_leaves_no_marker(self):
        self.prepare()
        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        self.assertFalse(views.NETWORK_RECOVERY.path().exists())
        self.assertEqual(views.APP_STATE.get('recovery_generation', 0), 0)

    def test_unlocked_board_with_newer_generation_is_adopted_not_refused(self):
        self.prepare()  # Lost marker, new computer or fresh install: Django starts at 0.
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=5))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recovery_generation'], 5)
        self.assertTrue(response.json()['control_allowed'])
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertEqual(self._marker()['generation'], 5)
        self.assertEqual(self._marker()['phase'], 'clear')
        views.reset_runtime_state()  # Django restart keeps the adopted generation.
        views._ensure_dataset_structure()
        self.assertEqual(views.APP_STATE['recovery_generation'], 5)

    def test_newer_generation_from_another_network_identity_is_still_refused(self):
        self.prepare()
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=5, network_revision=99))
        self.assertEqual(response.json()['reason'], 'network_identity_mismatch')
        self.assertEqual(views.APP_STATE.get('recovery_generation', 0), 0)

    def test_generation_is_not_adopted_when_it_cannot_be_saved(self):
        self.prepare()
        with mock.patch.object(views, '_write_recovery_payload', side_effect=OSError('disk')):
            response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=5))
        self.assertEqual(response.json()['reason'], 'network_identity_mismatch')
        self.assertEqual(views.APP_STATE.get('recovery_generation', 0), 0)

    def test_locked_django_answers_above_unlocked_board_so_it_can_home(self):
        self.prepare()
        self.lock()
        response = self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=5))
        self.assertEqual(response.json()['recovery_generation'], 6)  # Higher, so the board locks itself.
        self.assertFalse(response.json()['control_allowed'])
        self.assertEqual(views.APP_STATE['network_recovery']['generation'], 6)
        self.assertEqual(views.APP_STATE['network_recovery']['phase'], 'locked')
        self.assertEqual(self._marker()['generation'], 6)
        self.client.get('/api/esp32/heartbeat/', self.device(recovery_generation=6, network_interlocked='1'))
        self.assertTrue(self.post('/api/esp32/report/', self._recover()).json()['recovery_committed'])
        self.assertIsNone(views.APP_STATE['network_recovery'])

    def test_resent_final_ack_gets_the_same_answer_and_does_not_relock(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        ack = self._recover()
        first = self.post('/api/esp32/report/', ack).json()
        self.assertTrue(first['recovery_committed'])
        self.assertTrue(first['control_allowed'])
        generation = views.APP_STATE['recovery_generation']

        resent = self.post('/api/esp32/report/', ack).json()  # The first answer was lost on the way.
        self.assertTrue(resent['recovery_committed'])
        self.assertTrue(resent['control_allowed'])
        self.assertEqual(resent['control_lease_ms'], 6000)
        self.assertFalse(views.APP_STATE['esp32_interlocked'])
        poll = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertTrue(poll['control_allowed'])
        self.assertIsNone(views.APP_STATE['network_recovery'])
        self.assertEqual(views.APP_STATE['recovery_generation'], generation)

    def test_board_still_interlocked_after_recovery_relocks_as_before(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        ack = self._recover()
        self.post('/api/esp32/report/', ack)
        self.post('/api/esp32/report/', ack)
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertTrue(views.APP_STATE['network_recovery']['motor_locked'])

    def test_absent_board_lets_cleaned_up_round_leave_for_photo_only(self):
        self.prepare()
        self.lock()
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 60  # The board never returns.
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).json()['reason'],
                         'esp32_offline')
        options = {'work_mode': 'collection', 'hardware_mode': 'photo_only'}

        fruit_dir = self.dataset_root / 'temp/fruit_001'
        fruit_dir.mkdir(parents=True)
        (fruit_dir / 'img_01.jpg').write_bytes(b'partial photo fixture')
        views.APP_STATE['active_fruit_id'] = 'fruit_001'
        self.assertEqual(self.post('/api/collection_options/', options).status_code, 409)
        token = self.client.get('/api/state/').json()['capture_token']
        self.assertEqual(self.post('/api/discard/', {'fruit_id': 'fruit_001', 'capture_token': token}).status_code, 200)

        switched = self.post('/api/collection_options/', options)
        self.assertEqual(switched.status_code, 200, switched.content)
        state = switched.json()
        self.assertEqual(state['hardware_mode'], 'photo_only')
        self.assertEqual(state['status'], 'error')  # The data lock still waits for its confirmation.
        self.assertTrue(state['network_recovery']['data_locked'])
        self.assertTrue(state['network_recovery']['motor_locked'])
        self.assertEqual(self.post('/api/photo_capture/').json()['reason'], 'network_recovery_confirmation_required')

        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(self.post('/api/photo_capture/', {'recovery_confirmed': True}).status_code, 200)
        recovery = views.APP_STATE['network_recovery']
        self.assertFalse(recovery['data_locked'])
        self.assertTrue(recovery['motor_locked'])  # Hardware mode still requires the homing handshake.
        self.assertEqual(views.APP_STATE['hardware_mode'], 'photo_only')

    def test_reachable_board_keeps_data_lock_blocking_photo_only(self):
        self.prepare()
        self.lock()
        self.client.get('/api/esp32/heartbeat/', self.device(
            recovery_generation=views.APP_STATE['recovery_generation'], network_interlocked='1'))
        response = self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'photo_only'})
        self.assertEqual(response.status_code, 409)

    def test_unanswered_board_never_blocks_choosing_another_network(self):
        self.prepare()
        views.APP_STATE.update(status='error', sorter_recovery_required=True)
        blocked = self.client.get('/api/state/').json()['network']
        self.assertEqual(blocked['selection_disabled_reason'], 'sorter_recovery_required')  # Board is answering.

        views.APP_STATE['last_esp32_poll_monotonic'] = None  # Wrong interface: the board was never seen.
        state = self.client.get('/api/state/').json()
        self.assertTrue(state['network']['selection_allowed'])
        self.assertEqual(self.select().status_code, 200)
        self.assertTrue(views.APP_STATE['sorter_recovery_required'])  # Selecting clears nothing.


class MotorErrorWithoutRoundTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown
    _post_json = existing.DataCollectionFlowTests._post_json
    _report = existing.DataCollectionFlowTests._report
    _prepare_auto_run = existing.DataCollectionFlowTests._prepare_auto_run
    _start_capture_and_ready_station_1 = existing.DataCollectionFlowTests._start_capture_and_ready_station_1
    _mark_esp32_online = existing.DataCollectionFlowTests._mark_esp32_online
    _esp32_command = existing.DataCollectionFlowTests._esp32_command
    _esp32_poll_params = existing.DataCollectionFlowTests._esp32_poll_params

    def test_rejected_feed_stops_auto_run_and_next_start_recovers(self):
        self._prepare_auto_run(camera_ready=True)
        command = self._post_json('/api/auto_run/', {'enabled': True}).json()['motor_command']
        self.assertEqual(command['command'], 'feed_one')
        # The board was still inside a round Django had already dropped.
        self.assertEqual(self._report('motor_error', command_id=command['command_id'],
                                      message='feed_not_safe').status_code, 200)

        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertIsNone(state['active_fruit_id'])
        self.assertFalse(state['auto_run_enabled'])
        self.assertFalse(state['auto_run_finishing'])
        self.assertEqual(state['auto_run_recovery_reason'], 'motor_error')
        self.assertIsNone(state['auto_run_start_disabled_reason'])
        self.assertIn('feed_not_safe', state['message'])

        restarted = self._post_json('/api/auto_run/', {'enabled': True})
        self.assertEqual(restarted.status_code, 200, restarted.content)
        state = restarted.json()
        self.assertTrue(state['auto_run_enabled'])
        self.assertIsNone(state['auto_run_recovery_reason'])
        self.assertEqual(state['motor_command']['command'], 'feed_one')
        self.assertGreater(state['motor_command']['command_id'], command['command_id'])

    def test_pause_after_rejected_feed_does_not_wait_for_a_round_that_never_ends(self):
        self._prepare_auto_run(camera_ready=True)
        command = self._post_json('/api/auto_run/', {'enabled': True}).json()['motor_command']
        self._report('motor_error', command_id=command['command_id'], message='feed_not_safe')
        self.assertEqual(self._post_json('/api/auto_run/', {'enabled': False}).status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['auto_run_finishing'])
        self.assertEqual(state['options_disabled_reason'], 'motor_error')
        self.assertEqual(self._post_json('/api/auto_run/', {'enabled': True}).status_code, 200)

    def _round_active(self):
        response = self.client.get('/api/esp32/command/', {**self._esp32_poll_params(), 'format': 'text'})
        fields = dict(line.split('=', 1) for line in response.content.decode('utf-8').splitlines() if '=' in line)
        self.assertEqual(self._esp32_command().get('round_active'), int(fields['round_active']))
        return fields['round_active']

    def test_board_is_told_when_its_round_no_longer_exists(self):
        self._mark_esp32_online()
        self.assertEqual(self._round_active(), '0')
        self._start_capture_and_ready_station_1()
        self.assertEqual(self._round_active(), '1')  # Waiting for the photo, then release_gate.
        discarded = self._post_json('/api/discard/', {
            'fruit_id': views.APP_STATE['active_fruit_id'], 'capture_token': views.APP_STATE['capture_token']})
        self.assertEqual(discarded.status_code, 200)
        self.assertEqual(views.APP_STATE['status'], 'idle')
        self.assertEqual(self._round_active(), '0')  # The board closes its gates and returns to idle.

    def test_unresolved_states_keep_the_board_waiting(self):
        self._mark_esp32_online()
        for field, value in (('motor_command', {'command': 'classify_fruit', 'command_id': 9, 'fruit_id': 'fruit_001'}),
                             ('dataset_operation', {'kind': 'classify'}), ('sorter_status', 'pending'),
                             ('sorter_recovery_required', True),
                             ('auto_run_recovery_reason', 'gate3_manual_removal_required'),
                             ('status', 'error'), ('status', 'uploaded'), ('status', 'classified')):
            with self.subTest(field=field, value=value):
                previous = views.APP_STATE.get(field)
                views.APP_STATE[field] = value
                self.assertEqual(self._esp32_command()['round_active'], 1)
                views.APP_STATE[field] = previous
        self.assertEqual(self._esp32_command()['round_active'], 0)

    def test_motor_error_during_a_round_still_leaves_the_round_to_discard(self):
        self._start_capture_and_ready_station_1()
        fruit_id = views.APP_STATE['active_fruit_id']
        self.assertEqual(self._report('motor_error', message='release_gate_not_expected').status_code, 200)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'error')
        self.assertEqual(state['active_fruit_id'], fruit_id)
        self.assertIsNone(state['auto_run_recovery_reason'])
        self.assertTrue(state['can_discard'])
