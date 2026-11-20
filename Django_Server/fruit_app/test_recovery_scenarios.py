"""Any fault sequence around a network lock must stay recoverable by the documented manual steps.

FakeESP32 models only the firmware rules the recovery depends on (CaptureController.cpp):
generation and interlock persist in NVS across reboot, a new boot_id per boot, a larger
server generation with control_allowed=0 locks the board, and recover_network ends in
network_recovery_completed then network_recovery_committed. The real C++ controller runs
the same handshake against Django in test_network_firmware.py.
"""

import itertools

from django.test import SimpleTestCase

from . import local_network, views
from . import test_network_safety as safety

FAULTS = ('esp32_reboot', 'django_restart')
STEPS = ('locked', 'confirmed', 'completed')
MAX_MANUAL_ATTEMPTS = 3


class FakeESP32:
    def __init__(self, test):
        self.test = test
        self.boots = 0
        self.boot_id = 'board-0'
        self.generation = views.APP_STATE.get('recovery_generation', 0)
        self.interlocked = False

    def params(self, **fields):
        return self.test.device(boot_id=self.boot_id, recovery_generation=self.generation,
                                network_interlocked='1' if self.interlocked else '0', **fields)

    def follow(self, payload):
        returned = payload.get('recovery_generation')
        if not isinstance(returned, int) or returned < self.generation:
            return payload
        if payload.get('control_allowed') is False and returned != self.generation:
            self.interlocked = True
        self.generation = returned
        return payload

    def poll(self):
        return self.follow(self.test.client.get('/api/esp32/command/', self.params()).json())

    def report(self, event, command_id, *, lose_reply=False):
        payload = self.test.client.post('/api/esp32/report/', self.params(event=event, command_id=command_id)).json()
        return {} if lose_reply else self.follow(payload)

    def reboot(self):
        self.boots += 1
        self.boot_id = f'board-{self.boots}'


class FakePhone:
    def __init__(self, test):
        self.test = test

    def ready(self):
        self.test.client.get('/api/camera/state/?camera_ready=1')


class RecoveryScenarioTests(SimpleTestCase):
    setUp = safety.NetworkSafetyTests.setUp
    tearDown = safety.NetworkSafetyTests.tearDown
    post = safety.NetworkSafetyTests.post
    select = safety.NetworkSafetyTests.select
    device = safety.NetworkSafetyTests.device

    def restart_django(self):
        views.reset_runtime_state()
        views._ensure_dataset_structure()
        local_network.NETWORK.clear_selection()
        self.assertEqual(self.select().status_code, 200)

    def inject(self, fault, board):
        if fault == 'esp32_reboot':
            board.reboot()
            board.poll()
        elif fault == 'django_restart':
            self.restart_django()

    def assert_no_motion_while_locked(self):
        if views.APP_STATE.get('network_recovery'):
            command = views.APP_STATE.get('motor_command') or {}
            self.assertNotIn(command.get('command'), ('feed_one', 'classify_fruit'))

    def manual_recovery(self, board, phone, faults):
        """Dashboard confirm, then the board's homing handshake; faults fire at their step."""
        phone.ready()
        self.inject(faults.get('locked'), board)
        board.poll()  # The real board polls continuously; the first answer may only realign its generation.
        board.poll()
        if not views.APP_STATE.get('network_recovery'):
            return
        if self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}).status_code != 200:
            return
        self.inject(faults.get('confirmed'), board)
        command = board.poll()
        self.assert_no_motion_while_locked()
        if command.get('command') != 'recover_network':
            return
        board.interlocked = True
        if not board.report('network_recovery_completed', command['command_id']).get('recovery_commit'):
            return
        self.inject(faults.get('completed'), board)
        lose = faults.get('reply') == 'reply_lost'
        answer = board.report('network_recovery_committed', command['command_id'], lose_reply=lose)
        if lose:
            answer = board.report('network_recovery_committed', command['command_id'])
        if answer.get('recovery_committed'):
            board.interlocked = False

    def run_scenario(self, faults):
        self.assertEqual(self.select().status_code, 200)
        board, phone = FakeESP32(self), FakePhone(self)
        board.poll()
        phone.ready()
        views.APP_STATE['auto_run_enabled'] = True
        views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6  # Laptop dropped the hotspot.
        self.client.get('/api/state/')
        board.interlocked = True  # The board's own link-loss guard fired too.
        self.assertTrue(views.APP_STATE.get('network_recovery'))

        for attempt in range(MAX_MANUAL_ATTEMPTS):
            self.manual_recovery(board, phone, faults if attempt == 0 else {})
            self.assert_no_motion_while_locked()
            if not views.APP_STATE.get('network_recovery') and not board.interlocked:
                break
        self.assertIsNone(views.APP_STATE.get('network_recovery'), 'Django still locked')
        self.assertFalse(board.interlocked, 'ESP32 still locked')
        self.assertTrue(board.poll()['control_allowed'])

    def scenarios(self, faults_list):
        for index, faults in enumerate(faults_list):
            if index:  # A fresh Django, dataset and network per scenario; the runner tears down the last.
                self.tearDown()
                self.setUp()
            with self.subTest(**faults):
                self.run_scenario(faults)

    def test_every_single_fault_at_every_step_is_recoverable(self):
        self.scenarios([{step: fault} for step, fault in itertools.product(STEPS, FAULTS)])

    def test_two_faults_at_different_steps_are_recoverable(self):
        pairs = itertools.combinations(itertools.product(STEPS, FAULTS), 2)
        self.scenarios([{a[0]: a[1], b[0]: b[1]} for a, b in pairs if a[0] != b[0]])

    def lab_incident(self):
        self.assertEqual(self.select().status_code, 200)
        board, phone = FakeESP32(self), FakePhone(self)
        board.poll()
        phone.ready()
        views.APP_STATE.update(sorter_command_id=6, sorter_fruit_id='fruit_001', sorter_label='加工',
                               sorter_classification_code=views.CLASSIFICATION_CODES['加工'])
        views._write_sorter_recovery_marker(phase='running')
        board.interlocked = True  # Its command poll failed mid-operation.
        return board, phone

    def test_lab_incident_confirm_recovery_also_recovers(self):
        """The Dashboard 確認安全復原 handshake, not 開始拍攝, is the normal way out of the same state."""
        board, phone = self.lab_incident()
        self.restart_django()
        for _ in range(MAX_MANUAL_ATTEMPTS):
            self.manual_recovery(board, phone, {})
            if not views.APP_STATE.get('network_recovery') and not board.interlocked:
                break
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertFalse(board.interlocked)
        self.assertTrue(board.poll()['control_allowed'])
        self.assertFalse(views.APP_STATE['sorter_recovery_required'])
        # Only the ordinary boot handshake (timing ack) may remain; FakeESP32 does not send it.
        self.assertIn(self.client.get('/api/state/').json()['auto_run_disabled_reason'], (None, 'feeder_timing_not_applied'))

    def test_lab_incident_sorter_pending_when_django_network_dropped(self):
        """2026-11-18: graded 加工 at Gate 3, Django's interface selection dropped before the
        board fetched classify_fruit, the board locked itself, and neither a Django restart nor
        runtime reset helped. Holding BOOT 10 s (ADR-0025) then runtime reset must recover."""
        board, _phone = self.lab_incident()
        for _ in range(2):  # Every restart reloads the same fruit and blocks the start.
            self.restart_django()
            board.poll()
            state = self.client.get('/api/state/').json()
            self.assertTrue(views.APP_STATE['sorter_recovery_required'])
            self.assertIsNotNone(state['auto_run_disabled_reason'])

        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        self.assertEqual(self.select().status_code, 200)
        board.poll()
        self.assertFalse(views.APP_STATE['sorter_recovery_required'])
        self.assertIsNotNone(views.APP_STATE.get('network_recovery'), 'A still-locked board relocks Django')

        board.interlocked = False  # BOOT held 10 s: NVS lock cleared, generation kept.
        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        self.assertEqual(self.select().status_code, 200)
        self.assertTrue(board.poll()['control_allowed'])
        self.assertIsNone(views.APP_STATE.get('network_recovery'))
        self.assertFalse(views.APP_STATE['sorter_recovery_required'])

    def test_lost_final_reply_is_recoverable(self):
        self.scenarios([{'reply': 'reply_lost'}, {'reply': 'reply_lost', 'completed': 'django_restart'}])
