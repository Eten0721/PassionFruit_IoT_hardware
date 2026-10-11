"""Loss, cleanup and recovery contracts exercised through the HTTP endpoints."""
import json
from unittest import mock

from django.test import SimpleTestCase
from . import views, local_network
from . import test_network as onboarding


class NetworkSafetyTests(SimpleTestCase):
    setUp = onboarding.NetworkOnboardingTests.setUp
    tearDown = onboarding.NetworkOnboardingTests.tearDown
    post = onboarding.NetworkOnboardingTests.post
    select = onboarding.NetworkOnboardingTests.select
    _fake_image = onboarding.NetworkOnboardingTests._fake_image
    _upload_station = onboarding.NetworkOnboardingTests._upload_station

    def device(self, **fields):
        network = self.network.snapshot()
        return {'boot_id': 'board-a', 'network_capability': 'local_network_v1',
                'server_run_id': network['server_run_id'], 'network_revision': network['network_revision'],
                'recovery_generation': views.APP_STATE.get('recovery_generation', 0),
                'network_interlocked': '0', 'capability': 'feeder_v1',
                'sorter_capability': 'gate3_sorter_v1', 'feeder_state': 'idle',
                'feeder_sensor_state': 'clear', 'setup_allowed': '1', 'motion': 'idle', **fields}

    def prepare(self, *, photo_only=False):
        self.assertEqual(self.select().status_code, 200)
        if photo_only:
            self.assertEqual(self.post('/api/collection_options/', {
                'work_mode': 'collection', 'hardware_mode': 'photo_only',
            }).status_code, 200)
        else:
            self.assertEqual(self.client.get('/api/esp32/command/', self.device()).status_code, 200)
        self.client.get('/api/camera/state/?camera_ready=1')

    def lock(self):
        views.APP_STATE['auto_run_enabled'] = True
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
        return self.client.get('/api/state/').json()

    def test_heartbeat_never_claims_command_or_queues_feed(self):
        self.prepare()
        views.APP_STATE.update(auto_run_enabled=True, auto_feed_pending=True,
                               motor_command={'command': 'feed_one', 'command_id': 88})
        response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertEqual(response['command'], 'none')
        self.assertTrue(response['control_allowed'])
        self.assertEqual(response['control_lease_ms'], 6000)
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], 88)

    def test_stale_camera_blocks_both_hardware_trigger_routes(self):
        self.prepare()
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 5.5
        for route, event in (('/api/esp32_trigger/', None), ('/api/esp32/report/', 'hcsr04_trigger')):
            response = self.post(route, self.device(**({'event': event} if event else {})))
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()['reason'], 'camera_not_ready')
            self.assertIsNone(views.APP_STATE.get('motor_command'))
            self.assertIsNone(views.APP_STATE.get('active_fruit_id'))

    def test_first_reconnect_checks_old_heartbeat_before_refresh(self):
        self.prepare()
        views.APP_STATE.update(auto_run_enabled=True, pending_capture=True, capture_token=123)
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
        response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertFalse(response['control_allowed'])
        self.assertFalse(views.APP_STATE['pending_capture'])
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertNotEqual(views.APP_STATE['capture_token'], 123)
        self.assertTrue(views._network_recovery_path().exists())

    def test_camera_stale_blocks_start_before_confirmed_loss(self):
        self.prepare(photo_only=True)
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 5.5
        self.assertEqual(self.post('/api/photo_capture/').json()['reason'], 'camera_not_ready')
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.client.get('/api/camera/state/?camera_ready=1')
        capture = self.post('/api/photo_capture/').json()
        self._upload_station('fruit_001', capture['capture_token'], 1)
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 10
        # A readiness request must detect the preceding outage first.
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(views.APP_STATE['network_recovery']['reason'], 'camera_connection_lost')
        self.assertTrue((self.dataset_root / 'temp/fruit_001/img_01.jpg').exists())

    def test_photo_only_cleanup_confirmation_starts_new_round_without_motor(self):
        self.prepare(photo_only=True)
        first = self.post('/api/photo_capture/').json()
        self._upload_station('fruit_001', first['capture_token'], 1)
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 10
        state = self.client.get('/api/state/').json()
        token = state['capture_token']
        self.assertEqual(self.post('/api/recapture/').status_code, 409)
        self.assertEqual(self.post('/api/classify/', {'label': '上等'}).status_code, 409)
        self.assertEqual(self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'hardware'}).status_code, 409)
        self.assertEqual(self.post('/api/discard/', {'fruit_id': 'fruit_001', 'capture_token': token}).status_code, 200)
        self.client.get('/api/camera/state/?camera_ready=1')
        self.assertEqual(self.post('/api/photo_capture/').json()['reason'], 'network_recovery_confirmation_required')
        second = self.post('/api/photo_capture/', {'recovery_confirmed': True}).json()
        self.assertTrue(second['pending_capture'])
        self.assertNotEqual(second['capture_token'], token)
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_hardware_recovery_requires_two_commits_and_duplicates_do_not_repeat_home(self):
        self.prepare()
        self.lock()
        self.assertEqual(self.client.get('/api/esp32/command/', self.device()).json()['command'], 'none')
        response = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
        self.assertEqual(response.status_code, 200, response.content)
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')
        self.assertFalse(command['control_allowed'])
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1')
        response = self.post('/api/esp32/report/', ack).json()
        self.assertTrue(response['recovery_commit'])
        self.assertFalse(response['control_allowed'])
        self.assertIsNotNone(views.APP_STATE['network_recovery'])
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        ack.update(event='network_recovery_committed')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
        self.assertIsNone(views.APP_STATE['network_recovery'])
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['duplicate'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        generation = views.APP_STATE['recovery_generation']
        views.reset_runtime_state_for_tests()
        self.client.get('/api/state/')
        self.assertEqual(views.APP_STATE['recovery_generation'], generation)
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['duplicate'])
        self.assertIsNone(views.APP_STATE.get('network_recovery'))

    def test_wrong_ack_and_timeout_keep_diagnostic_lock(self):
        self.prepare()
        self.lock()
        self.client.get('/api/esp32/heartbeat/', self.device())
        self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
        ack = self.device(event='network_recovery_completed', command_id=999)
        self.assertEqual(self.post('/api/esp32/report/', ack).json()['reason'], 'network_recovery_ack_mismatch')
        generation = views.APP_STATE['recovery_generation']
        views.APP_STATE['network_recovery']['deadline'] = views.time.monotonic() - 1
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['network_recovery']['reason'], 'network_recovery_ack_timeout')
        self.assertGreater(state['recovery_generation'], generation)
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_marker_write_and_clear_failures_leave_memory_locked(self):
        self.prepare()
        with mock.patch.object(views, '_write_recovery_payload', side_effect=OSError('disk full')):
            self.lock()
        self.assertTrue(views.APP_STATE['network_persist_failed'])
        self.assertIsNotNone(views.APP_STATE['network_recovery'])
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.client.get('/api/camera/state/?camera_ready=1')
        self.client.get('/api/esp32/command/', self.device(network_interlocked='1'))
        command = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).json()['motor_command']
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        ack['event'] = 'network_recovery_committed'
        with mock.patch.object(views, '_write_recovery_payload', side_effect=OSError('disk full')):
            response = self.post('/api/esp32/report/', ack)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['reason'], 'recovery_persist_failed')
        self.assertIsNotNone(views.APP_STATE['network_recovery'])
        self.assertFalse(response.json()['control_allowed'])

    def test_committed_dataset_and_counter_survive_classifier_network_abort(self):
        self.prepare()
        self.assertEqual(self.post('/api/esp32_trigger/', self.device()).status_code, 200)
        for station in (1, 2, 3):
            command = self.client.get('/api/esp32/command/', self.device()).json()
            response = self.post('/api/esp32/report/', self.device(
                event=f'station_{station}_ready', station_index=station, command_id=command['command_id']))
            self.assertEqual(response.status_code, 200, response.content)
            token = views.APP_STATE['capture_token']
            self.assertEqual(self._upload_station('fruit_001', token, station).status_code, 200)
        response = self.post('/api/classify/', {'fruit_id': 'fruit_001', 'capture_token': token, 'label': '上等'})
        self.assertEqual(response.status_code, 200, response.content)
        self.client.get('/api/esp32/command/', self.device())
        state = self.lock()
        self.assertIsNotNone(state['network_recovery'])
        self.assertTrue((self.dataset_root / '上等/fruit_001/img_03.jpg').exists())
        self.assertEqual(views._read_counter(), 2)
        self.assertIsNone(state['active_fruit_id'])

    def test_service_restart_restores_lock_and_cannot_resume_old_command(self):
        self.prepare()
        self.lock()
        generation = views.APP_STATE['recovery_generation']
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['network_recovery']['phase'], 'locked')
        self.assertEqual(state['recovery_generation'], generation)
        self.assertEqual(state['motor_command']['command'], 'none')

    def test_failed_marker_then_restart_adopts_device_lock_without_granting_motion(self):
        self.prepare()
        with mock.patch.object(views, '_write_recovery_payload', side_effect=OSError('disk full')):
            self.lock()
        generation = views.APP_STATE['recovery_generation']
        views.reset_runtime_state_for_tests()
        response = self.client.get('/api/esp32/command/', self.device(
            recovery_generation=generation, network_interlocked='1')).json()
        self.assertEqual(response['recovery_generation'], generation)
        self.assertFalse(response['control_allowed'])
        self.assertEqual(response['command'], 'none')
        self.assertEqual(views.APP_STATE['network_recovery']['phase'], 'locked')

    def test_address_change_invalidates_token_origin_and_locks_active_round(self):
        self.prepare(photo_only=True)
        first = self.post('/api/photo_capture/').json()
        self.interfaces[0] = {**self.interfaces[0], 'ipv4': '192.168.50.11'}
        self.network._checked_at = None
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['network']['camera_url'], 'https://192.168.50.11:8443/camera/')
        self.assertEqual(state['network_recovery']['reason'], 'network_changed')
        self.assertNotEqual(first['capture_token'], state['capture_token'])
        response = self.client.get('/api/camera/state/?camera_ready=1', secure=True,
                                   HTTP_HOST='192.168.50.10:8443', REMOTE_ADDR='192.168.50.20')
        self.assertEqual(response.json()['reason'], 'network_origin_mismatch')
        response = self.client.get('/camera/', secure=True,
                                   HTTP_HOST='192.168.50.11:8443', REMOTE_ADDR='192.168.50.20')
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(views.APP_STATE['network_recovery'])

    def test_interface_disappears_without_selecting_other_adapter(self):
        self.prepare()
        self.interfaces = [{'interface_id': '9', 'name': 'Ethernet', 'ipv4': '10.0.0.2', 'prefix': 24}]
        self.network._checked_at = None
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['network']['selected'])
        self.assertIsNone(state['network']['camera_url'])
        self.assertIsNone(views.APP_STATE.get('network_recovery'))

    def test_graceful_pause_keeps_current_lease_and_preview_does_not_refresh_camera(self):
        self.prepare()
        views.APP_STATE.update(auto_run_enabled=True, status='waiting_camera', active_fruit_id='fruit_001')
        (self.dataset_root / 'temp/fruit_001').mkdir()
        self.post('/api/auto_run/', {'enabled': False})
        response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertTrue(response['control_allowed'])
        last_camera = views.APP_STATE['camera_last_live_frame_monotonic']
        self.client.get('/api/webrtc/state')
        self.assertEqual(last_camera, views.APP_STATE['camera_last_live_frame_monotonic'])
