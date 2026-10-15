"""只測試純函式與模擬的程序／網路，不安裝或啟動任何服務。"""

import codecs
from http.client import BadStatusLine
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import launch


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(temporary.name).resolve()
        self.root.relative_to(launch.ROOT)
        self.addCleanup(temporary.cleanup)
        reporter = patch.object(launch, 'report')
        self.report = reporter.start()
        self.addCleanup(reporter.stop)


class RequirementsTests(unittest.TestCase):
    def test_comments_blanks_and_pins(self):
        text = '# index pins\n\n torch==2.11.0 \n torchvision==0.26.0 # comment\n'
        self.assertEqual(launch.parse_torch_requirements(text),
                         [('torch', '2.11.0'), ('torchvision', '0.26.0')])

    def test_malformed_and_empty_requirements_raise(self):
        for text in ('torch>=2.11.0', 'torch==2.11.0+cpu', 'torch==2.11.0 extra',
                     '-r other.txt', 'torch==', '# empty\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                launch.parse_torch_requirements(text)

    def test_specs_include_the_requested_variant(self):
        requirements = [('torch', '2.11.0'), ('torchvision', '0.26.0')]
        for variant in ('cu128', 'cpu'):
            self.assertEqual(launch.build_torch_specs(requirements, variant),
                             [f'torch==2.11.0+{variant}', f'torchvision==0.26.0+{variant}'])
        with self.assertRaises(ValueError):
            launch.build_torch_specs(requirements, 'unknown')

    def test_digest_is_stable_and_tracks_every_input(self):
        inputs = ['3.14.4', 'cpu', b'torch==2.11.0', b'Django==5.2.16']
        digest = launch.compute_digest(*inputs)
        self.assertEqual(digest, launch.compute_digest(*inputs))
        for index, changed in enumerate(('3.14.5', 'cu128', b'torch==2.12.0', b'Django==5.2.17')):
            alternative = inputs.copy()
            alternative[index] = changed
            with self.subTest(index=index):
                self.assertNotEqual(digest, launch.compute_digest(*alternative))

    def test_python_guard(self):
        self.assertTrue(launch.check_python((3, 14, 4))['ok'])
        self.assertEqual(launch.check_python((3, 13, 9))['reason'], 'python_version_mismatch')

    def test_gpu_detection_success_failure_timeout_and_absence(self):
        for code, expected in ((0, 'cu128'), (1, 'cpu')):
            runner = Mock(return_value=Mock(returncode=code))
            self.assertEqual(launch.detect_torch_variant(runner, lambda _: 'nvidia-smi'), expected)
            runner.assert_called_once_with(['nvidia-smi'], timeout=10, capture_output=True)
        for error in (OSError('missing'), subprocess.TimeoutExpired('nvidia-smi', 10)):
            self.assertEqual(launch.detect_torch_variant(Mock(side_effect=error), lambda _: 'gpu'), 'cpu')
        runner = Mock()
        self.assertEqual(launch.detect_torch_variant(runner, lambda _: None), 'cpu')
        runner.assert_not_called()


class StampTests(TemporaryDirectoryTest):
    def test_missing_corrupt_and_non_object_stamps_are_stale(self):
        path = self.root / 'stamp.json'
        self.assertFalse(launch.stamp_matches(path, 'digest'))
        for contents in ('broken JSON', '[]', 'null', '{}', '{"digest":"other"}'):
            path.write_text(contents, encoding='utf-8')
            with self.subTest(contents=contents):
                self.assertFalse(launch.stamp_matches(path, 'digest'))

    def test_stamp_round_trip_and_metadata(self):
        path = self.root / 'stamp.json'
        launch.write_stamp(path, 'digest', 'cpu', '3.14.4')
        self.assertTrue(launch.stamp_matches(path, 'digest'))
        self.assertFalse(launch.stamp_matches(path, 'other'))
        stamp = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(stamp['variant'], 'cpu')
        self.assertEqual(stamp['python'], '3.14.4')
        self.assertTrue(stamp['written_at'])
        self.assertFalse(path.with_suffix('.tmp').exists())


class DependencyTests(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        (self.root / 'requirements-torch.txt').write_text('torch==2.11.0\ntorchvision==0.26.0\n')
        (self.root / 'requirements.txt').write_text('Django==5.2.16\n')
        self.stamp = self.root / 'stamp.json'

    def install(self, runner, variant='cpu'):
        return launch.ensure_dependencies(variant, self.root, self.stamp, runner, '3.14.4')

    def test_success_writes_stamp_and_matching_stamp_skips_all_pip(self):
        runner = Mock(return_value=Mock(returncode=0))
        self.assertTrue(self.install(runner)['ok'])
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(runner.call_args_list[0].args[0],
                         [launch.sys.executable, '-s', '-m', 'pip', 'install',
                          'torch==2.11.0+cpu', 'torchvision==0.26.0+cpu',
                          '--index-url', launch.TORCH_INDEXES['cpu']])
        self.assertEqual(runner.call_args_list[1].args[0][-3:],
                         ['install', '-r', str(self.root / 'requirements.txt')])
        self.assertEqual(runner.call_args_list[2].args[0][-1], 'check')
        self.assertTrue(self.stamp.is_file())
        runner.reset_mock()
        self.assertTrue(self.install(runner)['ok'])
        runner.assert_not_called()
        self.report.assert_called_with('依賴已是最新。')

    def test_pip_check_failure_warns_and_still_writes_stamp(self):
        runner = Mock(side_effect=[Mock(returncode=0), Mock(returncode=0), Mock(returncode=1)])
        self.assertTrue(self.install(runner)['ok'])
        self.assertTrue(self.stamp.exists())
        self.assertIn('pip check', self.report.call_args.args[0])

    def test_each_pip_failure_never_writes_stamp_and_stops(self):
        reasons = ('torch_install_failed', 'requirements_install_failed')
        for index, reason in enumerate(reasons):
            with self.subTest(reason=reason):
                self.stamp.write_text('{"digest":"stale"}')
                runner = Mock(side_effect=[Mock(returncode=0)] * index + [Mock(returncode=1)])
                result = self.install(runner)
                self.assertEqual(result['reason'], reason)
                self.assertFalse(self.stamp.exists())
                self.assertEqual(runner.call_count, index + 1)

    def test_runner_exception_does_not_leave_stamp(self):
        result = self.install(Mock(side_effect=OSError('pip unavailable')))
        self.assertEqual(result['reason'], 'torch_install_failed')
        self.assertFalse(self.stamp.exists())

    def test_variant_change_reinstalls_with_exact_variant(self):
        runner = Mock(return_value=Mock(returncode=0))
        self.install(runner)
        runner.reset_mock()
        self.install(runner, 'cu128')
        self.assertIn('torch==2.11.0+cu128', runner.call_args_list[0].args[0])
        self.assertEqual(launch.read_stamp(self.stamp)['variant'], 'cu128')

    def test_failed_variant_change_invalidates_previous_success(self):
        self.install(Mock(return_value=Mock(returncode=0)))
        self.install(Mock(return_value=Mock(returncode=1)), 'cu128')
        runner = Mock(return_value=Mock(returncode=0))
        self.install(runner)
        self.assertEqual(runner.call_count, 3)


class ModelTests(TemporaryDirectoryTest):
    def test_missing_pointer_and_real_model(self):
        path = self.root / 'ROI.pt'
        self.assertEqual(launch.check_model_file(path)['reason'], 'model_missing')
        path.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:123\n')
        result = launch.check_model_file(path)
        self.assertEqual(result['reason'], 'model_is_lfs_pointer')
        self.assertEqual(result['file'], str(path))
        path.write_bytes(b'PK\x03\x04actual model bytes')
        self.assertTrue(launch.check_model_file(path)['ok'])

    def test_all_six_models_are_checked(self):
        for name in launch.MODEL_FILES:
            (self.root / name).write_bytes(b'actual model')
        self.assertTrue(launch.check_models(self.root)['ok'])
        (self.root / 'GradeStage2.json').unlink()
        self.assertEqual(launch.check_models(self.root)['reason'], 'model_missing')

    def test_credentials_warning_names_exact_destination(self):
        path = self.root / 'notification_credentials.json'
        self.assertTrue(launch.check_line_credentials(path)['ok'])
        message = self.report.call_args.args[0]
        self.assertIn(str(path), message)
        self.assertIn('Google Drive', message)
        path.write_text('{}')
        self.report.reset_mock()
        launch.check_line_credentials(path)
        self.report.assert_called_once_with('檢查 LINE 通知憑證……')


class FirewallTests(TemporaryDirectoryTest):
    def test_parser_reports_only_missing_rules(self):
        first, second = launch.FIREWALL_RULES
        self.assertEqual(launch.missing_firewall_rules(f'\r\n {first}\r\n'), [second])
        self.assertEqual(launch.missing_firewall_rules(f'{first}\n{second}\n'), [])
        self.assertEqual(launch.missing_firewall_rules(''), list(launch.FIREWALL_RULES))

    def test_non_windows_does_not_spawn_powershell(self):
        runner = Mock()
        self.assertTrue(launch.check_firewall(runner, 'posix')['ok'])
        runner.assert_not_called()

    def test_missing_rules_and_check_errors_are_soft_warnings(self):
        for response in (Mock(returncode=0, stdout='', stderr=''),
                         Mock(returncode=1, stdout='', stderr='failed')):
            with self.subTest(code=response.returncode):
                runner = Mock(return_value=response)
                self.assertTrue(launch.check_firewall(runner, 'nt')['ok'])
                self.assertIn('首次設定防火牆.bat', self.report.call_args.args[0])
                self.assertEqual(runner.call_args.kwargs['timeout'], 10)
        for error in (OSError('no PowerShell'), subprocess.TimeoutExpired('powershell', 10)):
            self.assertTrue(launch.check_firewall(Mock(side_effect=error), 'nt')['ok'])


class PortTests(TemporaryDirectoryTest):
    def test_bindable_port_starts_without_browser(self):
        opener = Mock()
        result = launch.check_port(lambda: None, lambda: False, opener)
        self.assertEqual(result, {'ok': True, 'already_running': False})
        opener.assert_not_called()

    def test_already_running_opens_browser_without_bind(self):
        bind_check, opener = Mock(), Mock()
        result = launch.check_port(bind_check, lambda: True, opener)
        self.assertEqual(result, {'ok': True, 'already_running': True})
        opener.assert_called_once_with(launch.DASHBOARD_URL)
        bind_check.assert_not_called()

    def test_unbindable_port_reports_reason_and_os_error(self):
        opener = Mock()
        result = launch.check_port(lambda: '[WinError 10013] denied', lambda: False, opener)
        self.assertEqual(result, {'ok': False, 'reason': 'port_in_use',
                                  'detail': '[WinError 10013] denied'})
        opener.assert_not_called()

    def test_port_bind_error_detects_listening_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(('0.0.0.0', 0))
            holder.listen()
            port = holder.getsockname()[1]
            self.assertIsNotNone(launch.port_bind_error(port))
        self.assertIsNone(launch.port_bind_error(port))

    def test_probe_accepts_any_http_status_without_following_redirects(self):
        for status in (200, 302, 404, 500):
            with self.subTest(status=status):
                response = Mock(status=status)
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                connection = Mock()
                connection.getresponse.return_value = response
                with patch.object(launch, 'HTTPSConnection', return_value=connection) as factory:
                    self.assertTrue(launch.probe_dashboard())
                factory.assert_called_once()
                connection.request.assert_called_once_with('GET', '/dashboard/')
                response.__exit__.assert_called_once()
                connection.close.assert_called_once()

    def test_probe_rejects_non_http_services_and_closes_connection(self):
        for error in (OSError('refused'), TimeoutError(), BadStatusLine('non HTTP')):
            connection = Mock()
            connection.getresponse.side_effect = error
            with patch.object(launch, 'HTTPSConnection', return_value=connection):
                self.assertFalse(launch.probe_dashboard())
            connection.close.assert_called_once()


class ServerTests(TemporaryDirectoryTest):
    def test_interrupt_during_readiness_terminates_child(self):
        child = Mock()
        child.poll.return_value = None
        popen = Mock(return_value=child)
        with self.assertRaises(KeyboardInterrupt):
            launch.start_server(self.root, popen, Mock(side_effect=KeyboardInterrupt), Mock())
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)
        child.kill.assert_not_called()
        self.assertEqual(popen.call_args.args[0],
                         [launch.sys.executable, '-s', 'manage.py', 'runsslserver',
                          '0.0.0.0:8000', '--noreload'])
        self.assertEqual(popen.call_args.kwargs['cwd'], self.root)

    def test_interrupt_during_wait_kills_child_if_termination_times_out(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt, subprocess.TimeoutExpired('server', 10), 1]
        with self.assertRaises(KeyboardInterrupt):
            launch.start_server(self.root, Mock(return_value=child), Mock(return_value=True), Mock())
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 3)

    def test_normal_exit_returns_child_code_and_opens_dashboard(self):
        child = Mock()
        child.poll.side_effect = [None, 7]
        child.wait.return_value = 7
        opener = Mock()
        result = launch.start_server(self.root, Mock(return_value=child), Mock(return_value=True), opener)
        self.assertEqual(result, {'ok': True, 'exit_code': 7})
        opener.assert_called_once_with(launch.DASHBOARD_URL)
        child.terminate.assert_not_called()

    def test_early_exit_and_readiness_timeout(self):
        child = Mock()
        child.poll.return_value = 1
        result = launch.start_server(self.root, Mock(return_value=child), Mock(), Mock())
        self.assertEqual(result['reason'], 'server_exited')
        child.terminate.assert_not_called()
        child.poll.return_value = None
        result = launch.start_server(self.root, Mock(return_value=child), Mock(return_value=False),
                                     Mock(), Mock(side_effect=[0, 91]), Mock())
        self.assertEqual(result['reason'], 'server_timeout')
        child.terminate.assert_called_once()

    def test_browser_exception_also_terminates_child(self):
        child = Mock()
        child.poll.return_value = None
        with self.assertRaises(OSError):
            launch.start_server(self.root, Mock(return_value=child), Mock(return_value=True),
                                Mock(side_effect=OSError('browser failed')))
        child.terminate.assert_called_once()


class PreflightTests(TemporaryDirectoryTest):
    def test_inference_cpu_cuda_and_soft_failure(self):
        for device in ('cuda', 'cpu'):
            runner = Mock(return_value=Mock(returncode=0, stdout=device + '\n'))
            self.assertTrue(launch.check_inference_device('cu128', runner)['ok'])
            self.assertEqual(runner.call_args.kwargs['timeout'], 120)
        self.assertIn('570', self.report.call_args.args[0])
        for runner in (Mock(return_value=Mock(returncode=1, stdout='', stderr='import failed')),
                       Mock(side_effect=subprocess.TimeoutExpired('torch', 120))):
            self.assertTrue(launch.check_inference_device('cpu', runner)['ok'])

    def test_django_check_failure_is_structured(self):
        runner = Mock(return_value=Mock(returncode=1))
        self.assertEqual(launch.check_django(self.root, runner)['reason'], 'django_check_failed')
        runner.assert_called_once_with([launch.sys.executable, '-s', 'manage.py', 'check'], cwd=self.root)

    def test_all_failure_reasons_have_chinese_next_actions(self):
        for reason in launch.REASON_MESSAGES:
            with self.subTest(reason=reason):
                self.assertRegex(launch.reason_message(reason), '[\u4e00-\u9fff]')


class WindowsFileFormatTests(unittest.TestCase):
    def test_powershell_bom_and_windows_line_endings(self):
        for name in ('scripts/launcher.ps1', 'scripts/setup_firewall.ps1',
                     '啟動系統.bat', '首次設定防火牆.bat'):
            with self.subTest(name=name):
                content = (launch.ROOT / name).read_bytes()
                if name.endswith('.ps1'):
                    self.assertTrue(content.startswith(codecs.BOM_UTF8))
                self.assertIn(b'\r\n', content)
                self.assertNotIn(b'\n', content.replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main()
