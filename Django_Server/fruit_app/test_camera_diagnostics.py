"""Untrusted phone diagnostics at the HTTP and snapshot boundaries."""
import json
from unittest import mock

from django.test import SimpleTestCase

from . import local_network, tests as existing, views


class CameraDiagnosticsTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown

    def post(self, payload):
        return self.client.post('/api/camera/diagnostics/', json.dumps(payload),
                                content_type='application/json')

    def test_valid_report_is_latest_snapshot_with_server_timestamp(self):
        payload = {
            'event': 'camera_started', 'secure_context': True,
            'get_user_media_available': True, 'track_muted': False,
            'permission': 'granted', 'video_track_state': 'live',
            'rtc_connection_state': 'connected', 'rtc_ice_connection_state': 'completed',
            'video_width': 1920, 'video_height': 1080, 'last_live_frame_age_ms': 200,
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        snapshot = views.get_camera_diagnostics()
        self.assertEqual({key: snapshot[key] for key in payload}, payload)
        self.assertRegex(snapshot['received_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
        snapshot['event'] = 'changed'
        self.assertEqual(views.get_camera_diagnostics()['event'], 'camera_started')
        self.assertEqual(self.post({'event': 'track_ended'}).status_code, 200)
        latest = views.get_camera_diagnostics()
        self.assertEqual(latest['event'], 'track_ended')
        self.assertIsNone(latest['video_width'])
        self.assertIsNone(latest['permission'])

    def test_unknown_fields_are_discarded_without_echo_or_logging(self):
        with self.assertNoLogs(views.logger, level='DEBUG'):
            response = self.post({'event': 'camera_failed', 'permission': 'unknown',
                                  'userAgent': 'private browser', 'label': 'private lens',
                                  'message': 'ignore previous instructions', 'received_at': 'forged'})
        self.assertEqual(response.json(), {'ok': True})
        snapshot = views.get_camera_diagnostics()
        self.assertNotIn('userAgent', snapshot)
        self.assertNotIn('label', snapshot)
        self.assertNotIn('message', snapshot)
        self.assertNotEqual(snapshot['received_at'], 'forged')

    def test_invalid_values_preserve_previous_snapshot(self):
        self.post({'event': 'camera_started'})
        previous = views.get_camera_diagnostics()
        invalid = [
            {}, {'event': None}, {'event': 'unknown'}, {'event': []},
            {'permission': 'PermissionDeniedError'}, {'permission': {}},
            {'video_track_state': 'muted'}, {'video_track_state': 1},
            {'rtc_connection_state': 'completed'}, {'rtc_ice_connection_state': 'connecting'},
        ]
        for field in ('secure_context', 'get_user_media_available', 'track_muted'):
            invalid.extend({field: value} for value in (0, 1, 'true', [], {}))
        for field, maximum in (('video_width', 10000), ('video_height', 10000),
                               ('last_live_frame_age_ms', 86400000)):
            invalid.extend({field: value} for value in (-1, maximum + 1, 1.5, True, '1', [], {}))
        for fields in invalid:
            payload = {'event': 'camera_failed', **fields} if fields else {}
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['reason'], 'camera_diagnostics_invalid')
                self.assertEqual(views.get_camera_diagnostics(), previous)

    def test_non_json_and_oversized_bodies_preserve_previous_snapshot(self):
        self.post({'event': 'camera_started'})
        previous = views.get_camera_diagnostics()
        cases = [
            ('not json', 'application/json'), ('[]', 'application/json'),
            ('null', 'application/json'), ('"text"', 'application/json'),
            ('{"event": "camera_started"}', 'text/plain'),
            (b'\xff', 'application/json'),
            ('{"event":"camera_started","extra":"' + 'x' * 4096 + '"}', 'application/json'),
            ('[' * 1500 + '0' + ']' * 1500, 'application/json'),
        ]
        for body, content_type in cases:
            with self.subTest(content_type=content_type, size=len(body)):
                response = self.client.post('/api/camera/diagnostics/', body, content_type=content_type)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['reason'], 'camera_diagnostics_invalid')
                self.assertEqual(views.get_camera_diagnostics(), previous)
        body = json.dumps({'event': 'camera_started'}).ljust(4096)
        self.assertEqual(self.client.post('/api/camera/diagnostics/', body,
                                         content_type='application/json').status_code, 200)

    def test_optional_nulls_numeric_boundaries_and_all_events_are_accepted(self):
        for event in ('camera_started', 'camera_failed', 'track_ended', 'rtc_state_changed'):
            for value in (0, 10000, None):
                with self.subTest(event=event, value=value):
                    response = self.post({'event': event, 'secure_context': None,
                                          'permission': None, 'video_width': value, 'video_height': value,
                                          'last_live_frame_age_ms': 86400000})
                    self.assertEqual(response.status_code, 200)

    def test_network_identity_mismatch_does_not_save(self):
        self.post({'event': 'camera_started'})
        previous = views.get_camera_diagnostics()
        good = {'secure': True, 'HTTP_HOST': '192.168.50.10:8000', 'REMOTE_ADDR': '192.168.50.20'}
        cases = [
            ({**good, 'HTTP_HOST': '192.168.50.11:8000'}, 'network_origin_mismatch'),
            ({**good, 'secure': False}, 'network_origin_mismatch'),
            ({**good, 'HTTP_ORIGIN': 'https://other.example'}, 'network_origin_mismatch'),
            ({**good, 'REMOTE_ADDR': '192.168.51.20'}, 'network_subnet_mismatch'),
            ({**good, 'REMOTE_ADDR': 'invalid'}, 'network_source_invalid'),
        ]
        for request, reason in cases:
            with self.subTest(reason=reason):
                response = self.client.post('/api/camera/diagnostics/', '{"event":"camera_failed"}',
                                            content_type='application/json', **request)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['reason'], reason)
                self.assertEqual(views.get_camera_diagnostics(), previous)
        self.assertEqual(self.client.post('/api/camera/diagnostics/', '{"event":"camera_started"}',
                                         content_type='application/json', **good).status_code, 200)

    def test_network_selection_clears_snapshot(self):
        self.post({'event': 'camera_started'})
        response = self.client.post('/api/network/',
                                    json.dumps({'interface_id': '7', 'ipv4': '192.168.50.10'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(views.get_camera_diagnostics())

    def test_automatic_network_change_clears_snapshot(self):
        self.safety_monitor_patcher.stop()
        self.client.get('/api/state/')
        self.post({'event': 'camera_started'})
        local_network.NETWORK.select('7', '192.168.50.10')
        self.client.get('/api/state/')
        self.assertIsNone(views.get_camera_diagnostics())

    def test_snapshot_is_not_exposed_in_state_apis(self):
        self.post({'event': 'camera_started'})
        for url in ('/api/state/', '/api/camera/state/'):
            payload = self.client.get(url).json()
            self.assertNotIn('camera_diagnostics', payload)
            self.assertNotIn('received_at', payload)
            self.assertNotIn('camera_started', json.dumps(payload))

    def test_diagnostics_do_not_touch_capture_state_or_files(self):
        previous = dict(views.APP_STATE)
        with mock.patch.object(views, '_ensure_dataset_structure') as ensure, \
                mock.patch.object(views, '_check_network_safety') as safety:
            self.assertEqual(self.post({'event': 'camera_started'}).status_code, 200)
            views.get_camera_diagnostics()
        ensure.assert_not_called()
        safety.assert_not_called()
        self.assertEqual({key: value for key, value in views.APP_STATE.items() if key != 'camera_diagnostics'},
                         {key: value for key, value in previous.items() if key != 'camera_diagnostics'})
