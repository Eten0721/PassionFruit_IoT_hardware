import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

from .reason_text import REASON_TEXT, STATUS_TEXT, describe

APP_DIR = Path(__file__).resolve().parent


def _tree(name):
    return ast.parse((APP_DIR / name).read_text(encoding='utf-8'))


def _returned_codes(name, function_matches):
    codes = set()
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.FunctionDef) and function_matches(node.name):
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Return)
                    and isinstance(child.value, ast.Constant)
                    and isinstance(child.value.value, str)
                ):
                    codes.add(child.value.value)
    return codes


def _dict_keys_in(name, function_name):
    """Keys of ``{...}.get(reason, ...)`` lookups, i.e. the reasons a function handles."""
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                key.value
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == 'get'
                and isinstance(child.func.value, ast.Dict)
                and child.args and isinstance(child.args[0], ast.Name)
                and child.args[0].id == 'reason'
                for key in child.func.value.keys if isinstance(key, ast.Constant)
            }
    raise AssertionError(f'{function_name} not found in {name}')


class ReasonTextCoverageTests(SimpleTestCase):
    """Every code the operator can see on the main screen needs Chinese text."""

    def assert_translated(self, codes):
        missing = sorted(code for code in codes if code not in REASON_TEXT)
        self.assertEqual(missing, [], f'缺少中文說明：{missing}')

    def test_disabled_reasons_are_translated(self):
        self.assert_translated(
            _returned_codes('views.py', lambda name: 'disabled_reason' in name)
            | _returned_codes('network_recovery.py', lambda name: 'reason' in name)
            | _returned_codes('local_network.py', lambda name: name == 'request_reason')
        )

    def test_operator_alert_reasons_are_translated(self):
        self.assert_translated(_dict_keys_in('views.py', '_operator_alert_payload'))

    def test_recovery_and_network_reasons_are_translated(self):
        views_source = (APP_DIR / 'views.py').read_text(encoding='utf-8')
        network_source = (APP_DIR / 'local_network.py').read_text(encoding='utf-8')
        self.assert_translated(
            set(re.findall(r"auto_run_recovery_reason'\] = '([a-z0-9_]+)'", views_source))
            | set(re.findall(r"'reason': '([a-z0-9_]+)'", network_source))
            | set(re.findall(r"self\._reason = '([a-z0-9_]+)'", network_source))
            | {'camera_connection_lost', 'esp32_connection_lost', 'esp32_network_interlocked',
               'hcsr04', 'max_run_timeout', 'waiting_for_report'}
        )

    def test_discovery_statuses_are_translated(self):
        network_source = (APP_DIR / 'local_network.py').read_text(encoding='utf-8')
        statuses = set(re.findall(r"'status': '([a-z_]+)'", network_source)) | {'round_trip_complete'}
        missing = sorted(status for status in statuses if status not in STATUS_TEXT)
        self.assertEqual(missing, [])

    def test_describe_uses_chinese_only(self):
        self.assertEqual(describe(None), '')
        self.assertEqual(describe('network_capability_missing'), REASON_TEXT['network_capability_missing'])
        self.assertEqual(describe('esp32_not_idle:motion'), REASON_TEXT['esp32_not_idle'])
        self.assertEqual(describe('grade_model_failed: ValueError: bad'), REASON_TEXT['grade_model_failed'])
        self.assertEqual(describe('brand_new_code'), '其他狀態，詳見診斷區')

    def test_dashboard_hides_raw_codes_outside_diagnostics(self):
        dashboard_js = (APP_DIR / 'static' / 'fruit_app' / 'js' / 'dashboard.js').read_text(encoding='utf-8')
        self.assertNotIn('（${code}）', dashboard_js)
        self.assertIn("'其他狀態，詳見診斷區'", dashboard_js)
        response = self.client.get('/dashboard/')
        self.assertContains(response, 'id="status-text"')
        self.assertContains(response, 'id="reason-codes"')
