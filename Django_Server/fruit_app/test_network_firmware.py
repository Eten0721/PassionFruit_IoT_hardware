"""Real Django HTTP responses drive the production C++ controller through host I/O."""
import subprocess
import threading
from pathlib import Path
from unittest import skipUnless
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase
from . import dataset_store
from . import views
from . import test_network_safety as safety


HARNESS = Path(__file__).resolve().parents[2] / 'firmware/tests/build/network_http_test.exe'


@skipUnless(HARNESS.exists(), 'Build the firmware host harness first.')
class FirmwareHttpTests(SimpleTestCase):
    setUp = safety.NetworkSafetyTests.setUp
    tearDown = safety.NetworkSafetyTests.tearDown
    post = safety.NetworkSafetyTests.post
    _fake_image = safety.NetworkSafetyTests._fake_image
    _upload_station = safety.NetworkSafetyTests._upload_station

    def exercise(self, *, change_ip=False, provision=False):
        self.interfaces[0]['ipv4'] = '192.0.2.1'
        self.network.server_run_id = '12345678-1234-1234-1234-123456789abc'
        self.assertEqual(self.post('/api/network/', {'interface_id': '7', 'ipv4': '192.0.2.1'}).status_code, 200)
        stage = 'initial'
        events = []
        passed = False
        with subprocess.Popen([str(HARNESS), *(['--setup'] if provision else [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding='utf-8') as process:
            timeout = threading.Timer(25, process.kill)
            timeout.start()
            try:
                for line in process.stdout:
                    if line.strip() == 'PASS':
                        passed = True
                        continue
                    kind, method, url, body_hex = line.split()
                    self.assertEqual(kind, 'REQUEST')
                    parsed = urlsplit(url)
                    body = '' if body_hex == '-' else bytes.fromhex(body_hex).decode()
                    data = parse_qs(body if method == 'POST' else parsed.query)
                    event = data.get('event', [''])[0]
                    self.client.get('/api/camera/state/?camera_ready=1')
                    device = {'secure': True, 'HTTP_HOST': parsed.netloc, 'REMOTE_ADDR': '192.0.2.2'}
                    if method == 'GET':
                        response = self.client.get(parsed.path + '?' + parsed.query, **device)
                    else:
                        response = self.client.post(parsed.path, body, content_type='application/x-www-form-urlencoded', **device)
                    events.append((event or parsed.path, response.status_code, response.content.decode()[:400]))
                    instruction = 0
                    if event == 'timing_config_applied' and stage == 'initial':
                        result = self.post('/api/feeder/test/')
                        self.assertEqual(result.status_code, 200, result.content)
                        stage = 'calibration'
                    elif event == 'feed_cycle_completed' and stage == 'calibration':
                        self.assertEqual(response.status_code, 200, response.content)
                        result = self.post('/api/auto_run/', {'enabled': True})
                        self.assertEqual(result.status_code, 200, result.content)
                        stage, instruction = 'production', 1
                    elif event == 'hcsr04_station_1_ready' and stage == 'production':
                        self.assertEqual(response.status_code, 200, response.content)
                        fruit_id, token = views.APP_STATE['active_fruit_id'], views.APP_STATE['capture_token']
                        self.assertEqual(self._upload_station(fruit_id, token, 1).status_code, 200)
                        if change_ip:
                            self.interfaces[0]['ipv4'] = '192.0.2.3'
                            self.network._checked_at = None
                        else:
                            views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
                        state = self.client.get('/api/state/').json()
                        self.assertIsNotNone(state['network_recovery'])
                        self.assertNotEqual(token, state['capture_token'])
                        self.assertTrue((self.dataset_root / 'temp' / fruit_id / 'img_01.jpg').exists())
                        result = self.post('/api/discard/', {'fruit_id': fruit_id, 'capture_token': state['capture_token']})
                        self.assertEqual(result.status_code, 200, result.content)
                        stage, instruction = 'aborted', 3 if change_ip else 2
                    elif event == 'network_aborted' and stage == 'aborted' and response.status_code == 200:
                        result = self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True})
                        self.assertEqual(result.status_code, 200, result.content)
                        self.assertTrue(result.json()['recovery_pending'])
                        stage = 'recovering'
                    elif event == 'network_recovery_committed' and response.status_code == 200:
                        self.assertTrue(response.json()['recovery_committed'])
                        self.assertIsNone(views.APP_STATE['network_recovery'])
                        self.assertEqual(views.APP_STATE['motor_command']['command'], 'feed_one')
                        stage = 'committed'
                    process.stdin.write(f'{response.status_code} {response.content.hex() or "-"} {instruction}\n')
                    process.stdin.flush()
                self.assertEqual(process.wait(timeout=2), 0, f'{stage}: {events[-10:]} {process.stderr.read()}')
            finally:
                timeout.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
        self.assertTrue(passed, events)
        self.assertEqual(stage, 'committed')
        self.assertEqual(sum(event[0] == 'network_recovery_completed' for event in events), 1)
        self.assertEqual(sum(event[0] == 'network_recovery_committed' for event in events), 1)
        self.assertFalse((self.dataset_root / 'temp/fruit_001').exists())
        self.assertEqual(dataset_store.load_counter(views._dataset_root()), 1)

    def test_loss_cleanup_human_recovery_and_new_feed(self):
        self.exercise()

    def test_same_interface_ip_change_rejoins_before_human_recovery(self):
        self.exercise(change_ip=True)

    def test_ap_configuration_save_close_discovery_and_control_round_trip(self):
        self.exercise(provision=True)
