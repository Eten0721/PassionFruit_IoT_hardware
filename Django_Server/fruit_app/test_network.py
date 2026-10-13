"""Network onboarding contracts at the Django HTTP interface."""
import json
from unittest import mock

from django.test import SimpleTestCase
from . import local_network, tests as existing, views


class NetworkOnboardingTests(SimpleTestCase):
    tearDown = existing.DataCollectionFlowTests.tearDown
    _fake_image = existing.DataCollectionFlowTests._fake_image
    _upload_station = existing.DataCollectionFlowTests._upload_station

    def setUp(self):
        existing.DataCollectionFlowTests.setUp(self)
        self.hardware_guard_patcher.stop()
        self.device_observer_patcher.stop()
        self.safety_monitor_patcher.stop()
        existing.views.APP_STATE['camera_last_live_frame_monotonic'] = None
        self.interfaces = [{
            'interface_id': '7', 'name': 'Wi-Fi', 'ipv4': '192.168.50.10', 'prefix': 24,
        }]
        self.inventory = mock.Mock(side_effect=lambda: self.interfaces.copy())
        self.publishers = []
        def publish(*args):
            publisher = mock.Mock()
            self.publishers.append((args, publisher))
            return publisher
        self.publisher_factory = mock.Mock(side_effect=publish)
        self.network = local_network.LocalNetwork(self.inventory, self.publisher_factory)
        self.addCleanup(self.network.stop_monitor)
        self.network.start_run(port=8443)
        patcher = mock.patch.object(local_network, 'NETWORK', self.network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, url, payload=None, **kwargs):
        payload = {'dataset_mode': views.APP_STATE['work_mode'], **(payload or {})}
        return self.client.post(url, json.dumps(payload), content_type='application/json', **kwargs)

    def select(self):
        return self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'})

    def test_new_process_requires_explicit_selection(self):
        response = self.client.get('/api/network/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['selected'])
        response = self.client.post('/api/photo_capture/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['reason'], 'network_selection_required')
        self.assertEqual(self.client.get('/api/network/qr.svg').status_code, 409)
        self.assertEqual(self.client.get('/api/state/').status_code, 200)
        self.assertEqual(self.client.get('/api/camera/state/').status_code, 200)

    def test_single_candidate_is_not_selected_and_info_uses_actual_port(self):
        info = self.client.get('/api/network/info/').json()
        self.assertEqual(len(info['interfaces']), 1)
        self.assertIsNone(info['camera_url'])
        result = self.select()
        self.assertEqual(result.status_code, 200)
        info = self.client.get('/api/network/info/').json()
        self.assertEqual(info['server_url'], 'https://192.168.50.10:8443')
        self.assertEqual(info['camera_url'], 'https://192.168.50.10:8443/camera/')
        self.assertEqual(info['network_revision'], 1)
        self.assertEqual(info['discovery']['reason'], 'network_discovery_pending')
        self.assertEqual(info['setup']['status'], 'unknown')

    def test_failed_inventory_empty_inventory_and_stale_choices(self):
        self.inventory.side_effect = local_network.NetworkError('network_inventory_failed')
        self.assertEqual(self.select().json()['reason'], 'network_inventory_failed')
        self.inventory.side_effect = lambda: []
        self.assertEqual(self.select().json()['reason'], 'no_connected_network')
        self.inventory.side_effect = lambda: self.interfaces
        self.assertEqual(self.post('/api/network/', {'interface_id': '99', 'ipv4': '192.168.50.10'}).json()['reason'], 'network_selection_stale')
        self.assertEqual(self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.99'}).json()['reason'], 'network_selection_stale')
        self.assertEqual(self.post('/api/network/', {'interface_id': 7, 'ipv4': '192.168.50.10'}).status_code, 400)

    def test_device_requests_require_selected_origin_and_subnet(self):
        self.select()
        self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'photo_only'})
        good = {'secure': True, 'HTTP_HOST': '192.168.50.10:8443', 'REMOTE_ADDR': '192.168.50.20'}
        cases = [
            ({**good, 'HTTP_HOST': '192.168.50.11:8443'}, 'network_origin_mismatch'),
            ({**good, 'secure': False}, 'network_origin_mismatch'),
            ({**good, 'HTTP_ORIGIN': 'https://other.example'}, 'network_origin_mismatch'),
            ({**good, 'REMOTE_ADDR': '192.168.51.20'}, 'network_subnet_mismatch'),
            ({**good, 'REMOTE_ADDR': 'invalid'}, 'network_source_invalid'),
        ]
        for request, reason in cases:
            with self.subTest(reason=reason):
                response = self.client.get('/api/camera/state/?camera_ready=1', **request)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['reason'], reason)
        response = self.client.get('/api/camera/state/?camera_ready=1', **good)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'}, **good).json()['reason'], 'network_local_dashboard_required')

    def test_new_operations_are_blocked_and_cleanup_remains_available(self):
        for route in ('/api/photo_capture/', '/api/recapture/', '/api/upload_images/',
                      '/api/capture_started/', '/api/classify/', '/api/feeder/test/',
                      '/api/feeder/calibration/confirm/', '/api/esp32_trigger/',
                      '/api/esp32/report/', '/api/collection_options/', '/api/webrtc/offer'):
            with self.subTest(route=route):
                self.assertEqual(self.post(route).json()['reason'], 'network_selection_required')
        self.assertEqual(self.client.get('/api/esp32/command/').json()['reason'], 'network_selection_required')
        self.assertEqual(self.post('/api/auto_run/', {'enabled': True}).json()['reason'], 'network_selection_required')
        self.assertEqual(self.post('/api/auto_run/', {'enabled': False}).status_code, 200)
        self.select()
        for route in ('/api/esp32/report/', '/api/esp32_trigger/', '/api/feeder/test/'):
            self.assertEqual(self.post(route).json()['reason'], 'network_capability_missing')
        self.assertEqual(self.client.get('/api/esp32/command/?network_capability=local_network_v1').json()['reason'], 'network_identity_mismatch')

    def test_interface_expiry_requires_confirmation_and_allows_discard(self):
        self.select()
        self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'photo_only'})
        self.client.get('/api/camera/state/?camera_ready=1')
        capture = self.post('/api/photo_capture/').json()
        self._upload_station('fruit_001', capture['capture_token'], 1)
        self.interfaces = [{**self.interfaces[0], 'ipv4': '192.168.50.11'}]
        with mock.patch('fruit_app.local_network.time.monotonic', return_value=existing.views.time.monotonic() + 2):
            info = self.client.get('/api/network/').json()
            self.assertTrue(info['selected'])
            self.assertIsNone(info['reason'])
            self.assertEqual(info['camera_url'], 'https://192.168.50.11:8443/camera/')
            self.assertEqual(self.post('/api/photo_capture/').json()['reason'], 'network_recovery_confirmation_required')
        result = self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.11'})
        self.assertEqual(result.status_code, 409)
        self.assertTrue((self.dataset_root / 'temp' / 'fruit_001' / 'img_01.jpg').exists())
        token = self.client.get('/api/state/').json()['capture_token']
        self.assertEqual(self.post('/api/discard/', {'fruit_id': 'fruit_001', 'capture_token': token}).status_code, 200)
        self.assertEqual(self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.11'}).status_code, 200)

    def test_selected_network_photo_only_saves_three_images_and_classifies(self):
        self.select()
        self.assertEqual(self.post('/api/collection_options/', {'work_mode': 'collection', 'hardware_mode': 'photo_only'}).status_code, 200)
        device = {'secure': True, 'HTTP_HOST': '192.168.50.10:8443', 'REMOTE_ADDR': '192.168.50.20'}
        self.client.get('/api/camera/state/?camera_ready=1', **device)
        capture = self.post('/api/photo_capture/').json()
        for station in (1, 2, 3):
            capture = self.client.get('/api/camera/state/', **device).json()
            response = self.client.post('/api/upload_images/', {
                'fruit_id': 'fruit_001', 'capture_token': capture['capture_token'],
                'station_index': station, 'image': self._fake_image(station),
            }, **device)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['motor_command']['command'], 'none')
        result = self.post('/api/classify/', {'fruit_id': 'fruit_001', 'capture_token': capture['capture_token'], 'label': '上等'})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['sorter_command_queued'])
        self.assertEqual(len(list((self.dataset_root / '上等' / 'fruit_001').glob('*.jpg'))), 3)
        self.assertFalse(self.motor_command_sequence_path.exists())

    def test_svg_qr_decodes_to_selected_ipv4_url_without_cache(self):
        import re
        from xml.etree import ElementTree
        import cv2
        import numpy as np
        self.select()
        response = self.client.get('/api/network/qr.svg', HTTP_HOST='localhost:8443')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        root = ElementTree.fromstring(response.content)
        size = int(float(root.attrib['viewBox'].split()[2]) * 10)
        pixels = np.full((size, size), 255, dtype=np.uint8)
        path = root.find('{http://www.w3.org/2000/svg}path').attrib['d']
        rectangles = re.findall(r'M([0-9.]+),([0-9.]+)H([0-9.]+)V([0-9.]+)H[0-9.]+z', path)
        self.assertTrue(rectangles)
        for x, y, right, bottom in rectangles:
            x, y, right, bottom = [int(float(value) * 10) for value in (x, y, right, bottom)]
            pixels[y:bottom, x:right] = 0
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(pixels)
        self.assertEqual(decoded, 'https://192.168.50.10:8443/camera/')
        self.assertEqual(decoded, self.client.get('/api/network/info/').json()['camera_url'])

    def test_windows_inventory_filters_unusable_addresses_and_reports_query_failure(self):
        import subprocess
        from types import SimpleNamespace
        rows = [{'interface_id': '7', 'name': 'Wi-Fi', 'ipv4': address, 'prefix': 24}
                for address in ('192.168.50.10', '127.0.0.1', '169.254.1.1', '0.0.0.0', '224.0.0.1', '192.168.50.255')]
        self.inventory.side_effect = local_network.read_windows_interfaces
        result = SimpleNamespace(stdout=json.dumps(rows).encode('utf-8'))
        with mock.patch('fruit_app.local_network.subprocess.run', return_value=result):
            info = self.client.get('/api/network/').json()
            self.assertEqual(info['interfaces'], [rows[0]])
        self.network.start_run(port=8443)
        with mock.patch('fruit_app.local_network.subprocess.run', side_effect=subprocess.TimeoutExpired('powershell', 5)):
            self.assertEqual(self.client.get('/api/network/').json()['reason'], 'network_inventory_failed')

    def test_serving_process_resets_selection_and_cleans_up_monitor(self):
        from django.core.management import get_commands, call_command
        from .management.commands.runsslserver import Command, SSLCommand
        self.assertEqual(get_commands()['runsslserver'], 'fruit_app')
        with mock.patch('fruit_app.local_network.threading.Thread') as thread:
            local_network.LocalNetwork(self.inventory)
            call_command('check', verbosity=0)
            thread.assert_not_called()
        self.select()
        previous = self.client.get('/api/network/').json()['server_run_id']
        run_ids = []
        def serve(*args, **kwargs):
            info = self.client.get('/api/network/').json()
            self.assertFalse(info['selected'])
            run_ids.append(info['server_run_id'])
            self.assertEqual(self.select().status_code, 200)
            self.assertEqual(self.client.get('/api/network/').json()['camera_url'], 'https://192.168.50.10:9443/camera/')
        command = Command()
        command.port = '9443'
        with mock.patch.object(SSLCommand, 'inner_run', side_effect=serve):
            command.inner_run()
            command.inner_run()
        self.assertEqual(len(set([previous, *run_ids])), 3)
        self.assertFalse(self.client.get('/api/network/').json()['selected'])
        import threading
        self.assertFalse(any(thread.name == 'network-inventory' for thread in threading.enumerate()))

    def test_loopback_does_not_allow_a_foreign_browser_origin(self):
        self.assertEqual(self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.10'}, HTTP_ORIGIN='https://other.example').json()['reason'], 'network_origin_mismatch')

    def test_diagnostic_round_trip_is_scoped_and_never_consumes_motor_command(self):
        self.select()
        info = self.client.get('/api/network/info/').json()
        identity = {'boot_id': 'esp32-test-1', 'server_run_id': info['server_run_id'],
                    'network_revision': info['network_revision'], 'discovery_source': 'mdns'}
        device = {'secure': True, 'HTTP_HOST': '192.168.50.10:8443', 'REMOTE_ADDR': '192.168.50.20'}
        existing.views.APP_STATE['motor_command'] = {'command': 'feed_one', 'command_id': 99}
        response = self.client.get('/api/esp32/command/', {'diagnostic': '1', **identity}, **device)
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn('command=none\n', text)
        self.assertIn('control_allowed=0\n', text)
        diagnostic_id = next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith('diagnostic_id='))
        report = {'event': 'network_probe', **identity, 'diagnostic_id': diagnostic_id}
        for _ in range(2):
            self.assertEqual(self.post('/api/esp32/report/', report, **device).status_code, 200)
        network = self.client.get('/api/network/').json()
        self.assertTrue(network['esp32']['online'])
        self.assertEqual(network['esp32']['source'], 'mdns')
        self.assertEqual(existing.views.APP_STATE['motor_command'], {'command': 'feed_one', 'command_id': 99})
        self.assertIsNone(existing.views.APP_STATE['last_esp32_poll_monotonic'])
        self.assertEqual(self.client.get('/api/esp32/command/', **device).json()['reason'], 'network_capability_missing')
        self.assertEqual(self.post('/api/esp32/report/', {**report, 'diagnostic_id': 'wrong'}, **device).json()['reason'], 'network_diagnostic_mismatch')
        self.assertEqual(self.client.get('/api/esp32/command/', {'diagnostic': '1', **identity, 'network_revision': 0}, **device).json()['reason'], 'network_identity_mismatch')
        self.assertEqual(self.post('/api/esp32/report/', report, **{**device, 'REMOTE_ADDR': '192.168.51.20'}).json()['reason'], 'network_subnet_mismatch')
        with mock.patch('fruit_app.local_network.time.monotonic', return_value=existing.views.time.monotonic() + 7):
            self.assertFalse(self.client.get('/api/network/').json()['esp32']['online'])

    def test_discovery_monitor_registers_selected_ipv4_and_cleans_up_on_reselection(self):
        import time
        self.select()
        self.network.start_monitor()
        for _ in range(100):
            if self.network.snapshot()['discovery']['status'] == 'advertising':
                break
            time.sleep(0.01)
        self.assertEqual(len(self.publishers), 1)
        args, old = self.publishers[0]
        self.assertEqual(args[0], self.interfaces[0])
        self.assertEqual(args[1], 8443)
        self.assertEqual(args[3], 1)
        self.network.stop_monitor()
        old.close.assert_called_once()
        self.select()
        self.network.start_monitor()
        for _ in range(100):
            if len(self.publishers) == 2:
                break
            time.sleep(0.01)
        self.assertEqual(len(self.publishers), 2)
        self.network.start_run(port=8443)
        self.publishers[1][1].close.assert_called_once()
        self.assertFalse(self.network.snapshot()['selected'])

    def test_discovery_registration_failure_keeps_https_origin_and_manual_diagnostic(self):
        import time
        self.publisher_factory.side_effect = OSError('adapter failed')
        self.select()
        self.network.start_monitor()
        for _ in range(100):
            if self.network.snapshot()['discovery']['status'] == 'unavailable':
                break
            time.sleep(0.01)
        info = self.client.get('/api/network/').json()
        self.assertEqual(info['discovery']['reason'], 'network_discovery_registration_failed')
        self.assertEqual(info['server_url'], 'https://192.168.50.10:8443')
        response = self.client.get('/api/esp32/command/', {
            'diagnostic': '1', 'boot_id': 'manual-test', 'server_run_id': info['server_run_id'],
            'network_revision': info['network_revision'], 'discovery_source': 'manual',
            'discovery_reason': 'controller_not_found',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'control_allowed=0', response.content)

    def test_network_info_text_is_identity_checked_and_has_no_external_urls(self):
        self.select()
        response = self.client.get('/api/network/info/?format=text')
        self.assertIn(b'api_version=1\n', response.content)
        self.assertIn(b'selected=1\n', response.content)
        self.assertIn(b'server_url=https://192.168.50.10:8443\n', response.content)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.get('/api/network/info/', secure=True,
                                        HTTP_HOST='192.168.50.11:8443', REMOTE_ADDR='192.168.50.20').json()['reason'], 'network_origin_mismatch')

    def test_controller_conflict_is_visible_without_marking_device_online(self):
        info = self.select().json()
        observation = {'event': 'network_discovery_status', 'boot_id': 'conflict-device',
                       'server_run_id': info['server_run_id'], 'network_revision': info['network_revision'],
                       'discovery_reason': 'multiple_controllers_found'}
        response = self.post('/api/esp32/report/', observation)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'control_allowed=0\n', response.content)
        device = self.client.get('/api/network/').json()['esp32']
        self.assertFalse(device['online'])
        self.assertEqual(device['status'], 'discovery_conflict')
        self.assertEqual(device['reason'], 'multiple_controllers_found')
        self.assertEqual(self.post('/api/esp32/report/', {**observation, 'discovery_reason': 'unknown'}).json()['reason'], 'network_diagnostic_invalid')

    def test_cleanup_and_settings_enforce_selected_network_scope(self):
        routes = ('/api/discard/', '/api/reset_dataset/', '/api/set_counter/',
                  '/api/capture_timing/', '/api/auto_run/')
        self.select()
        for route in routes:
            for remote, host, reason in (
                ('192.168.60.20', '192.168.60.10:8443', 'network_origin_mismatch'),
                ('192.168.60.20', '192.168.50.10:8443', 'network_subnet_mismatch'),
            ):
                with self.subTest(route=route, reason=reason):
                    response = self.post(route, {'enabled': False}, secure=True,
                                         HTTP_HOST=host, REMOTE_ADDR=remote)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()['reason'], reason)
        self.network.start_run(port=8443)
        self.assertEqual(self.post('/api/auto_run/', {'enabled': False}).status_code, 200)
        self.assertEqual(self.post('/api/set_counter/', {'start_id': 2}).status_code, 200)
        self.assertEqual(self.post('/api/reset_dataset/').status_code, 200)
        for route in routes:
            self.assertEqual(self.post(route, HTTP_ORIGIN='https://other.example').json()['reason'], 'network_origin_mismatch')
            self.assertEqual(self.post(route, REMOTE_ADDR='192.168.50.20').json()['reason'], 'network_local_dashboard_required')

    def test_late_inventory_cannot_invalidate_a_new_confirmation(self):
        import threading
        entered = threading.Event()
        release = threading.Event()
        old_rows = self.interfaces.copy()
        def inventory():
            if threading.current_thread().name == 'network-inventory':
                entered.set()
                release.wait(timeout=2)
                self.network._stop.set()
                return old_rows
            return self.interfaces.copy()
        self.inventory.side_effect = inventory
        self.network.start_monitor()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.interfaces = [{**self.interfaces[0], 'ipv4': '192.168.50.11'}]
            result = self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.168.50.11'})
            self.assertEqual(result.status_code, 200)
            release.set()
            self.network._worker.join(timeout=2)
            self.assertFalse(self.network._worker.is_alive())
            info = self.client.get('/api/network/').json()
            self.assertTrue(info['selected'])
            self.assertEqual(info['camera_url'], 'https://192.168.50.11:8443/camera/')
            self.assertEqual(info['network_revision'], 1)
        finally:
            release.set()
            self.network.stop_monitor()

    def test_background_inventory_does_not_block_camera_http_and_invalidates_selection(self):
        import threading
        entered = threading.Event()
        release = threading.Event()
        self.select()
        def inventory():
            entered.set()
            release.wait(timeout=2)
            return []
        self.inventory.side_effect = inventory
        self.network.start_monitor()
        try:
            self.assertTrue(entered.wait(timeout=1))
            # HTTP must finish while the OS adapter is still waiting.
            self.assertEqual(self.client.get('/api/camera/state/').status_code, 200)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            self.network.stop_monitor()
        self.assertEqual(self.client.get('/api/network/').json()['reason'], 'no_connected_network')
        self.assertEqual(self.post('/api/photo_capture/').json()['reason'], 'no_connected_network')
