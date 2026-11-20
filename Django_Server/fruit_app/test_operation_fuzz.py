"""Random faults injected while fruit is being fed, photographed, graded and sorted.

OperatingBoard follows the firmware command protocol (feed_one, start_sequence,
release_gate, classify_fruit, recover_network) through the network layer, and
OperatingPhone uploads whenever the camera state requests a station. Properties,
checked after every step:
1. No feed_one or classify_fruit is handed out while Django holds a network lock.
2. No 5xx reply.
3. A physical action is never re-issued under a new command ID for the same fruit
   (classify_fruit) and never while another fruit is still in the machine (feed_one).
At the end the operator's documented steps (discard the round, 確認安全復原, and the
BOOT-hold escape hatch if needed) must return the machine to a state that feeds again.

FAULT_FUZZ_RUNS / FAULT_FUZZ_SEED / FAULT_FUZZ_STEPS widen the run as in test_fault_fuzz.
"""

import os
import random
from unittest import mock

from django.test import SimpleTestCase

from . import local_network, views
from . import test_detection as detection_tests
from . import test_fault_fuzz as fuzz
from . import test_recovery_scenarios as scenarios

RUNS = int(os.environ.get('FAULT_FUZZ_RUNS', '60'))
SEED = int(os.environ.get('FAULT_FUZZ_SEED', '20261121'))
STEPS = int(os.environ.get('FAULT_FUZZ_STEPS', '25'))
WORK = ('board_tick', 'board_tick', 'board_tick', 'phone_tick', 'phone_tick', 'wait_detection')
FAULTS = (
    'board_poll_reply_lost', 'esp32_reboot', 'django_restart', 'ip_change', 'heartbeat_expire',
    'report_reply_lost', 'resend_last_report', 'phone_offline', 'dashboard_confirm',
    'dashboard_discard', 'runtime_reset', 'boot_hold',
)
FAULTS = tuple(fault for fault in FAULTS if fault not in os.environ.get('FAULT_FUZZ_EXCLUDE', '').split(','))
PropertyViolation = fuzz.PropertyViolation


class OperatingBoard(fuzz.FuzzBoard):
    def __init__(self, test):
        super().__init__(test)
        self.done = {}  # command_id -> the report it produced, re-sent on a repeated command
        self.timing_revision = None
        self.lose_next_reply = False

    def send(self, event, **fields):
        lose, self.lose_next_reply = self.lose_next_reply, False
        command_id = fields.pop('command_id', 0)
        return self.report(event, command_id, lose_reply=lose, **fields)

    def report(self, event, command_id, *, lose_reply=False, **fields):
        self.last_report = (event, command_id, fields)
        response = self.test.client.post('/api/esp32/report/', self.params(event=event, command_id=command_id, **fields))
        self.test.check_response(response)
        return {} if lose_reply else self.follow(response.json())

    def resend(self):
        if self.last_report:
            event, command_id, fields = self.last_report
            self.report(event, command_id, **fields)

    def tick(self):
        payload = self.poll()
        self.test.observe_command(payload)
        if self.interlocked:
            if payload.get('command') == 'recover_network':
                self.run_command()
            return
        revision = payload.get('timing_revision')
        if revision and revision != self.timing_revision:
            self.timing_revision = revision
            self.send('timing_config_applied', timing_revision=revision)
        command, command_id = payload.get('command'), payload.get('command_id') or 0
        if command in (None, 'none') or command_id <= 0:
            return
        if command_id in self.done:  # Firmware dedupes by command ID and only re-reports.
            event, fields = self.done[command_id]
            self.send(event, command_id=command_id, **fields)
            return
        if command == 'feed_one':
            fields = {'feeder_elapsed_ms': 84, 'feeder_max_run_ms': payload.get('feeder_max_run_ms', 10000),
                      'feeder_stop_reason': 'hcsr04'}
            self.done[command_id] = ('feed_cycle_completed', fields)
            self.send('feed_cycle_completed', command_id=command_id, **fields)
            if payload.get('feed_context') != 'calibration':
                self.send('hcsr04_trigger')
        elif command in ('start_sequence', 'release_gate'):
            # release_gate N opens gate N, so the fruit settles at station N + 1.
            station = 1 if command == 'start_sequence' else (payload.get('station_index') or 1) + 1
            fields = {'station_index': station}
            self.done[command_id] = (f'station_{station}_ready', fields)
            self.send(f'station_{station}_ready', command_id=command_id, **fields)
        elif command == 'classify_fruit':
            fields = {'classification_code': payload.get('classification_code')}
            self.done[command_id] = ('classification_sorter_completed', fields)
            self.send('classification_sorter_completed', command_id=command_id, **fields)


class OperatingPhone(scenarios.FakePhone):
    def __init__(self, test):
        super().__init__(test)
        self.online = True

    def tick(self):
        if not self.online:
            return
        camera = self.test.client.get('/api/camera/state/?camera_ready=1').json()
        if not camera.get('capture_requested') or not camera.get('fruit_id'):
            return
        station = camera.get('station_index') or 1
        response = self.test._upload_station(camera['fruit_id'], camera['capture_token'], station)
        self.test.check_response(response)


class OperationFuzzTests(SimpleTestCase):
    _upload_station = detection_tests.DetectionFlowTests._upload_station
    _fake_image = detection_tests.DetectionFlowTests._fake_image
    _wait_for_detection = detection_tests.DetectionFlowTests._wait_for_detection
    post = fuzz.FaultFuzzTests.post
    device = fuzz.FaultFuzzTests.device
    select = fuzz.FaultFuzzTests.select
    restart_django = fuzz.FaultFuzzTests.restart_django
    check_response = fuzz.FaultFuzzTests.check_response

    def setUp(self):
        detection_tests.DetectionFlowTests.setUp(self)
        self.hardware_guard_patcher.stop()
        self.device_observer_patcher.stop()
        self.safety_monitor_patcher.stop()
        self.interfaces = [{'interface_id': '7', 'name': 'Wi-Fi', 'ipv4': '192.168.50.10', 'prefix': 24}]
        self.network = local_network.LocalNetwork(mock.Mock(side_effect=lambda: self.interfaces.copy()),
                                                  mock.Mock(side_effect=lambda *args: mock.Mock()))
        self.addCleanup(self.network.stop_monitor)
        self.network.start_run(port=8443)
        patcher = mock.patch.object(local_network, 'NETWORK', self.network)
        patcher.start()
        self.addCleanup(patcher.stop)
        models = mock.patch('fruit_app.detection.execute_model_batch',
                            side_effect=detection_tests.DetectionFlowTests._external_result)
        models.start()
        self.addCleanup(models.stop)
        self.classified = {}
        self.feeds = set()

    tearDown = detection_tests.DetectionFlowTests.tearDown

    def observe_command(self, payload):
        command, command_id, fruit = payload.get('command'), payload.get('command_id'), payload.get('fruit_id')
        if command == 'classify_fruit':
            first = self.classified.setdefault(fruit, command_id)
            if first != command_id:
                raise PropertyViolation(f'classify_fruit for {fruit} re-issued as #{command_id} after #{first}')
        if command == 'feed_one' and views.APP_STATE.get('active_fruit_id'):
            raise PropertyViolation(f"feed_one #{command_id} while {views.APP_STATE['active_fruit_id']} is in the machine")
        if command == 'feed_one' and payload.get('feed_context') != 'calibration':
            self.feeds.add(command_id)
        if command in ('feed_one', 'classify_fruit') and views.APP_STATE.get('network_recovery'):
            raise PropertyViolation(f'{command} handed out while network-locked')

    def start(self, board, phone):
        self.assertEqual(self.select().status_code, 200)
        self.assertEqual(self.post('/api/collection_options/', {
            'work_mode': 'detection', 'hardware_mode': 'hardware'}).status_code, 200)
        board.tick()
        phone.tick()
        self.check_response(self.post('/api/auto_run/', {'enabled': True}))

    def work(self, board, phone, rounds=12):
        for _ in range(rounds):
            board.tick()
            phone.tick()
            if views.APP_STATE.get('detection_status') in ('pending', 'running'):
                self._wait_for_detection()

    def fruits_done(self):
        return len(self.detection_rows())

    def detection_rows(self):
        summary = self.dataset_root / 'detection_summary.csv'
        if not summary.exists():
            return []
        return summary.read_text(encoding='utf-8-sig').splitlines()[1:]

    def test_happy_path_sorts_fruit_after_fruit(self):
        board, phone = OperatingBoard(self), OperatingPhone(self)
        self.start(board, phone)
        self.work(board, phone, rounds=40)
        self.assertGreaterEqual(self.fruits_done(), 2, views.APP_STATE.get('message'))

    # ---- Fault injection while operating -------------------------------------------------

    def operation_active(self):
        state = views.APP_STATE
        return bool(state.get('active_fruit_id') or (state.get('motor_command') or {}).get('command') not in (None, 'none')
                    or state.get('sorter_status') in ('pending', 'running'))

    def act(self, action, board, phone):
        if action == 'board_tick':
            board.tick()
        elif action == 'phone_tick':
            phone.online = True
            phone.tick()
        elif action == 'wait_detection':
            if views.APP_STATE.get('detection_status') in ('pending', 'running'):
                self._wait_for_detection()
        elif action == 'board_poll_reply_lost':
            board.poll(lose_reply=True)
        elif action == 'esp32_reboot':
            # The board saves its interlock in NVS when power drops mid-operation.
            board.interlocked = board.interlocked or self.operation_active()
            board.reboot()
            board.timing_revision = None
        elif action == 'django_restart':
            self.restart_django()
        elif action == 'ip_change':
            fuzz.FaultFuzzTests.act(self, 'ip_change', board, phone)
        elif action == 'heartbeat_expire':
            views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
            self.client.get('/api/state/')
            board.interlocked = board.interlocked or self.operation_active()  # Its own lease expired too.
        elif action == 'report_reply_lost':
            board.lose_next_reply = True
        elif action == 'resend_last_report':
            board.resend()
        elif action == 'phone_offline':
            phone.online = False
        elif action == 'dashboard_confirm':
            self.check_response(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}))
        elif action == 'dashboard_discard':
            self.discard()
        elif action == 'runtime_reset':
            self.check_response(self.client.post('/api/runtime/reset/'))
            self.check_response(self.select())
        elif action == 'boot_hold':
            board.poll()  # The 10 s hold spans dozens of polls: the first may only sync the generation,
            board.poll()  # the next reports the board locked at it.
            board.interlocked = False  # ADR-0025: NVS lock cleared, generation kept.

    def discard(self):
        state = self.client.get('/api/state/').json()
        if state.get('active_fruit_id'):
            self.check_response(self.post('/api/discard/', {
                'fruit_id': state['active_fruit_id'], 'capture_token': state.get('capture_token')}))

    def stuck(self, board):
        state = views.APP_STATE
        return bool(state.get('network_recovery') or board.interlocked or state.get('sorter_recovery_required')
                    or state.get('gate3_manual_removal_required') or state.get('auto_run_recovery_reason'))

    def operator_recovers(self, board, phone):
        """What CAPTURE_SPEC tells the operator: re-pick the network, discard the round, confirm."""
        for _ in range(3):
            phone.online = True
            self.check_response(self.select())
            self.work(board, phone, rounds=2)
            self.discard()
            self.check_response(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}))
            self.work(board, phone, rounds=6)
            if not self.stuck(board):
                return True
        return False

    def feeds_again(self, board, phone):
        before = len(self.feeds)
        self.check_response(self.post('/api/auto_run/', {'enabled': True}))
        self.work(board, phone, rounds=12)
        return len(self.feeds) > before

    def run_sequence(self, actions):
        self.tearDown()
        self.setUp()
        try:
            board, phone = OperatingBoard(self), OperatingPhone(self)
            self.start(board, phone)
            for action in actions:
                self.act(action, board, phone)
                self.check_locked_motion()
            if self.operator_recovers(board, phone) and self.feeds_again(board, phone):
                return None
            snapshot = self.snapshot(board)
            fuzz.FaultFuzzTests.escape_hatch(self, board)
            self.discard()
            self.check_response(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}))
            self.work(board, phone, rounds=6)
            if not self.stuck(board) and self.feeds_again(board, phone):
                return f'confirm path stuck {snapshot}; escape hatch recovered'
            return f'UNRECOVERABLE {snapshot} -> {self.snapshot(board)}'
        except PropertyViolation as exc:
            return f'property: {exc}'

    def snapshot(self, board):
        state = views.APP_STATE
        return {'status': state.get('status'), 'lock': (state.get('network_recovery') or {}).get('reason'),
                'board_locked': board.interlocked, 'sorter': state.get('sorter_recovery_required'),
                'gate3': state.get('gate3_manual_removal_required'), 'auto_run': state.get('auto_run_recovery_reason'),
                'blocked': self.client.get('/api/state/').json().get('auto_run_disabled_reason')}

    def check_locked_motion(self):
        fuzz.FaultFuzzTests.check_no_motion_while_locked(self)

    shrink = fuzz.FaultFuzzTests.shrink
    unlocked = fuzz.FaultFuzzTests.unlocked

    def test_random_faults_while_operating_stay_recoverable(self):
        rng = random.Random(SEED)
        failures = []
        for run in range(RUNS):
            actions = [rng.choice(WORK if rng.random() < 0.7 else FAULTS) for _ in range(rng.randint(3, STEPS))]
            failure = self.run_sequence(actions)
            if failure:
                minimal, failure = self.shrink(actions, failure)
                failures.append(f'seed={SEED} run={run}: {failure}\n    steps={minimal}')
                if len(failures) >= 5:
                    break
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))
