"""Single owner of network recovery markers, handshake and operation policy.

The runtime supplies existing capture/command effects; calls run under STATE_LOCK.
Device observations remain in memory and never enter the recovery marker.
"""
import json
import secrets
import time
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse

from . import local_network


class Policy:
    def __init__(self, runtime):
        self.runtime = runtime
        self.state = runtime.APP_STATE

    @property
    def locked(self):
        return bool(self.state.get('network_recovery'))

    @property
    def needs_confirmation(self):
        return self.locked and (self.kinds()['data_locked'] or self.state.get('hardware_mode') != 'photo_only')

    @property
    def handshake_active(self):
        return (self.state.get('network_recovery') or {}).get('phase') in ('homing', 'committing')

    def kinds(self, recovery=None):
        recovery = recovery if recovery is not None else self.state.get('network_recovery')
        if not recovery:
            return {'data_locked': False, 'motor_locked': False}
        # Legacy device-only interlocks carry no round; other legacy hardware losses
        # conservatively retain both locks, including an interrupted homing handshake.
        legacy_motor = (recovery.get('reason') == 'esp32_network_interlocked'
                        or recovery.get('hardware_mode', 'hardware') != 'photo_only')
        legacy_data = bool(recovery.get('fruit_id')) or recovery.get('reason') != 'esp32_network_interlocked'
        kinds = {
            'data_locked': recovery.get('data_locked', legacy_data) is not False,
            'motor_locked': recovery.get('motor_locked', legacy_motor) is not False,
        }
        if not any(kinds.values()):
            kinds['motor_locked'] = True
        return kinds

    def ignores_motor_lock(self, operation, target_mode=None):
        kinds = self.kinds()
        return (self.locked and not kinds['data_locked'] and kinds['motor_locked']
                and not self.handshake_active and not self.state.get('network_persist_failed')
                and (self.state.get('hardware_mode') == 'photo_only'
                     or operation == 'options' and target_mode == 'photo_only'))

    def path(self):
        return Path(settings.MOTOR_COMMAND_SEQUENCE_PATH).with_name('network_recovery.json')

    def reason(self):
        if self.state.get('network_persist_failed'):
            return 'recovery_persist_failed'
        recovery = self.state.get('network_recovery') or {}
        return recovery.get('reason', 'network_recovery_required')

    def persist(self):
        try:
            self.runtime._write_recovery_payload(self.path(), self.state['network_recovery'])
            self.state['network_persist_failed'] = False
            return True
        except OSError:
            self.state['network_persist_failed'] = True
            self.state['last_error_reason'] = 'recovery_persist_failed'
            return False

    def lock(self, reason, *, force=False):
        if not force and not self.runtime._network_operation_active():
            return
        recovery = self.state.get('network_recovery')
        previous_kinds = self.kinds()
        kinds = previous_kinds.copy()
        device_only = reason == 'esp32_network_interlocked' and self.state.get('hardware_mode') == 'photo_only'
        kinds['data_locked'] |= self.runtime._network_operation_active() and not device_only
        kinds['motor_locked'] |= self.state.get('hardware_mode') != 'photo_only' or reason == 'esp32_network_interlocked'
        if not any(kinds.values()):
            kinds['motor_locked'] = True
        if recovery and recovery.get('phase') == 'locked':
            new_data_lock = kinds['data_locked'] and not previous_kinds['data_locked']
            recovery.update(kinds)
            if new_data_lock:
                recovery['reason'] = reason
                self._abort(reason)
            if reason == 'recovery_persist_failed':
                recovery['reason'] = reason
                self.state['last_error_reason'] = reason
            if kinds != previous_kinds or reason == 'recovery_persist_failed':
                self.persist()
            return
        network = local_network.NETWORK.snapshot()
        command = self.state.get('motor_command') or {}
        self.state['recovery_generation'] = self.state.get('recovery_generation', 0) + 1
        self.state['network_recovery'] = {
            'generation': self.state['recovery_generation'], 'phase': 'locked', 'reason': reason,
            'fruit_id': self.state.get('active_fruit_id') or self.state.get('sorter_fruit_id'),
            'hardware_mode': self.state.get('session_hardware_mode') if self.state.get('active_fruit_id')
            else self.state.get('hardware_mode'),
            'boot_id': self.state.get('esp32_boot_id'),
            'server_run_id': network['server_run_id'], 'network_revision': network['network_revision'],
            'command_id': command.get('command_id'), 'command': command.get('command'),
            'resume_auto_run': reason != 'feeder_wait_cancelled' and (recovery or {}).get('resume_auto_run', True),
            **kinds,
        }
        if not device_only or kinds['data_locked']:
            self._abort(reason)
        self.persist()
        self.runtime._record_transition('network_interlocked', details={'reason': reason})

    def _abort(self, reason):
        self.runtime._disable_auto_run()
        self.state['auto_run_finishing'] = False
        self.state['pending_capture'] = False
        self.state['capture_token'] = secrets.randbits(48)
        self.state['motor_command'] = None
        self.state['gate3_waiting_boot_id'] = None
        self.state['auto_run_recovery_reason'] = reason
        self.state['last_error_reason'] = reason
        self.state['status'] = 'error'
        if self.runtime._sorter_busy():
            self.state['sorter_status'] = 'failed'
            self.state['sorter_deadline_monotonic'] = None
        self.runtime._clear_wait_timer()
        self.state['message'] = ('失聯安全鎖已啟用。請刪除或隔離本輪暫存資料，依斷電程序移除果實，'
                                '確認手已離開並重新加入網路後，再明確確認復原。')

    def check_safety(self):
        network = local_network.NETWORK.snapshot()
        identity = (network['server_run_id'], network['network_revision'])
        previous = self.state.get('network_identity')
        if previous is not None and identity != previous:
            self.lock('network_changed')
            self.state['camera_last_live_frame_monotonic'] = None
            self.state['camera_last_live_frame_at'] = None
            self.state['camera_diagnostics'] = None
            self.state['last_esp32_poll_monotonic'] = None
            self.state['esp32_network_capable'] = False
        self.state['network_identity'] = identity
        if not network['selected']:
            self.lock('network_selection_required')
        if not self.runtime._network_operation_active() and not self.state.get('network_recovery'):
            return
        now = time.monotonic()
        last_camera = self.state.get('camera_last_live_frame_monotonic')
        if last_camera is not None and now - last_camera >= 10 and not self.handshake_active:
            self.lock('camera_connection_lost', force=True)
        if self.state.get('hardware_mode') != 'photo_only':
            last_device = self.state.get('last_esp32_poll_monotonic')
            if last_device is not None and now - last_device >= 6:
                self.lock('esp32_connection_lost', force=True)
        recovery = self.state.get('network_recovery') or {}
        deadline = recovery.get('deadline')
        if deadline is not None and now >= deadline:
            self.lock('network_recovery_ack_timeout', force=True)

    def observe_device(self, data):
        self.check_safety()  # Reconnection cannot erase the expired heartbeat.
        if data.get('network_capability') != 'local_network_v1':
            return 'network_capability_missing'
        reported_generation = self.runtime._safe_int(data.get('recovery_generation'))
        current_generation = self.state.get('recovery_generation', 0)
        if (reported_generation is not None and reported_generation > current_generation
                and self.runtime._safe_bool(data.get('network_interlocked')) is True
                and self.runtime._device_identity_matches({**dict(data.items()), 'recovery_generation': current_generation})):
            # Recover a lost server marker conservatively; this never grants motion.
            self.lock('esp32_network_interlocked', force=True)
            self.state['recovery_generation'] = reported_generation
            self.state['network_recovery']['generation'] = reported_generation
            self.persist()
        if not self.runtime._device_identity_matches(data):
            return 'network_identity_mismatch'
        previous_boot = self.state.get('esp32_boot_id')
        restarted = (previous_boot and previous_boot != data['boot_id']
                     and self.state.get('hardware_mode') != 'photo_only'
                     and self.runtime._network_operation_active())
        if restarted:
            self.lock('esp32_restarted', force=True)
        self.state['esp32_boot_id'] = data['boot_id']
        self.state['esp32_network_capable'] = True
        self.state['last_esp32_poll_monotonic'] = time.monotonic()
        self.state['last_esp32_poll_at'] = self.runtime._now_string()
        self.state['esp32_interlocked'] = self.runtime._safe_bool(data.get('network_interlocked')) is True
        self.record_device_facts(data)
        self.state['esp32_motion'] = data.get('motion', 'unknown')
        self.state['esp32_setup_allowed'] = self.runtime._safe_bool(data.get('setup_allowed')) is True
        # Firmware omits the field after its setup AP opens successfully, which clears the failure.
        self.state['esp32_setup_failure'] = data.get('setup_failure') if data.get('setup_failure') in self.runtime.ESP32_SETUP_FAILURES else None
        block_reason = data.get('setup_block_reason')
        self.state['esp32_setup_block_reason'] = (
            block_reason if isinstance(block_reason, str) and self.runtime.ESP32_SETUP_BLOCK_REASON_PATTERN.fullmatch(block_reason) else None)
        self.state['esp32_setup_active'] = (
            data.get('motion') == 'setup' and self.state['esp32_setup_allowed']
            and self.runtime._network_setup_reason() is None
        )
        # The device has claimed (motion=setup) or refused (not safe-idle) a Dashboard setup request.
        if data.get('motion') == 'setup' or not self.state['esp32_setup_allowed']:
            self.state.pop('esp32_setup_request_deadline', None)
        receipt = self.state.get('network_recovery_receipt') or {}
        completing_receipt = (
            data.get('event') in ('network_recovery_completed', 'network_recovery_committed')
            and receipt == {'generation': self.runtime._safe_int(data.get('recovery_generation')),
                            'command_id': self.runtime._safe_int(data.get('command_id')), 'boot_id': data.get('boot_id')}
        )
        # Photo-only never commands motors, so the device's own NVS lock waits until hardware mode
        # is selected again; relocking here would undo every photo-only recovery on the next poll.
        if (self.state['esp32_interlocked'] and not completing_receipt and not self.kinds()['motor_locked']
                and (self.locked or self.state.get('hardware_mode') != 'photo_only')):
            self.lock('esp32_network_interlocked', force=True)
        return 'network_recovery_required' if restarted else None

    def record_device_facts(self, data):
        # Terminal reports omit unchanged facts; omission must not erase a valid poll.
        self.runtime._restore_timing_ack_for_boot(data.get('boot_id') or self.state.get('esp32_boot_id'))
        if 'capability' in data or 'sorter_capability' in data:
            self.state['esp32_feeder_capable'] = data.get('capability') == 'feeder_v1'
            self.state['esp32_sorter_capable'] = data.get('sorter_capability') == 'gate3_sorter_v1'
        if 'feeder_state' in data:
            feeder = (data.get('feeder_state') or '').strip()
            self.state['esp32_feeder_state'] = feeder if feeder in ('idle', 'awaiting_fruit') else None
        if 'feeder_sensor_state' in data:
            sensor = (data.get('feeder_sensor_state') or '').strip()
            self.state['esp32_feeder_sensor_state'] = sensor if sensor in ('clear', 'blocked', 'unavailable') else None

    def confirm_disabled_reason(self):
        if self.state.get('dataset_operation'):
            return 'dataset_operation_in_progress'
        if self.state.get('active_fruit_id') or self.runtime._has_unclassified_temp_fruit():
            return 'temp_fruit_exists'
        if self.state.get('motor_command'):
            return 'pending_motor_command'
        if not local_network.NETWORK.snapshot()['selected']:
            return 'network_selection_required'
        if self.state.get('hardware_mode') == 'photo_only':
            return None if self.runtime._camera_is_ready() else 'camera_not_ready'
        if not self.runtime._esp32_is_online():
            return 'esp32_offline'
        if not self.state.get('esp32_network_capable'):
            return 'network_capability_missing'
        return None

    def ui_reason(self):
        recovery = self.state.get('network_recovery')
        if not self.needs_confirmation:
            return 'network_recovery_not_required'
        if recovery.get('phase') in ('homing', 'committing'):
            return 'network_recovery_in_progress'
        return self.confirm_disabled_reason()

    def confirm(self, data):
        recovery = self.state['network_recovery']
        if data.get('recovery_confirmed') is not True:
            return self.runtime._json_error('請完成資料清理與人工安全復原後明確確認。', status=409,
                               reason='network_recovery_confirmation_required')
        reason = self.ui_reason()
        if reason:
            return self.runtime._json_error('請先完成復原條件。', status=409, reason=reason)
        if self.state.get('network_persist_failed') and not self.persist():
            return local_network.network_error('recovery_persist_failed', status=503)
        if self.state.get('hardware_mode') == 'photo_only' or not self.kinds()['motor_locked']:
            response = self.finish(data_only=True)
            if response is not None or self.state.get('hardware_mode') == 'photo_only':
                return response
            return JsonResponse(self.runtime._state_payload(extra={'ok': True}))
        try:
            command = self.runtime._set_motor_command('recover_network')
        except self.runtime.CaptureCommandError as exc:
            return self.runtime._json_error(exc.message, status=exc.status, reason=exc.reason)
        recovery.update(phase='homing', recovery_command_id=command['command_id'],
                        recovery_boot_id=self.state['esp32_boot_id'], deadline=time.monotonic() + 15)
        if not self.persist():
            self.state['motor_command'] = None
            recovery['phase'] = 'locked'
            return local_network.network_error('recovery_persist_failed', status=503)
        return JsonResponse(self.runtime._state_payload(extra={'ok': True, 'recovery_pending': True}))

    def finish(self, receipt=None, *, data_only=False):
        retained = None
        pause_trigger = not (self.state.get('network_recovery') or {}).get('resume_auto_run', True)
        if data_only and self.kinds()['motor_locked']:
            retained = {**self.state['network_recovery'], 'data_locked': False,
                        'motor_locked': True, 'phase': 'locked', 'deadline': None}
        try:
            if retained is None:
                self.runtime._clear_sorter_recovery_marker()
                self.runtime._clear_gate3_recovery_marker()
            self.runtime._write_recovery_payload(self.path(), retained or {
                'phase': 'clear', 'generation': self.state.get('recovery_generation', 0), 'receipt': receipt,
                'auto_trigger_paused': pause_trigger,
            })
        except OSError:
            self.state['network_persist_failed'] = True
            self.state['last_error_reason'] = 'recovery_persist_failed'
            return local_network.network_error('recovery_persist_failed', status=503)
        self.state['network_recovery'] = retained
        self.state['network_recovery_receipt'] = receipt
        self.state['network_persist_failed'] = False
        self.state['motor_command'] = None
        self.state['auto_run_recovery_reason'] = retained['reason'] if retained else None
        self.state['last_error_reason'] = retained['reason'] if retained else None
        self.state['status'] = 'idle'
        self.state['esp32_feeder_state'] = 'idle'
        self.state['auto_run_finishing'] = False
        if pause_trigger:
            self.state['auto_trigger_paused'] = True
        if receipt is not None:
            self.state['esp32_interlocked'] = False
        if retained is None:
            self.runtime._reset_sorter_state()
        return None

    def report(self, data):
        recovery = self.state.get('network_recovery') or {}
        event = data.get('event')
        receipt = self.state.get('network_recovery_receipt') or {}
        if not recovery and receipt == {'generation': self.runtime._safe_int(data.get('recovery_generation')),
                                        'command_id': self.runtime._safe_int(data.get('command_id')),
                                        'boot_id': data.get('boot_id')}:
            return JsonResponse({'ok': True, 'duplicate': True, 'recovery_committed': True, 'recovery_commit': True})
        if (not recovery or recovery.get('phase') not in ('homing', 'committing')
                or recovery.get('recovery_command_id') != self.runtime._safe_int(data.get('command_id'))
                or recovery.get('recovery_boot_id') != data.get('boot_id')):
            return local_network.network_error('network_recovery_ack_mismatch')
        if event == 'network_recovery_completed':
            recovery['phase'] = 'committing'
            if not self.persist():
                return local_network.network_error('recovery_persist_failed', status=503)
            return JsonResponse({'ok': True, 'recovery_commit': True})
        if event != 'network_recovery_committed' or recovery.get('phase') != 'committing':
            return local_network.network_error('network_recovery_ack_mismatch')
        receipt = {'generation': recovery['generation'], 'command_id': recovery['recovery_command_id'],
                   'boot_id': data['boot_id']}
        response = self.finish(receipt)
        if response is not None:
            return response
        if (recovery.get('resume_auto_run', True)
                and recovery.get('reason') != 'gate3_manual_removal_required'
                and self.runtime._auto_run_disabled_reason() is None):
            self.state['auto_run_enabled'] = True
            self.runtime._queue_production_feed()
        elif recovery.get('reason') == 'gate3_manual_removal_required':
            self.state['message'] = '本輪已跳過，ESP32 已完成歸位交握；請按「開始拍攝」開始下一顆。'
        elif not recovery.get('resume_auto_run', True):
            self.state['message'] = '送料等待已結束，可切換模式或網路；需要送料時請再按「開始拍攝」。'
        return JsonResponse({'ok': True, 'recovery_committed': True})

    def restore(self):
        path = self.path()
        if not path.exists() or self.locked:
            return
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(payload, dict) or type(payload.get('generation')) is not int
                    or payload['generation'] < 1):
                raise ValueError('invalid network recovery marker')
        except (OSError, ValueError):
            payload = {'generation': 1, 'reason': 'recovery_persist_failed', 'hardware_mode': 'hardware'}
            self.state['network_persist_failed'] = True
        if payload.get('phase') == 'clear':
            self.state['recovery_generation'] = payload['generation']
            self.state['network_recovery_receipt'] = payload.get('receipt')
            self.state['auto_trigger_paused'] = payload.get('auto_trigger_paused') is True
            return
        self.state['network_recovery'] = {**payload, **self.kinds(payload), 'phase': 'locked', 'deadline': None}
        self.state['recovery_generation'] = payload['generation']
        if 'motor_locked' not in payload:
            self.state['hardware_mode'] = payload.get('hardware_mode', 'hardware')
            self.state['session_hardware_mode'] = self.state['hardware_mode']
        self.state['auto_run_recovery_reason'] = payload.get('reason', 'network_recovery_required')
        self.state['status'] = 'error' if self.needs_confirmation else 'idle'
        self.state['message'] = '已恢復失聯安全鎖。請清理本輪資料、人工移除果實並明確確認復原。'

    def apply_payload(self, payload, network):
        if self.needs_confirmation:
            payload.update(can_recapture=False, can_classify=False, pending_capture=False,
                           capture_requested=False,
                           can_start_auto_run=self.runtime._recovery_can_confirm() and self.state['hardware_mode'] == 'hardware',
                           can_start_photo_capture=self.runtime._recovery_can_confirm() and self.state['hardware_mode'] == 'photo_only')
        network['selection_disabled_reason'] = self.disabled_reason('network')
        network['selection_allowed'] = network['selection_disabled_reason'] is None
        payload['network_recovery_required'] = self.needs_confirmation

    def disabled_reason(self, operation, *, target_mode=None):
        if operation == 'network':
            if self.handshake_active:
                return 'network_recovery_in_progress'
            if self.locked:
                return self.reason() if self.runtime._network_operation_active() else None
            return self.runtime._options_disabled_reason()
        if operation in ('device', 'observation', 'start', 'options_request', 'discard', 'notification',
                         'assistant', 'counter', 'reset'):
            return None
        if operation == 'motor' and self.state.get('hardware_mode') == 'photo_only':
            return 'hardware_mode_required'
        if not self.locked and not self.state.get('network_persist_failed'):
            if self.state.get('esp32_interlocked') and self.state.get('hardware_mode') != 'photo_only':
                return 'esp32_network_interlocked'
            return None
        if operation == 'setup':
            return 'network_recovery_required'
        if operation != 'network' and self.ignores_motor_lock(operation, target_mode):
            return None
        return self.reason()

    def command_payload(self):
        command = self.state.get('motor_command') or {}
        if self.state.get('hardware_mode') == 'photo_only':
            return {'command': 'none', 'command_id': 0}
        if command.get('command') == 'recover_network' and self.handshake_active:
            return command
        return {'command': 'none', 'command_id': 0}

    def control_allowed(self):
        return not (self.locked or self.state.get('network_persist_failed') or self.state.get('esp32_interlocked'))

