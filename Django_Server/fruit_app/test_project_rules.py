"""AGENTS.md rules that a machine can check; each bug fixed by a rule belongs here."""

import ast
from pathlib import Path

from django.test import SimpleTestCase

APP_DIR = Path(__file__).resolve().parent


def error_responses_without_reason(source, filename='<source>'):
    """Every 4xx/5xx JSON reply names a reason, so the Dashboard never shows a bare failure."""
    missing = []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        if name == '_json_error':
            reason = keywords.get('reason')
            if reason is None or (isinstance(reason, ast.Constant) and not reason.value):
                missing.append(node.lineno)
        elif name == 'JsonResponse':
            status = keywords.get('status')
            if not (isinstance(status, ast.Constant) and isinstance(status.value, int) and status.value >= 400):
                continue
            payload = node.args[0] if node.args else None
            keys = [key.value for key in payload.keys if isinstance(key, ast.Constant)] if isinstance(payload, ast.Dict) else []
            if 'reason' not in keys:
                missing.append(node.lineno)
    return missing


class ProjectRuleTests(SimpleTestCase):
    def test_checker_flags_only_errors_without_reason(self):
        source = '\n'.join([
            "_json_error('a', status=409)",
            "_json_error('b', status=409, reason=None)",
            "_json_error('c', status=409, reason='busy')",
            "JsonResponse({'status': 'error'}, status=500)",
            "JsonResponse({'status': 'error', 'reason': 'x'}, status=500)",
            "JsonResponse({'ok': True})",
        ])
        self.assertEqual(error_responses_without_reason(source), [1, 2, 4])

    def test_every_error_response_has_a_reason(self):
        for path in sorted(APP_DIR.glob('*.py')):
            if path.name.startswith('test'):
                continue
            with self.subTest(file=path.name):
                missing = error_responses_without_reason(path.read_text(encoding='utf-8'), str(path))
                self.assertEqual(missing, [], f'{path.name} lines need reason=: {missing}')
