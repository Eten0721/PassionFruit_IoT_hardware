"""Dashboard Gemini assistant with a fake HTTP boundary; no real API calls."""

import inspect
import json
import tempfile
import urllib.error
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from . import assistant, views
from .test_detection_stats import FakeDataset, _view


def _candidate(*parts, grounding=None):
    candidate = {'content': {'role': 'model', 'parts': list(parts)}}
    if grounding:
        candidate['groundingMetadata'] = {'groundingChunks': grounding}
    return 200, {'candidates': [candidate]}


class FakeGemini:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({'url': url, 'payload': json.loads(json.dumps(payload)),
                           'headers': headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AssistantTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.credentials = self.root / 'assistant_credentials.json'
        self.usage = self.root / 'assistant_usage.json'
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        (self.docs / '快速開始.md').write_text('雙擊 啟動系統.bat', encoding='utf-8')
        self.data = FakeDataset(self.root / 'detection')
        self.today = date(2026, 10, 16)

    def configure(self, **extra):
        self.credentials.write_text(json.dumps({
            'gemini_api_key': 'secret-key', 'model': 'gemini-test', **extra,
        }), encoding='utf-8')

    def ask(self, http, text='問題'):
        bot = assistant.Assistant(self.credentials, self.usage, self.data.root, self.docs,
                                  http_post=http, today=lambda: self.today)
        return bot.ask([{'role': 'user', 'text': text}])

    def test_not_configured_does_not_call_gemini(self):
        http = FakeGemini()
        self.assertEqual(self.ask(http)['reason'], 'assistant_not_configured')
        self.configure(model=' ')
        self.assertEqual(self.ask(http)['reason'], 'assistant_not_configured')
        self.assertEqual(http.calls, [])

    def test_stats_tool_numbers_and_chart_come_from_django(self):
        self.configure()
        self.data.add(1, '2026-10-15 09:00:00', grade=3)
        self.data.add(2, '2026-10-15 10:00:00', grade=0,
                      views_=[_view(defects=['anthracnose']), _view(), _view()])
        call = {'functionCall': {'name': 'query_detection_stats',
                                 'args': {'group_by': 'defect', 'start_date': '2026-10-15'}},
                'thoughtSignature': 'sig'}
        http = FakeGemini(_candidate(call), _candidate({'text': '炭疽病 1 顆，佔 50%。'}))

        answer = self.ask(http, '這兩天炭疽病多嗎？')

        self.assertTrue(answer['ok'])
        self.assertEqual(answer['reply'], '炭疽病 1 顆，佔 50%。')
        self.assertEqual(answer['charts'][0]['title'], '瑕疵出現比例')
        self.assertEqual(answer['charts'][0]['datasets'][0]['data'][3], 50.0)
        first, second = http.calls
        self.assertEqual(first['url'], assistant.API_URL.format(model='gemini-test'))
        self.assertEqual(first['headers'], {'x-goog-api-key': 'secret-key'})
        instruction = first['payload']['systemInstruction']['parts'][0]['text']
        self.assertIn('今天日期：2026-10-16', instruction)
        self.assertIn('雙擊 啟動系統.bat', instruction)
        self.assertIn('（本機找不到此文件）', instruction)
        # The model turn is echoed verbatim, then the Django tool result follows.
        self.assertEqual(second['payload']['contents'][1], {'role': 'model', 'parts': [call]})
        response = second['payload']['contents'][2]['parts'][0]['functionResponse']
        self.assertEqual(response['response']['totals']['graded'], 2)
        self.assertNotIn('chart', response['response'])
        self.assertEqual(answer['usage'], {'used': 2, 'limit': assistant.DEFAULT_DAILY_REQUEST_LIMIT})

    def test_search_runs_as_separate_grounded_request_with_sources(self):
        self.configure()
        http = FakeGemini(
            _candidate({'functionCall': {'name': 'search_agriculture_knowledge',
                                         'args': {'question': '百香果炭疽病防治'}}}),
            _candidate({'text': '保持通風。'}, grounding=[
                {'web': {'uri': 'https://example.org/a', 'title': '改良場'}},
                {'web': {'uri': 'http://insecure.example/b', 'title': 'x'}},
            ]),
            _candidate({'text': '建議保持通風，用藥依改良場建議。'}),
        )

        answer = self.ask(http, '炭疽病怎麼防治？')

        self.assertEqual(answer['sources'], [{'title': '改良場', 'uri': 'https://example.org/a'}])
        main, search, final = (call['payload'] for call in http.calls)
        self.assertEqual(main['tools'], [{'functionDeclarations': [
            assistant.STATS_TOOL, assistant.SEARCH_TOOL]}])
        self.assertEqual(search['tools'], [{'google_search': {}}])
        self.assertNotIn('functionDeclarations', json.dumps(search))
        result = final['contents'][2]['parts'][0]['functionResponse']['response']
        self.assertEqual(result['answer'], '保持通風。')

    def test_grade_filter_reaches_the_stats_tool(self):
        self.configure()
        self.data.add(1, '2026-10-15 09:00:00', grade=0,
                      views_=[_view(color='bad_color'), _view(), _view()])
        self.data.add(2, '2026-10-15 10:00:00', grade=3,
                      views_=[_view(color='bad_color'), _view(), _view()])
        call = {'functionCall': {'name': 'query_detection_stats',
                                 'args': {'group_by': 'defect', 'grades': ['加工']}}}
        http = FakeGemini(_candidate(call), _candidate({'text': '加工果都有色澤異常。'}))

        answer = self.ask(http, '加工果裡最常見的瑕疵？')

        response = http.calls[1]['payload']['contents'][2]['parts'][0]['functionResponse']['response']
        self.assertEqual(response['totals']['graded'], 1)
        self.assertEqual(response['defects'][0], {'key': 'bad_color', 'label': '色澤異常',
                                                  'count': 1, 'ratio': 1.0})
        self.assertEqual(answer['charts'][0]['title'], '瑕疵出現比例（加工）')
        grades = assistant.STATS_TOOL['parameters']['properties']['grades']
        self.assertEqual(grades['items']['enum'], ['上等', '中等', '下等', '加工'])

    def test_billing_and_model_errors_have_own_reasons_and_log_without_key(self):
        self.configure()
        for status, reason in ((402, 'assistant_billing_required'),
                               (404, 'assistant_model_unavailable')):
            with self.subTest(status=status), self.assertLogs('fruit_app.assistant', 'WARNING') as logs:
                body = {'error': {'message': f'Problem for key secret-key (HTTP {status})'}}
                answer = self.ask(FakeGemini((status, body)))
                self.assertEqual(answer['reason'], reason)
                self.assertEqual(answer['message'], assistant.MESSAGES[reason])
                output = '\n'.join(logs.output)
                self.assertIn(f'Problem for key *** (HTTP {status})', output)
                self.assertNotIn('secret-key', output)

    def test_search_failure_keeps_answer_and_explains_paid_tier(self):
        self.configure()
        search_call = _candidate({'functionCall': {'name': 'search_agriculture_knowledge',
                                                   'args': {'question': '炭疽病防治'}}})
        http = FakeGemini(search_call, (429, {'error': {'message': 'grounding not available'}}),
                          _candidate({'text': '目前無法搜尋，請洽在地改良場。'}))

        with self.assertLogs('fruit_app.assistant', 'WARNING'):
            answer = self.ask(http, '炭疽病怎麼防治？')

        self.assertTrue(answer['ok'])
        self.assertEqual(answer['reply'], '目前無法搜尋，請洽在地改良場。')
        self.assertEqual(answer['notices'], [assistant.SEARCH_UNAVAILABLE['default']])
        self.assertIn('付費層級', answer['notices'][0])
        result = http.calls[2]['payload']['contents'][2]['parts'][0]['functionResponse']['response']
        self.assertEqual(result['error'], 'search_unavailable')

    def test_daily_limit_during_search_ends_the_turn(self):
        self.configure(daily_request_limit=1)
        search_call = _candidate({'functionCall': {'name': 'search_agriculture_knowledge',
                                                   'args': {'question': 'x'}}})
        self.assertEqual(self.ask(FakeGemini(search_call))['reason'], 'assistant_quota_exceeded')

    def test_failures_return_structured_reasons(self):
        self.configure()
        cases = (
            (assistant._Failure('assistant_offline'), 'assistant_offline'),
            ((429, {}), 'assistant_quota_exceeded'),
            ((402, {}), 'assistant_billing_required'),
            ((404, {}), 'assistant_model_unavailable'),
            ((400, {}), 'assistant_rejected'),
            ((503, {}), 'assistant_service_error'),
            ((200, {'candidates': []}), 'assistant_invalid_response'),
        )
        for response, reason in cases:
            with self.subTest(reason=reason):
                answer = self.ask(FakeGemini(response))
                self.assertEqual(answer['reason'], reason)
                self.assertFalse(answer['ok'])
                self.assertNotIn('secret-key', json.dumps(answer, ensure_ascii=False))

    def test_daily_limit_blocks_before_sending_and_resets_next_day(self):
        self.configure(daily_request_limit=1)
        self.assertTrue(self.ask(FakeGemini(_candidate({'text': 'ok'})))['ok'])
        blocked = FakeGemini()
        self.assertEqual(self.ask(blocked)['reason'], 'assistant_quota_exceeded')
        self.assertEqual(blocked.calls, [])
        self.today = date(2026, 10, 17)
        self.assertTrue(self.ask(FakeGemini(_candidate({'text': 'ok'})))['ok'])

    def test_alert_wording_sends_only_facts_without_tools(self):
        bot = lambda http: assistant.Assistant(self.credentials, self.usage, self.data.root,  # noqa: E731
                                               self.docs, http_post=http, today=lambda: self.today)
        facts = {'category': '炭疽病', 'current': {'count': 8, 'graded': 20, 'ratio': 0.4}}
        self.assertEqual(bot(FakeGemini()).write_alert_advice(facts)['reason'],
                         'assistant_not_configured')
        self.configure()
        http = FakeGemini(_candidate({'text': '請加強清園。'}))
        self.assertEqual(bot(http).write_alert_advice(facts),
                         {'ok': True, 'reason': None, 'text': '請加強清園。'})
        [call] = http.calls
        self.assertNotIn('tools', call['payload'])
        self.assertEqual(json.loads(call['payload']['contents'][0]['parts'][0]['text']), facts)
        self.assertEqual(bot(FakeGemini((429, {}))).write_alert_advice(facts)['reason'],
                         'assistant_quota_exceeded')

    def test_tool_rounds_are_bounded(self):
        self.configure()
        loop = _candidate({'functionCall': {'name': 'unknown_tool', 'args': {}}})
        http = FakeGemini(*[loop] * (assistant.MAX_TOOL_ROUNDS + 1))
        self.assertEqual(self.ask(http)['reason'], 'assistant_tool_rounds_exceeded')

    def test_invalid_messages_raise(self):
        bot = assistant.Assistant(self.credentials, self.usage, self.data.root, self.docs)
        for messages in (None, [], [{'role': 'system', 'text': 'x'}],
                         [{'role': 'user', 'text': ' '}],
                         [{'role': 'user', 'text': 'x' * (assistant.MAX_MESSAGE_CHARS + 1)}],
                         [{'role': 'user', 'text': 'q'}, {'role': 'model', 'text': 'a'}]):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                bot.ask(messages)

    def test_http_boundary_maps_network_errors_to_offline(self):
        with mock.patch('urllib.request.urlopen',
                        side_effect=urllib.error.URLError(OSError('down'))):
            with self.assertRaises(assistant._Failure) as raised:
                assistant.http_post_json('https://example.invalid', {}, {}, 1)
        self.assertEqual(raised.exception.reason, 'assistant_offline')


class LauncherIconTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'runtime_config' / 'assistant_icon.png'

    def image_bytes(self, size, fmt='JPEG'):
        buffer = BytesIO()
        Image.new('RGB', size, 'orange').save(buffer, format=fmt)
        return buffer.getvalue()

    def test_upload_is_cropped_square_and_resized(self):
        for size in ((1600, 900), (40, 90)):
            with self.subTest(size=size):
                self.assertEqual(assistant.save_launcher_icon(self.image_bytes(size), self.path),
                                 {'ok': True, 'reason': None})
                with Image.open(self.path) as icon:
                    self.assertEqual((icon.format, icon.size),
                                     ('PNG', (assistant.ICON_SIZE, assistant.ICON_SIZE)))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['assistant_icon.png'])

    def test_bad_uploads_return_reasons_and_keep_existing_icon(self):
        assistant.save_launcher_icon(self.image_bytes((200, 200)), self.path)
        before = self.path.read_bytes()
        self.assertEqual(assistant.save_launcher_icon(b'', self.path)['reason'], 'icon_empty')
        self.assertEqual(assistant.save_launcher_icon(b'not an image', self.path)['reason'],
                         'icon_unreadable')
        with mock.patch.object(assistant, 'ICON_MAX_PIXELS', 100):
            self.assertEqual(assistant.save_launcher_icon(self.image_bytes((20, 20)), self.path)['reason'],
                             'icon_too_large')
        self.assertEqual(self.path.read_bytes(), before)

    def test_icon_endpoints_upload_serve_and_reset(self):
        unwrapped = inspect.unwrap(views.assistant_icon_api)
        factory = RequestFactory()
        with mock.patch.object(views, '_assistant_icon_path', return_value=self.path):
            self.assertEqual(self.client.get('/api/assistant/icon.png').status_code, 404)
            upload = SimpleUploadedFile('me.png', self.image_bytes((300, 200), 'PNG'), 'image/png')
            response = unwrapped(factory.post('/api/assistant/icon/', {'icon': upload}))
            self.assertEqual(json.loads(response.content)['has_icon'], True)
            served = self.client.get('/api/assistant/icon.png')
            self.assertEqual((served.status_code, served['Content-Type']), (200, 'image/png'))
            served.close()
            bad = unwrapped(factory.post('/api/assistant/icon/', {
                'icon': SimpleUploadedFile('x.png', b'nope', 'image/png')}))
            self.assertEqual((bad.status_code, json.loads(bad.content)['reason']),
                             (400, 'icon_unreadable'))
            reset = unwrapped(factory.post('/api/assistant/icon/', {'reset': 'true'}))
            self.assertEqual(json.loads(reset.content)['has_icon'], False)
            self.assertFalse(self.path.exists())


class AssistantViewTests(SimpleTestCase):
    def test_view_maps_invalid_input_and_never_touches_runtime_state(self):
        bot = mock.Mock()
        bot.ask.side_effect = ValueError('messages 必須是非空陣列。')
        request = mock.Mock(method='POST', META={'CONTENT_TYPE': 'application/json'},
                            body=b'{"messages": []}')
        with mock.patch.object(views, 'dashboard_assistant', return_value=bot), \
                mock.patch.object(views, 'STATE_LOCK') as lock:
            response = inspect.unwrap(views.assistant_api)(request)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(json.loads(response.content)['reason'], 'invalid_assistant_request')
            bot.ask.side_effect = None
            bot.ask.return_value = {'ok': False, 'reason': 'assistant_offline'}
            response = inspect.unwrap(views.assistant_api)(request)
            self.assertEqual(json.loads(response.content)['reason'], 'assistant_offline')
        lock.__enter__.assert_not_called()
