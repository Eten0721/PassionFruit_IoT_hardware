import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import diagnostics, views
from . import tests as existing


class DiagnosticsModuleTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.runtime = self.root / 'runtime_config'
        self.dataset = self.root / 'dataset'

    def test_event_log_drops_older_half_past_the_cap(self):
        with mock.patch.object(diagnostics, 'EVENT_LOG_MAX_BYTES', 200):
            for index in range(40):
                diagnostics.append_event(self.runtime, {'event': 'tick', 'index': index})
        lines = (self.runtime / diagnostics.EVENT_LOG_NAME).read_text(encoding='utf-8').splitlines()
        self.assertLess(len(lines), 40)
        self.assertEqual(json.loads(lines[-1])['index'], 39)

    def test_export_has_state_markers_and_no_credentials_or_photos(self):
        self.runtime.mkdir()
        (self.runtime / 'sorter_recovery.json').write_text('{"phase": "running"}', encoding='utf-8')
        (self.runtime / 'notification_credentials.json').write_text('{"token": "secret"}', encoding='utf-8')
        (self.runtime / 'assistant_credentials.json').write_text('{"key": "secret"}', encoding='utf-8')
        fruit = self.dataset / 'detection' / 'temp' / 'fruit_001'
        fruit.mkdir(parents=True)
        (fruit / 'img_01.jpg').write_bytes(b'jpeg')
        (fruit / '.capture-session.json').write_text('{"work_mode": "detection"}', encoding='utf-8')
        stream = io.BytesIO()
        diagnostics.write_export(stream, state={'status': 'error'}, runtime_root=self.runtime,
                                 dataset_root=self.dataset)
        with zipfile.ZipFile(stream) as archive:
            names = set(archive.namelist())
            listing = json.loads(archive.read('dataset/temp_listing.json'))
        self.assertIn('state.json', names)
        self.assertIn('runtime_config/sorter_recovery.json', names)
        self.assertIn('dataset/detection/temp/fruit_001/.capture-session.json', names)
        self.assertFalse(any('credentials' in name or name.endswith('.jpg') for name in names), names)
        self.assertEqual(listing['detection/temp/fruit_001'], ['.capture-session.json', 'img_01.jpg'])


class DiagnosticsExportTests(SimpleTestCase):
    setUp = existing.DataCollectionFlowTests.setUp
    tearDown = existing.DataCollectionFlowTests.tearDown

    def test_rejections_and_transitions_survive_runtime_reset(self):
        diagnostics._last_rejection = None
        self.assertEqual(self.client.post('/api/discard/').status_code, 409)  # No dataset_mode: refused with a reason.
        self.assertEqual(self.client.post('/api/runtime/reset/').status_code, 200)
        response = self.client.get('/api/diagnostics/export/')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            events = [json.loads(line) for line in
                      archive.read(f'runtime_config/{diagnostics.EVENT_LOG_NAME}').decode('utf-8').splitlines()]
            state = json.loads(archive.read('state.json'))
        self.assertIn('stale_dataset_mode', [event.get('reason') for event in events if event['event'] == 'request_rejected'])
        self.assertIn('runtime_reset', [event['event'] for event in events])
        self.assertEqual(state['status'], views.APP_STATE['status'])
