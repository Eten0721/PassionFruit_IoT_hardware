"""Statistics image and export ZIP drawn from fake detection data."""

import io
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image

from . import detection_report
from .test_detection_stats import FakeDataset, _view


@unittest.skipUnless(detection_report.DEFAULT_FONT_PATH.is_file(), '需要微軟正黑體')
class DetectionReportTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = FakeDataset(Path(temp.name) / 'detection')

    def test_export_contains_rendered_report_and_rejects_bad_option(self):
        self.data.add(1, '2026-10-16 10:00:00', grade=0,
                      views_=[_view(defects=['abrasion']), _view(), _view()])
        self.data.add(2, '2026-10-16 10:05:00', grade=3)
        stream = io.BytesIO()
        with self.assertRaises(ValueError):
            detection_report.write_export(self.data.root, stream, include_results='yes')

        exported = detection_report.write_export(
            self.data.root, stream, include_results=False, now=datetime(2026, 10, 17, 9, 30))

        self.assertEqual(exported, {'fruits': 2, 'results': 0,
                                    'filename': 'detection_export_20261017_0930.zip'})
        with zipfile.ZipFile(stream) as archive, \
                Image.open(io.BytesIO(archive.read('report.png'))) as image:
            self.assertEqual(image.size, (1800, 2250))

    def test_empty_partition_and_alert_snapshot_render(self):
        empty = detection_report.snapshot([], title='空', subtitle='無資料')
        self.assertIsNone(empty['period'])
        alert = detection_report.snapshot([], title='提醒', subtitle='近 7 天', previous=[],
                                          highlight={'processing', 'abrasion'})
        self.assertEqual(alert['highlight'], ['abrasion', 'processing'])
        for report, image_format in ((empty, 'png'), (alert, 'jpg')):
            target = io.BytesIO()
            detection_report.render(report, target, image_format=image_format)
            with Image.open(target) as image:
                self.assertEqual(image.format, 'PNG' if image_format == 'png' else 'JPEG')
        with self.assertRaises(ValueError):
            detection_report.render(empty, io.BytesIO(), image_format='gif')
        with self.assertRaises(FileNotFoundError):
            detection_report.render(empty, io.BytesIO(), font_path=Path('missing.ttc'))
