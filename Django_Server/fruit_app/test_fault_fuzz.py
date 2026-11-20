"""Random fault sequences around a network lock; every failure is shrunk to a minimal reproduction.

Properties checked after every step and at the end:
1. While Django holds a network lock it never hands out feed_one or classify_fruit.
2. No request is answered with a 5xx.
3. Afterwards the Dashboard 確認安全復原 handshake unlocks both sides within 3 attempts
   (strict); if it cannot, BOOT held 10 s + runtime reset (ADR-0025) must (escape hatch).

FAULT_FUZZ_RUNS / FAULT_FUZZ_SEED / FAULT_FUZZ_STEPS widen a local or scheduled run;
CI uses the defaults below. A failure message names the seed and the shrunk step list.
"""

import os
import random
from unittest import mock

from django.test import SimpleTestCase

from . import local_network, network_recovery, views
from . import test_recovery_scenarios as scenarios

RUNS = int(os.environ.get('FAULT_FUZZ_RUNS', '150'))
SEED = int(os.environ.get('FAULT_FUZZ_SEED', '20261120'))
STEPS = int(os.environ.get('FAULT_FUZZ_STEPS', '10'))
ACTIONS = (
    'board_poll', 'board_poll_reply_lost', 'phone_ready', 'esp32_reboot', 'django_restart',
    'ip_change', 'heartbeat_expire', 'dashboard_confirm', 'board_runs_command',
    'board_runs_command_reply_lost', 'resend_last_report', 'runtime_reset', 'boot_hold',
)


class PropertyViolation(AssertionError):
    pass


class FuzzBoard(scenarios.FakeESP32):
    def __init__(self, test):
        super().__init__(test)
        self.last_command = None
        self.last_report = None

    def poll(self, *, lose_reply=False):
        response = self.test.client.get('/api/esp32/command/', self.params())
        self.test.check_response(response)
        payload = response.json()
        if lose_reply:
            return payload
        self.follow(payload)
        if payload.get('command') == 'recover_network':
            self.last_command = payload['command_id']
        return payload

    def report(self, event, command_id, *, lose_reply=False):
        self.last_report = (event, command_id)
        response = self.test.client.post('/api/esp32/report/', self.params(event=event, command_id=command_id))
        self.test.check_response(response)
        return {} if lose_reply else self.follow(response.json())

    def run_command(self, *, lose_reply=False):
        """The real board homes, reports completed, then committed once Django allows the commit."""
        if self.last_command is None:
            return
        self.interlocked = True
        answer = self.report('network_recovery_completed', self.last_command)
        if answer.get('recovery_commit'):
            final = self.report('network_recovery_committed', self.last_command, lose_reply=lose_reply)
            if final.get('recovery_committed'):
                self.interlocked = False
                self.last_command = None


class FaultFuzzTests(SimpleTestCase):
    setUp = scenarios.RecoveryScenarioTests.setUp
    tearDown = scenarios.RecoveryScenarioTests.tearDown
    post = scenarios.RecoveryScenarioTests.post
    device = scenarios.RecoveryScenarioTests.device

    def select(self):
        # The operator picks whatever address the Dashboard lists now.
        return self.post('/api/network/', {'interface_id': '7', 'ipv4': self.interfaces[0]['ipv4']})

    def restart_django(self):
        views.reset_runtime_state()
        views._ensure_dataset_structure()
        local_network.NETWORK.clear_selection()
        self.check_response(self.select())  # May be refused mid-handshake; the operator retries later.

    def check_response(self, response):
        if response.status_code >= 500:
            raise PropertyViolation(f'{response.status_code} from server: {response.content[:200]!r}')

    def check_no_motion_while_locked(self):
        if views.APP_STATE.get('network_recovery'):
            command = (views.APP_STATE.get('motor_command') or {}).get('command')
            if command in ('feed_one', 'classify_fruit'):
                raise PropertyViolation(f'{command} issued while network-locked')

    def unlocked(self, board):
        return not views.APP_STATE.get('network_recovery') and not board.interlocked

    def act(self, action, board, phone):
        if action == 'board_poll':
            board.poll()
        elif action == 'board_poll_reply_lost':
            board.poll(lose_reply=True)
        elif action == 'phone_ready':
            phone.ready()
        elif action == 'esp32_reboot':
            board.reboot()
        elif action == 'django_restart':
            self.restart_django()
        elif action == 'ip_change':
            octet = int(self.interfaces[0]['ipv4'].rsplit('.', 1)[1]) % 200 + 11
            self.interfaces[0]['ipv4'] = f'192.168.50.{octet}'
            self.network._checked_at = None
            self.client.get('/api/state/')
            self.check_response(self.select())
        elif action == 'heartbeat_expire':
            views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
            self.client.get('/api/state/')
        elif action == 'dashboard_confirm':
            self.check_response(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}))
        elif action == 'board_runs_command':
            board.run_command()
        elif action == 'board_runs_command_reply_lost':
            board.run_command(lose_reply=True)
        elif action == 'resend_last_report' and board.last_report:
            board.report(*board.last_report)
        elif action == 'runtime_reset':
            self.check_response(self.client.post('/api/runtime/reset/'))
            self.check_response(self.select())
        elif action == 'boot_hold':
            board.poll()  # The 10 s hold spans dozens of polls: the first may only sync the generation,
            board.poll()  # the next reports the board locked at it.
            board.interlocked = False  # ADR-0025: NVS lock cleared, generation kept.

    def confirm_recovery(self, board, phone):
        for _ in range(scenarios.MAX_MANUAL_ATTEMPTS):
            if self.unlocked(board):
                return True
            self.check_response(self.select())  # The operator re-picks the network when asked.
            phone.ready()
            board.poll()
            board.poll()
            if views.APP_STATE.get('network_recovery'):
                self.check_response(self.post('/api/auto_run/', {'enabled': True, 'recovery_confirmed': True}))
            board.poll()
            board.run_command()
            board.poll()
            self.check_no_motion_while_locked()
        return self.unlocked(board)

    def escape_hatch(self, board):
        for _ in range(2):  # ADR-0025 allows one repeat when Django's generation lagged.
            board.interlocked = False
            self.check_response(self.client.post('/api/runtime/reset/'))
            self.check_response(self.select())
            board.poll()
            board.poll()
            if self.unlocked(board):
                return True
        return False

    def run_sequence(self, actions):
        """Return None when every property holds, else a short failure description."""
        self.tearDown()
        self.setUp()
        try:
            self.assertEqual(self.select().status_code, 200)
            board, phone = FuzzBoard(self), scenarios.FakePhone(self)
            board.poll()
            phone.ready()
            views.APP_STATE['auto_run_enabled'] = True
            views.APP_STATE['last_esp32_poll_monotonic'] = views.time.monotonic() - 6
            self.client.get('/api/state/')
            board.interlocked = True
            for action in actions:
                self.act(action, board, phone)
                self.check_no_motion_while_locked()
            if self.confirm_recovery(board, phone):
                return None
            snapshot = (views.APP_STATE.get('network_recovery') or {}).get('reason'), board.interlocked
            if self.escape_hatch(board):
                return f'confirm path stuck (django_lock, board_locked)={snapshot}; escape hatch recovered'
            return f'UNRECOVERABLE (django_lock, board_locked)={snapshot}'
        except PropertyViolation as exc:
            return f'property: {exc}'

    def shrink(self, actions, failure):
        """Drop steps one at a time while the same kind of failure persists."""
        kind = failure.split(':', 1)[0].split(' (', 1)[0]
        changed = True
        while changed:
            changed = False
            for index in range(len(actions)):
                candidate = actions[:index] + actions[index + 1:]
                result = self.run_sequence(candidate)
                if result and result.split(':', 1)[0].split(' (', 1)[0] == kind:
                    actions, failure, changed = candidate, result, True
                    break
        return actions, failure

    def test_random_fault_sequences_stay_recoverable(self):
        rng = random.Random(SEED)
        failures = []
        for run in range(RUNS):
            actions = [rng.choice(ACTIONS) for _ in range(rng.randint(1, STEPS))]
            failure = self.run_sequence(actions)
            if failure:
                minimal, failure = self.shrink(actions, failure)
                failures.append(f'seed={SEED} run={run}: {failure}\n    steps={minimal}')
                if len(failures) >= 5:
                    break
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))

    def test_fuzzer_catches_a_recovery_that_never_commits(self):
        """The referee must bite: with the commit step broken, some sequence has to fail."""
        rng = random.Random(SEED)
        with mock.patch.object(network_recovery.Policy, 'finish', lambda *args, **kwargs: None):
            found = any(self.run_sequence([rng.choice(ACTIONS) for _ in range(rng.randint(1, STEPS))])
                        for _ in range(20))
        self.assertTrue(found)
