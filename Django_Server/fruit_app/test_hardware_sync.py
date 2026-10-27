"""Hardware ACKs survive Django, but never another board boot or new values."""
import json
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from . import capture_timing, views
from . import test_network_safety as safety
from .tests import write_hardware_params


class HardwareSyncTests(SimpleTestCase):
    setUp = safety.NetworkSafetyTests.setUp
    tearDown = safety.NetworkSafetyTests.tearDown
    post = safety.NetworkSafetyTests.post
    select = safety.NetworkSafetyTests.select
    device = safety.NetworkSafetyTests.device
    prepare = safety.NetworkSafetyTests.prepare

    def acknowledge(self, **fields):
        return self.post('/api/esp32/report/', self.device(
            event='timing_config_applied', timing_revision=views.APP_STATE['capture_timing_revision'],
            **fields))

    def test_ack_survives_django_restart_only_after_same_boot_is_observed(self):
        self.prepare()
        revision = views.APP_STATE['capture_timing_revision']
        self.assertGreater(revision, 999999)
        self.assertEqual(self.acknowledge().status_code, 200)
        views.reset_runtime_state()
        self.client.get('/api/state/')
        self.assertEqual(views.APP_STATE['capture_timing_revision'], revision)
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], 0)
        response = self.client.get('/api/esp32/command/', self.device()).json()
        self.assertEqual(response['command'], 'none')
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], revision)
        self.assertFalse(views.APP_STATE['auto_run_enabled'])
        self.assertIsNone(views.APP_STATE['motor_command'])

    def test_other_boot_must_send_its_own_ack_even_with_same_values(self):
        self.prepare()
        self.assertEqual(self.acknowledge().status_code, 200)
        self.client.get('/api/esp32/command/', self.device(boot_id='board-b'))
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], 0)
        self.assertEqual(self.acknowledge(boot_id='board-b').status_code, 200)
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'],
                         views.APP_STATE['capture_timing_revision'])

    def test_changed_values_and_rollback_always_increase_revision_and_clear_ack(self):
        self.prepare()
        self.assertEqual(self.acknowledge().status_code, 200)
        first = views.APP_STATE['capture_timing_revision']
        original = views.APP_STATE['capture_timing'].copy()
        write_hardware_params(self.hardware_params_path, fruit_settle_ms=500)
        views.reset_runtime_state()
        self.client.get('/api/state/')
        second = views.APP_STATE['capture_timing_revision']
        self.assertGreater(second, first)
        self.client.get('/api/esp32/command/', self.device())
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], 0)
        write_hardware_params(self.hardware_params_path, **original)
        views.reset_runtime_state()
        self.client.get('/api/state/')
        self.assertGreater(views.APP_STATE['capture_timing_revision'], second)

    def test_failed_receipt_save_does_not_allow_start_and_device_can_retry(self):
        self.prepare()
        with mock.patch.object(capture_timing, 'write_recovery_payload', side_effect=OSError('disk full')):
            rejected = self.acknowledge()
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(rejected.json()['reason'], 'hardware_sync_save_failed')
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], 0)
        self.assertEqual(self.acknowledge().status_code, 200)

    def test_corrupt_publication_never_silently_reuses_legacy_revision(self):
        for invalid in ([], None):
            with self.subTest(invalid=invalid):
                views._hardware_sync_path().write_text(json.dumps(invalid), encoding='utf-8')
                views.reset_runtime_state()
                with self.assertRaises(ImproperlyConfigured):
                    self.client.get('/api/state/')

    def test_smaller_content_fingerprint_still_publishes_larger_wire_revision(self):
        path = views._hardware_sync_path().with_name('ordered-test.json')
        first = capture_timing.publish(path, {'servo_settle_ms': 200}, 900000)
        second = capture_timing.publish(path, {'servo_settle_ms': 250}, 100000)
        self.assertGreater(second['revision'], first['revision'])
        again = capture_timing.publish(path, {'servo_settle_ms': 250}, 100000)
        self.assertEqual(again['revision'], second['revision'])

    def test_old_ack_does_not_approve_current_configuration(self):
        self.prepare()
        response = self.post('/api/esp32/report/', self.device(
            event='timing_config_applied', timing_revision=177464))
        self.assertTrue(response.json()['ignored'])
        self.assertEqual(views.APP_STATE['capture_timing_applied_revision'], 0)
        self.assertIsNone(views.APP_STATE['capture_timing_sync_record']['ack'])
