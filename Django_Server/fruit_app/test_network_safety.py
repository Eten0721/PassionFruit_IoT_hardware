"""Loss, cleanup and recovery contracts exercised through the HTTP endpoints."""
import json
from unittest import mock

from django.test import SimpleTestCase
from . import dataset_store
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

    def test_setup_requires_both_idle_device_and_idle_server(self):
        self.prepare()
        response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertTrue(response['setup_allowed'])
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup']['status'], 'allowed')
        views.APP_STATE['motor_command'] = {'command': 'feed_one', 'command_id': 88}
        response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
        self.assertFalse(response['setup_allowed'])
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['network']['setup'], {'status': 'blocked', 'reason': 'motor_command_pending'})

    def test_dashboard_setup_request_is_delivered_once_and_cleared_when_claimed(self):
        self.assertEqual(self.post('/api/network/setup_request/').json()['reason'], 'esp32_offline')
        self.prepare()
        self.assertFalse(self.client.get('/api/esp32/heartbeat/', self.device()).json()['setup_requested'])
        self.assertEqual(self.post('/api/network/setup_request/').status_code, 200)
        self.assertTrue(self.client.get('/api/esp32/command/', self.device()).json()['setup_requested'])
        response = self.client.get('/api/esp32/heartbeat/', self.device(motion='setup')).json()
        self.assertFalse(response['setup_requested'])
        self.assertTrue(response['setup_allowed'])

    def test_dashboard_setup_request_is_refused_when_not_idle_and_expires(self):
        self.prepare()
        views.APP_STATE['motor_command'] = {'command': 'feed_one', 'command_id': 88}
        self.assertEqual(self.post('/api/network/setup_request/').json()['reason'], 'motor_command_pending')
        views.APP_STATE['motor_command'] = None
        self.assertEqual(self.post('/api/network/setup_request/').status_code, 200)
        views.APP_STATE['esp32_setup_request_deadline'] = views.time.monotonic() - 1
        self.assertFalse(self.client.get('/api/esp32/heartbeat/', self.device()).json()['setup_requested'])
        self.assertEqual(self.post('/api/network/setup_request/').status_code, 200)
        self.client.get('/api/esp32/heartbeat/', self.device(setup_allowed='0', setup_block_reason='network_interlocked'))
        self.assertNotIn('esp32_setup_request_deadline', views.APP_STATE)

    def test_device_setup_block_reason_is_shown_and_validated(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(setup_allowed='0', setup_block_reason='network_interlocked'))
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup'],
                         {'status': 'blocked', 'reason': 'esp32_not_idle:network_interlocked'})
        self.client.get('/api/esp32/heartbeat/', self.device(setup_allowed='0', setup_block_reason='<script>'))
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup']['reason'], 'esp32_not_idle')

    def test_setup_ap_failure_is_shown_until_device_stops_reporting_it(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(setup_failure='setup_ap_failed'))
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup'],
                         {'status': 'blocked', 'reason': 'setup_ap_failed'})
        self.client.get('/api/esp32/heartbeat/', self.device(setup_failure='not_a_reason'))
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup']['status'], 'allowed')
        self.client.get('/api/esp32/heartbeat/', self.device(setup_failure='setup_ap_failed'))
        self.client.get('/api/esp32/heartbeat/', self.device())
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup']['status'], 'allowed')

    def test_setup_grant_reserves_idle_server_until_device_rejoins(self):
        self.prepare()
        response = self.client.get('/api/esp32/heartbeat/', self.device(motion='setup')).json()
        self.assertTrue(response['setup_allowed'])
        self.assertEqual(self.post('/api/feeder/test/').json()['reason'], 'esp32_setup_active')
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True}).json()['reason'], 'esp32_setup_active')
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.client.get('/api/esp32/heartbeat/', self.device())
        self.assertFalse(views.APP_STATE['esp32_setup_active'])

    def test_setup_reports_each_central_blocker_and_offline_unknown(self):
        self.prepare()
        for fields, reason in (
            ({'auto_run_enabled': True}, 'auto_run_active'),
            ({'gate3_waiting_fruit_id': 'fruit_001'}, 'gate3_waiting_for_classification'),
            ({'active_fruit_id': 'fruit_001'}, 'active_fruit_exists'),
            ({'pending_capture': True}, 'capture_busy'),
            ({'network_persist_failed': True}, 'network_recovery_required'),
            ({'sorter_recovery_required': True}, 'sorter_recovery_required'),
            ({'auto_run_recovery_reason': 'sorter_recovery_clear_failed'}, 'sorter_recovery_clear_failed'),
        ):
            with self.subTest(reason=reason):
                previous = {key: views.APP_STATE.get(key) for key in fields}
                views.APP_STATE.update(fields)
                response = self.client.get('/api/esp32/heartbeat/', self.device()).json()
                self.assertFalse(response['setup_allowed'])
                self.assertEqual(self.client.get('/api/network/').json()['setup']['reason'], reason)
                views.APP_STATE.update(previous)
        self.client.get('/api/esp32/heartbeat/', self.device(setup_allowed='0'))
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup']['reason'], 'esp32_not_idle')
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
        self.assertEqual(self.client.get('/api/state/').json()['network']['setup'], {'status': 'unknown', 'reason': 'esp32_offline'})

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

    def test_locked_command_poll_refreshes_capabilities_lost_by_django_restart(self):
        self.prepare()
        self.lock()
        self.assertTrue(views.APP_STATE.get('network_recovery'))
        # A restarted Django reloads the persisted lock but starts with empty device facts.
        views.APP_STATE.update(esp32_feeder_capable=False, esp32_sorter_capable=False, esp32_feeder_state=None)
        locked = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(locked['command'], 'none')
        self.assertTrue(views.APP_STATE['esp32_feeder_capable'])
        self.assertTrue(views.APP_STATE['esp32_sorter_capable'])
        self.assertEqual(views.APP_STATE['esp32_feeder_state'], 'idle')

    def test_photo_only_ignores_device_interlock_until_hardware_mode_returns(self):
        self.prepare(photo_only=True)
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertEqual(self.post('/api/notification/', {'enabled': True}).status_code, 200)
        self.assertEqual(self.post('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'hardware',
        }).status_code, 200)
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertEqual(views.APP_STATE['network_recovery']['reason'], 'esp32_network_interlocked')
        self.assertEqual(self.post('/api/notification/', {'enabled': False}).status_code, 200)

    def test_motor_recovery_confirmation_needs_esp32_without_camera_or_feeder_sensor(self):
        self.prepare()
        self.lock()
        self.client.get('/api/esp32/command/', self.device(network_interlocked='1', feeder_sensor_state='unavailable'))
        state = self.client.get('/api/state/').json()
        # Camera and HC-SR04 gate production; neither gates motor recovery.
        self.assertTrue(state['can_start_auto_run'])
        self.assertEqual(state['auto_run_disabled_reason'], 'esp32_connection_lost')
        self.assertIsNone(state['network_recovery_confirm_disabled_reason'])
        views.APP_STATE['camera_last_live_frame_monotonic'] = None
        state = self.client.get('/api/state/').json()
        self.assertTrue(state['can_start_auto_run'])
        self.assertIsNone(state['network_recovery_confirm_disabled_reason'])
        response = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['recovery_pending'])
        self.assertEqual(response.json()['network_recovery_confirm_disabled_reason'], 'network_recovery_in_progress')

    def test_motor_lock_survives_photo_only_round_switch_back_and_restart(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        locked = self.client.get('/api/state/').json()['network_recovery']
        self.assertFalse(locked['data_locked'])
        self.assertTrue(locked['motor_locked'])
        switched = self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'photo_only'})
        self.assertEqual(switched.status_code, 200, switched.content)
        # Reload must retain the selected photo-only mode and the dormant motor lock.
        views.reset_runtime_state_for_tests()
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['hardware_mode'], 'photo_only')
        self.assertEqual(state['network_recovery']['generation'], locked['generation'])
        self.client.get('/api/camera/state/?camera_ready=1')
        capture = self.post('/api/photo_capture/').json()
        self.assertTrue(capture['pending_capture'])
        for station in (1, 2, 3):
            token = views.APP_STATE['capture_token']
            response = self._upload_station('fruit_001', token, station)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(self.post('/api/discard/', {'fruit_id': 'fruit_001', 'capture_token': token}).status_code, 200)
        self.assertEqual(self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'hardware'}).status_code, 200)
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertEqual(self.post('/api/feeder/test/').json()['reason'], 'esp32_network_interlocked')
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True}).json()['reason'], 'network_recovery_confirmation_required')
        self.assertEqual(self.client.get('/api/esp32/command/', self.device()).json()['command'], 'none')
        self.assertEqual(views.APP_STATE['network_recovery']['generation'], locked['generation'])

    def test_locked_network_reselection_rejects_active_fruit(self):
        self.prepare()
        self.lock()
        fruit_dir = self.dataset_root / 'temp/fruit_001'
        fruit_dir.mkdir()
        (fruit_dir / 'img_01.jpg').write_bytes(b'partial photo fixture')
        views.APP_STATE['active_fruit_id'] = 'fruit_001'
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['network']['selection_allowed'])
        self.assertEqual(state['network']['selection_disabled_reason'], 'esp32_connection_lost')
        response = self.select()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'esp32_connection_lost')
        self.assertEqual(self.network.snapshot()['network_revision'], state['network']['network_revision'])
        self.assertEqual(views.APP_STATE['network_recovery'], state['network_recovery'])

    def test_locked_network_reselection_rejects_both_handshake_phases(self):
        self.prepare()
        self.lock()
        self.client.get('/api/esp32/command/', self.device())
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).status_code, 200)
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        for phase in ('homing', 'committing'):
            with self.subTest(phase=phase):
                if phase == 'committing':
                    response = self.post('/api/esp32/report/', self.device(
                        event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1'))
                    self.assertTrue(response.json()['recovery_commit'])
                state = self.client.get('/api/state/').json()
                self.assertEqual(state['network_recovery']['phase'], phase)
                self.assertFalse(state['network']['selection_allowed'])
                self.assertEqual(state['network']['selection_disabled_reason'], 'network_recovery_in_progress')
                response = self.select()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['reason'], 'network_recovery_in_progress')
                self.assertEqual(self.network.snapshot()['network_revision'], state['network']['network_revision'])
                self.assertEqual(views.APP_STATE['network_recovery'], state['network_recovery'])

    def test_restart_with_unselected_network_can_reselect_then_confirm_recovery(self):
        for mode in ('hardware', 'photo_only'):
            with self.subTest(mode=mode):
                self.prepare(photo_only=mode == 'photo_only')
                views.APP_STATE['auto_run_enabled'] = True
                views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 10
                recovery = self.client.get('/api/state/').json()['network_recovery']
                old_server_run_id = self.network.snapshot()['server_run_id']
                views.reset_runtime_state_for_tests()
                self.network.start_run(port=8443)
                state = self.client.get('/api/state/').json()
                self.assertFalse(state['network']['selected'])
                self.assertNotEqual(state['network']['server_run_id'], old_server_run_id)
                self.assertTrue(state['network']['selection_allowed'])
                self.assertIsNone(state['network']['selection_disabled_reason'])
                self.assertEqual(state['network_recovery_confirm_disabled_reason'], 'network_selection_required')
                self.assertEqual(self.select().status_code, 200)
                restored = self.client.get('/api/state/').json()['network_recovery']
                for key in ('data_locked', 'motor_locked', 'phase', 'generation'):
                    self.assertEqual(restored[key], recovery[key])
                if mode == 'hardware':
                    self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
                    response = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
                    self.assertEqual(response.status_code, 200, response.content)
                    command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
                    self.assertEqual(command['command'], 'recover_network')
                    ack = self.device(event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1')
                    self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
                    ack['event'] = 'network_recovery_committed'
                    self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
                else:
                    self.client.get('/api/camera/state/?camera_ready=1')
                    response = self.post('/api/photo_capture/', {'recovery_confirmed': True})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertIsNone(views.APP_STATE['motor_command'])
                self.assertIsNone(views.APP_STATE['network_recovery'])

    def test_legacy_markers_keep_lock_kinds_and_restart_requires_new_confirmation(self):
        self.prepare()
        for phase in ('locked', 'homing', 'committing'):
            for mode, reason, fruit_id, kinds in (
                ('photo_only', 'camera_connection_lost', 'fruit_001', (True, False)),
                ('photo_only', 'esp32_network_interlocked', None, (False, True)),
                ('hardware', 'esp32_network_interlocked', None, (False, True)),
                ('hardware', 'esp32_connection_lost', 'fruit_001', (True, True)),
            ):
                with self.subTest(phase=phase, mode=mode, reason=reason):
                    # Literal 1e8f06e schema: no lock-kind or device-capability fields.
                    dataset_store.write_recovery_payload(views._network_recovery_path(), {
                        'phase': phase, 'generation': 7, 'reason': reason, 'hardware_mode': mode,
                        'fruit_id': fruit_id, 'boot_id': 'board-a', 'command_id': 41,
                        'command': 'start_sequence', 'server_run_id': 'old-run', 'network_revision': 3,
                        'recovery_command_id': 42, 'recovery_boot_id': 'board-a', 'deadline': 123,
                    })
                    views.reset_runtime_state_for_tests()
                    state = self.client.get('/api/state/').json()
                    recovery = state['network_recovery']
                    self.assertEqual(recovery['phase'], 'locked')
                    self.assertEqual(recovery['generation'], 7)
                    self.assertEqual((recovery['data_locked'], recovery['motor_locked']), kinds)
                    self.assertTrue(recovery['data_locked'] or recovery['motor_locked'])
                    self.assertEqual(state['motor_command']['command'], 'none')
                    self.assertFalse(state['esp32_feeder_capable'])
                    self.assertFalse(state['esp32_sorter_capable'])
                    self.assertIsNone(state['esp32_feeder_state'])
                    self.assertIsNone(state['feeder_sensor_state'])
                    ack = self.post('/api/esp32/report/', self.device(event='network_recovery_committed', command_id=42))
                    self.assertEqual(ack.json()['reason'], 'network_recovery_ack_mismatch')
                    self.assertIsNotNone(views.APP_STATE['network_recovery'])

    def test_restore_empty_lock_kinds_conservatively_requires_motor_recovery(self):
        self.prepare()
        for phase in ('locked', 'homing', 'committing'):
            for mode in ('hardware', 'photo_only'):
                with self.subTest(phase=phase, mode=mode):
                    dataset_store.write_recovery_payload(views._network_recovery_path(), {
                        'phase': phase, 'generation': 8, 'reason': 'network_changed',
                        'hardware_mode': mode, 'data_locked': False, 'motor_locked': False,
                    })
                    views.reset_runtime_state_for_tests()
                    recovery = self.client.get('/api/state/').json()['network_recovery']
                    self.assertEqual(recovery['phase'], 'locked')
                    self.assertEqual(recovery['generation'], 8)
                    self.assertFalse(recovery['data_locked'])
                    self.assertTrue(recovery['motor_locked'])
                    self.assertTrue(recovery['data_locked'] or recovery['motor_locked'])

    def test_legacy_photo_only_interlock_allows_photos_until_hardware_recovery(self):
        dataset_store.write_recovery_payload(views._network_recovery_path(), {
            'phase': 'locked', 'generation': 7,
            'reason': 'esp32_network_interlocked', 'hardware_mode': 'photo_only',
        })
        views.reset_runtime_state_for_tests()
        self.network.start_run(port=8443)
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['network_recovery']['data_locked'])
        self.assertTrue(state['network_recovery']['motor_locked'])
        self.assertTrue(state['network']['selection_allowed'])
        self.assertEqual(self.select().status_code, 200)
        self.assertEqual(self.post('/api/notification/', {'enabled': True}).status_code, 200)
        self.client.get('/api/camera/state/?camera_ready=1')
        response = self.post('/api/photo_capture/')
        self.assertEqual(response.status_code, 200, response.content)
        capture = response.json()
        for station in (1, 2, 3):
            camera = self.client.get('/api/camera/state/').json()
            response = self._upload_station(capture['active_fruit_id'], camera['capture_token'], station)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(self.post('/api/discard/', {
            'fruit_id': capture['active_fruit_id'], 'capture_token': views.APP_STATE['capture_token'],
        }).status_code, 200)
        self.assertEqual(self.select().status_code, 200)
        recovery = self.client.get('/api/state/').json()['network_recovery']
        self.assertEqual((recovery['data_locked'], recovery['motor_locked']), (False, True))
        self.assertEqual(recovery['generation'], 7)
        self.assertEqual(recovery['phase'], 'locked')
        self.assertEqual(self.post('/api/collection_options/', {
            'work_mode': 'collection', 'hardware_mode': 'hardware',
        }).status_code, 200)
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertEqual(self.post('/api/feeder/test/').json()['reason'], 'esp32_network_interlocked')
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True}).json()['reason'],
                         'network_recovery_confirmation_required')
        response = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
        self.assertEqual(response.status_code, 200, response.content)
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        ack['event'] = 'network_recovery_committed'
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
        self.assertIsNone(views.APP_STATE['network_recovery'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_every_control_device_entry_updates_facts_while_locked(self):
        self.prepare()
        self.lock()
        for route, method in (('/api/esp32/heartbeat/', 'get'), ('/api/esp32/command/', 'get'),
                              ('/api/esp32/report/', 'post'), ('/api/esp32_trigger/', 'post')):
            with self.subTest(route=route):
                views.reset_runtime_state_for_tests()
                self.client.get('/api/state/')
                device = self.device(network_interlocked='1', feeder_sensor_state='blocked', feeder_state='awaiting_fruit',
                                     event='network_aborted')
                response = self.client.get(route, device) if method == 'get' else self.post(route, device)
                state = self.client.get('/api/state/').json()
                self.assertTrue(state['esp32_feeder_capable'], response.content)
                self.assertTrue(state['esp32_sorter_capable'])
                self.assertEqual(state['esp32_feeder_state'], 'awaiting_fruit')
                self.assertEqual(state['feeder_sensor_state'], 'blocked')
                self.assertTrue(views.APP_STATE['esp32_interlocked'])
                saved = json.loads(views._network_recovery_path().read_text(encoding='utf-8'))
                self.assertNotIn('esp32_feeder_capable', saved)
                self.assertNotIn('esp32_interlocked', saved)
                self.assertEqual(self.client.get('/api/esp32/command/', self.device()).json()['command'], 'none')

    def test_motor_recovery_ignores_expired_camera_during_two_phase_handshake(self):
        self.prepare()
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).status_code, 200)
        generation = views.APP_STATE['recovery_generation']
        views.APP_STATE['camera_last_live_frame_monotonic'] = views.time.monotonic() - 11
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'], network_interlocked='1')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        ack['event'] = 'network_recovery_committed'
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['recovery_generation'], generation)
        self.assertIsNone(state['network_recovery'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_device_interlock_adds_motor_lock_to_existing_data_lock(self):
        self.prepare()
        views.APP_STATE['network_recovery'] = {
            'phase': 'locked', 'generation': 1, 'reason': 'camera_connection_lost', 'hardware_mode': 'hardware',
            'data_locked': True, 'motor_locked': False,
        }
        views.APP_STATE['recovery_generation'] = 1
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        marker = json.loads(views._network_recovery_path().read_text(encoding='utf-8'))
        self.assertTrue(marker['data_locked'])
        self.assertTrue(marker['motor_locked'])
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).status_code, 200)
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')

    def test_photo_only_adopts_newer_device_generation_without_aborting_photos(self):
        self.prepare(photo_only=True)
        first = self.client.get('/api/esp32/heartbeat/', self.device(
            recovery_generation=8, network_interlocked='1')).json()
        self.assertFalse(first['control_allowed'])
        state = self.client.get('/api/state/').json()
        self.assertFalse(state['network_recovery']['data_locked'])
        self.assertTrue(state['network_recovery']['motor_locked'])
        capture = self.post('/api/photo_capture/').json()
        self.assertTrue(capture['pending_capture'])
        token = capture['capture_token']
        second = self.client.get('/api/esp32/heartbeat/', self.device(
            recovery_generation=9, network_interlocked='1')).json()
        self.assertFalse(second['control_allowed'])
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['recovery_generation'], 9)
        self.assertEqual(state['capture_token'], token)
        self.assertTrue(state['pending_capture'])
        self.assertFalse(state['network_recovery']['data_locked'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.assertEqual(self._upload_station('fruit_001', token, 1).status_code, 200)

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

    def test_recovery_refreshes_blocked_sensor_before_committing_new_feed(self):
        self.prepare()
        views.APP_STATE['capture_timing']['feeder_calibrated'] = True
        applied = self.post('/api/esp32/report/', self.device(
            event='timing_config_applied', timing_revision=views.APP_STATE['capture_timing_revision'],
        ))
        self.assertEqual(applied.status_code, 200)
        self.client.get('/api/esp32/command/', self.device(feeder_sensor_state='blocked'))
        self.lock()
        locked = self.client.get('/api/esp32/command/', self.device(
            network_interlocked='1', feeder_sensor_state='blocked',
        )).json()
        self.assertEqual(locked['command'], 'none')
        self.assertEqual(self.post('/api/auto_run/', {
            'enabled': True, 'recovery_confirmed': True,
        }).status_code, 200)
        command = self.client.get('/api/esp32/command/', self.device(
            network_interlocked='1', feeder_sensor_state='clear',
        )).json()
        self.assertEqual(command['command'], 'recover_network')
        self.assertFalse(command['control_allowed'])
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        ack = self.device(event='network_recovery_completed',
                          command_id=command['command_id'], network_interlocked='1')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
        self.assertEqual(views.APP_STATE['motor_command']['command'], 'recover_network')
        ack.update(event='network_recovery_committed')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
        feed = views.APP_STATE['motor_command']
        self.assertIsNotNone(feed, views._auto_run_disabled_reason())
        self.assertEqual(feed['command'], 'feed_one')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['duplicate'])
        self.assertEqual(views.APP_STATE['motor_command'], feed)

    def test_recovery_sensor_blockers_never_commit_new_feed(self):
        self.prepare()
        views.APP_STATE['capture_timing']['feeder_calibrated'] = True
        applied = self.post('/api/esp32/report/', self.device(
            event='timing_config_applied', timing_revision=views.APP_STATE['capture_timing_revision'],
        ))
        self.assertEqual(applied.status_code, 200)
        for sensor in ('blocked', 'unavailable', 'invalid', ''):
            with self.subTest(sensor=sensor):
                self.client.get('/api/esp32/command/', self.device())
                self.lock()
                locked = self.client.get('/api/esp32/command/', self.device(
                    network_interlocked='1', feeder_sensor_state=sensor,
                )).json()
                self.assertEqual(locked['command'], 'none')
                self.assertEqual(self.post('/api/auto_run/', {
                    'enabled': True, 'recovery_confirmed': True,
                }).status_code, 200)
                command = self.client.get('/api/esp32/command/', self.device(
                    network_interlocked='1', feeder_sensor_state=sensor,
                )).json()
                self.assertEqual(command['command'], 'recover_network')
                ack = self.device(event='network_recovery_completed',
                                  command_id=command['command_id'], network_interlocked='1',
                                  feeder_sensor_state=sensor)
                self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_commit'])
                ack.update(event='network_recovery_committed')
                self.assertTrue(self.post('/api/esp32/report/', ack).json()['recovery_committed'])
                self.assertIsNone(views.APP_STATE['motor_command'])
                self.assertFalse(views.APP_STATE['auto_run_enabled'])

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
        self.assertEqual(dataset_store.load_counter(views._dataset_root()), 2)
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


class NetworkRecoveryMatrixTests(SimpleTestCase):
    """Public endpoint matrix, first characterized against cb97758 / 1e8f06e."""

    setUp = NetworkSafetyTests.setUp
    tearDown = NetworkSafetyTests.tearDown
    post = NetworkSafetyTests.post
    select = NetworkSafetyTests.select
    device = NetworkSafetyTests.device
    prepare = NetworkSafetyTests.prepare

    def exercise(self, mode, locks, operation):
        self.prepare(photo_only=mode == 'photo_only')
        self.client.get('/api/esp32/command/', self.device())
        state = views.APP_STATE
        state['capture_timing']['feeder_calibrated'] = operation != 'calibration'
        revision = state['capture_timing_revision']
        state['capture_timing_applied_revision'] = revision
        state['feeder_test_passed_revision'] = revision
        state['feeder_test_result'] = {'ok': True, 'stop_reason': 'hcsr04', 'timing_revision': revision}
        if locks != 'none':
            reason = {'data': 'camera_connection_lost', 'motor': 'esp32_network_interlocked',
                      'both': 'esp32_connection_lost'}[locks]
            state['network_recovery'] = {
                'phase': 'locked', 'generation': 1, 'reason': reason, 'hardware_mode': mode,
                'data_locked': locks in ('data', 'both'), 'motor_locked': locks in ('motor', 'both'),
            }
            state.update(recovery_generation=1,
                         status='idle' if mode == 'photo_only' and locks == 'motor' else 'error',
                         auto_run_recovery_reason=reason)
            dataset_store.write_recovery_payload(views._network_recovery_path(), state['network_recovery'])
        if operation in ('discard', 'reset'):
            fruit_dir = self.dataset_root / 'temp/fruit_001'
            fruit_dir.mkdir()
            (fruit_dir / 'img_01.jpg').write_bytes(b'partial photo fixture')
            views.dataset_store.save_capture_session(fruit_dir, '2026-10-14', 'collection')
            state['active_fruit_id'] = 'fruit_001'
        requests = {
            'auto': ('/api/auto_run/', {'enabled': True}),
            'photo': ('/api/photo_capture/', {}),
            'confirm': ('/api/photo_capture/' if mode == 'photo_only' else '/api/auto_run/',
                        {'enabled': True, 'recovery_confirmed': True}),
            'discard': ('/api/discard/', {'fruit_id': 'fruit_001', 'capture_token': state['capture_token']}),
            'notification': ('/api/notification/', {'enabled': True}),
            'options': ('/api/collection_options/', {'work_mode': 'collection',
                        'hardware_mode': 'photo_only' if mode == 'hardware' else 'hardware'}),
            'timing': ('/api/capture_timing/', dict(state['capture_timing'])),
            'feeder': ('/api/feeder/test/', {}),
            'calibration': ('/api/feeder/calibration/confirm/', {'timing_revision': revision}),
            'network': ('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'}),
            'setup': ('/api/network/setup_request/', {}),
            'counter': ('/api/set_counter/', {'start_id': 42}),
            'reset': ('/api/reset_dataset/', {}),
        }
        if operation == 'command':
            return self.client.get('/api/esp32/command/', self.device())
        url, data = requests[operation]
        return self.post(url, data)


def _matrix_test(mode, locks, operation, expected_reason):
    def test(self):
        response = self.exercise(mode, locks, operation)
        self.assertEqual(response.status_code, 409 if expected_reason else 200, response.content)
        self.assertEqual(response.json().get('reason'), expected_reason)
        if operation == 'counter':
            self.assertEqual(response.json()['next_fruit_id'], 'fruit_042')
        if operation == 'reset':
            self.assertTrue(response.json()['reset_done'])
            self.assertFalse((self.dataset_root / 'temp/fruit_001').exists())
        if operation in ('counter', 'reset', 'network') and locks != 'none':
            recovery = self.client.get('/api/state/').json()['network_recovery']
            self.assertEqual(recovery['phase'], 'locked')
            self.assertEqual(recovery['data_locked'], locks in ('data', 'both'))
            self.assertEqual(recovery['motor_locked'], locks in ('motor', 'both'))
            self.assertEqual(recovery['generation'], 1)
        if operation == 'command':
            self.assertEqual(response.json()['command'], 'none')
        if operation == 'confirm' and locks != 'none':
            recovery = views.APP_STATE.get('network_recovery')
            if mode == 'photo_only' and locks in ('motor', 'both'):
                self.assertTrue(recovery['motor_locked'])
                self.assertFalse(recovery['data_locked'])
            elif mode == 'hardware' and locks in ('motor', 'both'):
                self.assertEqual(views.APP_STATE['motor_command']['command'], 'recover_network')
            else:
                self.assertIsNone(recovery)
        if locks != 'none' and operation != 'confirm':
            self.assertIsNotNone(views.APP_STATE.get('network_recovery'))
        if mode == 'photo_only' or locks != 'none' and operation != 'confirm':
            self.assertIsNone(views.APP_STATE.get('motor_command'))
    return test


# Columns are literal observable reasons, not values computed by the implementation.
_MATRIX_OPERATIONS = ('auto', 'photo', 'confirm', 'discard', 'notification', 'options',
                      'timing', 'feeder', 'calibration', 'network', 'setup', 'command', 'counter', 'reset')
_RECOVERY_MATRIX = {
    ('hardware', 'none'): (None, 'photo_only_required', None, None, None, None,
                           None, None, None, None, None, None, None, None),
    ('photo_only', 'none'): ('hardware_mode_required', None, None, None, None, None,
                             None, 'hardware_mode_required', None, None, None, None, None, None),
}
for _mode in ('hardware', 'photo_only'):
    for _locks, _reason in (('data', 'camera_connection_lost'), ('motor', 'esp32_network_interlocked'),
                            ('both', 'esp32_connection_lost')):
        _RECOVERY_MATRIX[(_mode, _locks)] = (
            'network_recovery_confirmation_required' if _mode == 'hardware' else 'hardware_mode_required',
            'network_recovery_confirmation_required' if _mode == 'photo_only' else 'photo_only_required',
            None, None, None, _reason, _reason, _reason, _reason,
            None, 'network_recovery_required', None, None, None,
        )
_RECOVERY_MATRIX[('hardware', 'motor')] = (
    'network_recovery_confirmation_required', 'photo_only_required', None, None, None, None,
    'esp32_network_interlocked', 'esp32_network_interlocked', 'esp32_network_interlocked',
    None, 'network_recovery_required', None, None, None,
)
_RECOVERY_MATRIX[('photo_only', 'motor')] = (
    'hardware_mode_required', None, None, None, None, None, None, 'hardware_mode_required', None,
    None, 'network_recovery_required', None, None, None,
)
for (_mode, _locks), _reasons in _RECOVERY_MATRIX.items():
    for _operation, _reason in zip(_MATRIX_OPERATIONS, _reasons, strict=True):
        setattr(NetworkRecoveryMatrixTests, f'test_matrix_{_mode}_{_locks}_{_operation}',
                _matrix_test(_mode, _locks, _operation, _reason))
