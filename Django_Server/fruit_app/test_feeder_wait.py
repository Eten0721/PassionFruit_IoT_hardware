"""Explicitly end a timed-out feed through the existing device handshake."""
from unittest import mock

from django.test import SimpleTestCase

from . import views
from . import test_network_safety as safety


class FeederWaitTests(SimpleTestCase):
    setUp = safety.NetworkSafetyTests.setUp
    tearDown = safety.NetworkSafetyTests.tearDown
    post = safety.NetworkSafetyTests.post
    select = safety.NetworkSafetyTests.select
    device = safety.NetworkSafetyTests.device
    prepare = safety.NetworkSafetyTests.prepare

    def timeout(self):
        self.prepare()
        revision = views.APP_STATE['capture_timing_revision']
        self.assertEqual(self.post('/api/esp32/report/', self.device(
            event='timing_config_applied', timing_revision=revision)).status_code, 200)
        started = self.post('/api/auto_run/', {'enabled': True})
        self.assertEqual(started.status_code, 200, started.content)
        command = started.json()['motor_command']
        response = self.post('/api/esp32/report/', self.device(
            event='feeder_max_run_timeout', command_id=command['command_id'],
            feeder_state='awaiting_fruit', feeder_elapsed_ms=5001,
            feeder_max_run_ms=command['feeder_max_run_ms'], feeder_stop_reason='max_run_timeout'))
        self.assertEqual(response.status_code, 200, response.content)

    def finish(self):
        response = self.post('/api/feeder/finish_wait/', {'recovery_confirmed': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['recovery_pending'])
        command = self.client.get('/api/esp32/command/', self.device(network_interlocked='1')).json()
        self.assertEqual(command['command'], 'recover_network')
        self.assertFalse(command['control_allowed'])
        return command

    def commit(self, command):
        ack = self.device(event='network_recovery_completed', command_id=command['command_id'],
                          network_interlocked='1', setup_allowed='0')
        response = self.post('/api/esp32/report/', ack)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['recovery_commit'])
        ack['event'] = 'network_recovery_committed'
        response = self.post('/api/esp32/report/', ack)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['recovery_committed'])
        self.client.get('/api/esp32/heartbeat/', self.device())
        return ack

    def test_finish_requires_confirmation_and_blocks_changes_until_both_acks(self):
        self.timeout()
        state = self.client.get('/api/state/').json()
        self.assertTrue(state['can_start_auto_run'])
        self.assertIsNone(state['auto_run_start_disabled_reason'])
        self.assertTrue(state['can_finish_feeder_wait'])
        self.assertFalse(state['can_change_options'])
        self.assertEqual(self.post('/api/feeder/finish_wait/').status_code, 409)
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        # Ending a wait never requires a working camera or changes feeder timing.
        views.APP_STATE['camera_last_live_frame_monotonic'] = None
        command = self.finish()
        self.assertFalse(views.APP_STATE['network_recovery']['resume_auto_run'])
        self.assertFalse(self.client.get('/api/state/').json()['can_change_options'])
        self.assertEqual(self.select().status_code, 409)
        self.assertEqual(self.post('/api/network/setup_request/').status_code, 409)
        self.assertTrue(self.post('/api/feeder/finish_wait/', {'recovery_confirmed': True}).json()['recovery_pending'])
        self.assertEqual(views.APP_STATE['motor_command']['command_id'], command['command_id'])
        wrong = self.post('/api/esp32/report/', self.device(
            event='network_recovery_completed', command_id=command['command_id'] + 1, network_interlocked='1'))
        self.assertEqual(wrong.status_code, 409)
        ack = self.commit(command)
        state = self.client.get('/api/state/').json()
        self.assertEqual(state['status'], 'idle')
        self.assertTrue(state['can_change_options'])
        self.assertTrue(state['network']['selection_allowed'])
        self.assertEqual(state['network']['setup']['status'], 'allowed')
        self.assertIsNone(state['motor_command'].get('command_id') or None)
        self.assertFalse(state['auto_run_enabled'])
        self.assertIsNone(state['last_error_reason'])
        self.assertFalse(views._auto_trigger_enabled())
        self.client.get('/api/camera/state/?camera_ready=1')
        trigger = self.post('/api/esp32_trigger/', self.device())
        self.assertEqual(trigger.status_code, 409)
        self.assertEqual(trigger.json()['reason'], 'auto_trigger_disabled')
        self.assertTrue(self.post('/api/esp32/report/', ack).json()['duplicate'])
        self.assertIsNone(views.APP_STATE['motor_command'])
        self.client.get('/api/esp32/heartbeat/', self.device())
        self.assertEqual(self.post('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'photo_only',
        }).status_code, 200)

    def test_network_selection_and_esp32_setup_are_available_after_finish(self):
        self.timeout()
        self.commit(self.finish())
        self.assertEqual(self.post('/api/network/setup_request/').status_code, 200)
        self.assertEqual(self.select().status_code, 200)
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_restart_preserves_paused_trigger_and_explicit_start_reenables_it(self):
        self.timeout()
        self.commit(self.finish())
        views.reset_runtime_state()
        self.client.get('/api/state/')
        self.assertTrue(views.APP_STATE['auto_trigger_paused'])
        self.assertFalse(views._auto_trigger_enabled())
        self.client.get('/api/esp32/heartbeat/', self.device())
        revision = views.APP_STATE['capture_timing_revision']
        self.post('/api/esp32/report/', self.device(event='timing_config_applied', timing_revision=revision))
        self.client.get('/api/camera/state/?camera_ready=1')
        response = self.post('/api/auto_run/', {'enabled': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(views.APP_STATE['auto_trigger_paused'])
        self.assertEqual(response.json()['motor_command']['command'], 'feed_one')

    def test_busy_round_sensor_failure_and_other_recovery_are_not_cleared(self):
        self.timeout()
        cases = [
            ({'active_fruit_id': 'fruit_999'}, 'temp_fruit_exists'),
            ({'motor_command': {'command': 'start_sequence', 'command_id': 99}}, 'pending_motor_command'),
            ({'pending_capture': True}, 'capture_incomplete'),
            ({'detection_status': 'running'}, 'detection_in_progress'),
            ({'gate3_waiting_fruit_id': 'fruit_999'}, 'gate3_manual_removal_required'),
            ({'sorter_recovery_required': True}, 'sorter_recovery_required'),
            ({'auto_run_recovery_reason': 'feeder_sensor_unavailable'}, 'feeder_wait_not_required'),
            ({'hardware_mode': 'photo_only'}, 'hardware_mode_required'),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields), mock.patch.dict(views.APP_STATE, fields):
                response = self.post('/api/feeder/finish_wait/', {'recovery_confirmed': True})
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()['reason'], expected)
                self.assertIsNone(views.APP_STATE.get('network_recovery'))

    def test_retry_after_lost_ack_retains_no_feed_policy(self):
        self.timeout()
        self.finish()
        views.NETWORK_RECOVERY.lock('network_recovery_ack_timeout', force=True)
        self.assertFalse(views.APP_STATE['network_recovery']['resume_auto_run'])
        views.reset_runtime_state()
        self.client.get('/api/state/')
        self.assertFalse(views.APP_STATE['network_recovery']['resume_auto_run'])
        self.client.get('/api/esp32/heartbeat/', self.device(network_interlocked='1'))
        response = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
        self.assertEqual(response.status_code, 200, response.content)
        self.commit(response.json()['motor_command'])
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])
