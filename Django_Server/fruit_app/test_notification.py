"""Farmer notification previews through the local generation interface."""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw, ImageFont
from django.core.management import call_command
from django.test import SimpleTestCase

from .notification import generate_notification


class NotificationTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.result_path = self.root / 'result.json'

    def save_result(self, classification):
        self.result_path.write_text(json.dumps({
            'contract_version': 1,
            'result_id': '34aa0123456789abcdef0123456789abcd',
            'fruit_id': 'fruit_001',
            'hardware_mode': 'photo_only',
            'capture_time': '2026-10-14T09:00:00+08:00',
            'classification': classification,
            'storage': {'status': 'saved'},
            'images': {},
        }), encoding='utf-8')

    def test_non_processing_and_ungraded_results_do_not_notify(self):
        for source in ('model', 'manual'):
            for grade, label, code in ((1, '下等', 'low'),
                                       (2, '中等', 'medium'),
                                       (3, '上等', 'high')):
                with self.subTest(grade=grade, source=source):
                    self.save_result({'status': 'graded', 'grade': grade,
                                      'label': label, 'code': code, 'source': source})
                    self.assertEqual(generate_notification(self.result_path), {
                        'ok': False, 'reason': 'not_processing',
                    })
        self.save_result({'status': 'needs_review', 'grade': None,
                          'label': None, 'code': None, 'source': None})
        self.assertEqual(generate_notification(self.result_path), {
            'ok': False, 'reason': 'not_graded',
        })
        result = json.loads(self.result_path.read_text(encoding='utf-8'))
        result['inference'] = {'status': 'failed', 'reason': 'detection_incomplete'}
        self.write_fixture(result)
        self.assertEqual(generate_notification(self.result_path), {
            'ok': False, 'reason': 'not_graded',
        })
        result['classification'].update(status='abandoned', source='manual')
        self.write_fixture(result)
        self.assertEqual(generate_notification(self.result_path), {
            'ok': False, 'reason': 'not_graded',
        })
        self.assertEqual(list(self.root.iterdir()), [self.result_path])

    def processing_fixture(self, source='model'):
        self.save_result({'status': 'graded', 'grade': 0, 'label': '加工',
                          'code': 'processing', 'source': source,
                          'worst_view': 'img_01.jpg', 'reason': None})
        result = json.loads(self.result_path.read_text(encoding='utf-8'))
        result.update(
            completed_at='2026-10-14T09:01:00+08:00',
            inference={'status': 'completed', 'reason': None},
            sorting={'status': 'skipped', 'reason': 'photo_only'},
            status='completed', complete=True,
        )
        for index, color in enumerate(('red', 'green', 'blue'), 1):
            stem = f'img_{index:02d}'
            original = f'{stem}_original.jpg'
            annotated = f'{stem}_defect_annotated.jpg'
            marked = f'{stem}_defect_marked.jpg'
            with Image.new('RGB', (320, 240), color) as image:
                image.save(self.root / original)
            with Image.new('RGB', (320, 240), 'yellow') as image:
                ImageDraw.Draw(image).text((5, 5), 'abrasion 0.987654', fill='black')
                image.save(self.root / annotated)
            with Image.new('RGB', (320, 240), 'cyan') as image:
                image.save(self.root / marked)
            result['images'][f'{stem}.jpg'] = {
                'roi': {'status': 'ok'},
                'models': {
                    'color': {'status': 'ok', 'class_name': 'good_color',
                              'display_label': '好', 'confidence': 0.987654},
                    'wrinkle': {'status': 'ok', 'class_name': 'false(沒皺褶)',
                                'display_label': '無皺褶（好）', 'confidence': 0.987654},
                    'defect': {'status': 'ok', 'detections': [
                        {'class_name': 'abrasion', 'class_index': 0,
                         'confidence': 0.987654, 'mask_area_ratio': 0.1},
                    ]},
                },
                'artifacts': {'original': original, 'defect_annotated': annotated,
                              'defect_marked': marked},
                'grade': {'status': 'ok', 'grade': 0, 'label': '加工',
                          'code': 'processing'},
            }
        return result

    def write_fixture(self, result):
        self.result_path.write_text(json.dumps(result, ensure_ascii=False),
                                    encoding='utf-8')

    def test_model_processing_preview_has_original_time_identity_and_observations(self):
        result = self.processing_fixture()
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        text = Path(preview['text_path']).read_text(encoding='utf-8')
        self.assertEqual(preview['text'], text)
        for expected in ('2026-10-14T09:00:00+08:00', result['result_id'],
                         '加工等級', '照片 1：擦傷', '照片 2：擦傷', '照片 3：擦傷'):
            self.assertIn(expected, text)
        self.assertNotIn(result['completed_at'], text)
        self.assertTrue(text.startswith('測試'))
        for forbidden in ('0.987654', '98.7654', 'confidence', 'processing',
                          'class_name', 'result_id', 'classification', 'abrasion',
                          '因此', '導致', '判級原因', '因為', '已分槽',
                          '已放入加工籃', '可食用'):
            self.assertNotIn(forbidden, text)
        for key, limit, width in (('image_path', 10_000_000, 1920),
                                  ('preview_path', 1_000_000, 960)):
            path = Path(preview[key])
            self.assertIn(result['result_id'], path.name)
            self.assertLessEqual(path.stat().st_size, limit)
            with Image.open(path) as image:
                self.assertEqual(image.format, 'JPEG')
                self.assertEqual(image.width, width)

    def manual_fixture(self):
        result = self.processing_fixture('manual')
        result['model_classification'] = {
            'status': 'needs_review', 'grade': None, 'label': None, 'code': None,
            'source': None, 'worst_view': None, 'reason': 'grade_model_failed',
        }
        result['classification'].update(worst_view=None,
                                         decided_at='2026-10-14T10:00:00+08:00')
        result.update(inference={'status': 'failed', 'reason': 'detection_incomplete'},
                      complete=False, status='manual_completed', reason='grade_model_failed')
        for image in result['images'].values():
            image['grade'] = {'status': 'unavailable', 'grade': None,
                              'reason': 'grade_model_failed', 'features': None}
        return result

    def test_manual_processing_uses_final_classification_without_rewriting_any_input(self):
        result = self.manual_fixture()
        result['hardware_mode'] = 'hardware'
        result['sorting'] = {'status': 'not_started', 'reason': 'gate3_held'}
        self.write_fixture(result)
        (self.root / 'detection_summary.csv').write_text('original summary', encoding='utf-8')
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with mock.patch('socket.socket.connect', side_effect=AssertionError('No network')):
            preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        self.assertIn('人工判為加工等級', preview['text'])
        self.assertFalse(preview['text'].startswith('測試'))
        for filename, data in before.items():
            self.assertEqual((self.root / filename).read_bytes(), data)
        saved = json.loads(self.result_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['model_classification'], result['model_classification'])
        self.assertIsNone(saved['images']['img_01.jpg']['grade']['grade'])
        for forbidden in ('已分槽', '已放入加工籃', '可食用', 'grade_model_failed', 'None'):
            self.assertNotIn(forbidden, preview['text'])

    def test_successful_views_without_defects_are_reported_honestly(self):
        result = self.processing_fixture()
        for image in result['images'].values():
            image['models']['defect']['detections'] = []
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        self.assertEqual(preview['text'].count('未偵測到瑕疵'), 3)
        self.assertNotIn('擦傷', preview['text'])

    def test_disease_and_other_successful_observations_are_chinese_and_suspected(self):
        result = self.processing_fixture()
        first = result['images']['img_01.jpg']['models']
        first['color']['class_name'] = 'bad_color'
        first['wrinkle']['display_label'] = '有皺褶（壞）'
        first['defect']['detections'] = [
            {'class_name': name, 'class_index': index, 'confidence': 0.987654}
            for index, name in enumerate(('abrasion', 'anthracnose', 'insect_bite', 'insect_track'))
        ]
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        self.assertEqual(preview['views'][0]['observations'],
                         ['色澤異常', '皺褶', '擦傷', '疑似炭疽病', '蟲咬', '蟲跡'])
        self.assertIn('疑似炭疽病', preview['text'])
        for forbidden in ('0.987654', 'anthracnose', 'insect_bite', 'insect_track',
                          '因此', '因為', '導致', '已分槽', '可食用'):
            self.assertNotIn(forbidden, preview['text'])

    def test_failed_view_uses_original_and_ignores_failed_model_detections(self):
        result = self.manual_fixture()
        second = result['images']['img_02.jpg']
        second['models']['defect']['status'] = 'error'
        second['models']['defect']['detections'] = [{'class_name': 'anthracnose'}]
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        view = preview['views'][1]
        self.assertEqual(view['label'], '未完成檢測')
        self.assertEqual(view['image_path'], str(self.root / 'img_02_original.jpg'))
        self.assertNotIn('疑似炭疽病', preview['text'])
        with Image.open(preview['image_path']) as image:
            self.assertLess(image.getpixel((960, image.height - 240))[0], 5)
            self.assertGreater(image.getpixel((960, image.height - 240))[1], 120)

    def test_partial_model_success_retains_only_successful_observations(self):
        result = self.manual_fixture()
        result['images']['img_02.jpg']['models']['color']['status'] = 'error'
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertEqual(preview['views'][1]['label'], '未完成檢測；擦傷')
        self.assertNotIn('未偵測到瑕疵', preview['views'][1]['label'])

    def test_no_usable_detection_results_are_not_described_as_no_defects(self):
        result = self.manual_fixture()
        for image in result['images'].values():
            image['roi']['status'] = 'error'
            for model in image['models'].values():
                model['status'] = 'skipped'
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        self.assertIn('無可用檢測結果', preview['text'])
        self.assertEqual(preview['text'].count('未完成檢測'), 3)
        self.assertNotIn('未偵測到瑕疵', preview['text'])

    def test_missing_view_has_a_placeholder_without_invented_image_or_marks(self):
        result = self.manual_fixture()
        del result['images']['img_03.jpg']
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        view = preview['views'][2]
        self.assertIsNone(view['image_path'])
        self.assertEqual(view['observations'], [])
        self.assertEqual(view['label'], '未完成檢測；未取得影像')
        with Image.open(preview['image_path']) as image:
            self.assertEqual(image.getpixel((1290, image.height - 10)), (238, 238, 238))

    def test_missing_marked_image_falls_back_to_original_without_changing_detection_status(self):
        result = self.processing_fixture()
        (self.root / 'img_02_defect_marked.jpg').unlink()
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        view = preview['views'][1]
        self.assertTrue(view['detection_complete'])
        self.assertEqual(view['image_path'], str(self.root / 'img_02_original.jpg'))
        self.assertEqual(view['label'], '無標示圖；擦傷')

    def test_legacy_annotated_image_is_never_used_in_farmer_preview(self):
        result = self.processing_fixture()
        for image in result['images'].values():
            del image['artifacts']['defect_marked']
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        self.assertTrue(preview['ok'])
        for index, view in enumerate(preview['views'], 1):
            self.assertTrue(view['detection_complete'])
            self.assertEqual(view['label'], '無標示圖；擦傷')
            self.assertEqual(view['image_path'], str(self.root / f'img_{index:02d}_original.jpg'))
        self.assertNotIn('未完成檢測', preview['text'])
        self.assertNotIn('未偵測到瑕疵', preview['text'])
        with Image.open(preview['image_path']) as image:
            # The annotated fixture is yellow with English text; the original is red.
            red, green, blue = image.getpixel((320, image.height - 240))
            self.assertGreater(red, 245)
            self.assertLess(green, 10)
            self.assertLess(blue, 10)

    def test_chinese_legend_matches_ultralytics_class_colors_and_marked_image(self):
        from ultralytics.utils.plotting import colors

        result = self.processing_fixture()
        result['images']['img_01.jpg']['models']['defect']['detections'] = [
            {'class_name': 'abrasion', 'class_index': 3, 'confidence': 0.87},
            {'class_name': 'anthracnose', 'class_index': 0, 'confidence': 0.98},
        ]
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        first = preview['views'][0]
        self.assertEqual(first['image_path'], str(self.root / 'img_01_defect_marked.jpg'))
        self.assertEqual(first['legend'], [
            {'label': '擦傷', 'class_index': 3, 'color': list(reversed(colors(3, True)))},
            {'label': '疑似炭疽病', 'class_index': 0, 'color': list(reversed(colors(0, True)))},
        ])
        with Image.open(preview['image_path']) as image:
            # Each expected color must occupy a visible swatch in photo 1's caption.
            with image.crop((0, 0, 640, image.height - 480)) as caption:
                for entry in first['legend']:
                    count = sum(all(abs(value - expected) < 15
                                    for value, expected in zip(pixel, entry['color'], strict=True))
                                for pixel in caption.getdata())
                    self.assertGreater(count, 200)
            red, green, blue = image.getpixel((320, image.height - 240))
            self.assertLess(red, 10)
            self.assertGreater(green, 245)
            self.assertGreater(blue, 245)
        self.assertNotIn('0.87', preview['text'])
        self.assertNotIn('anthracnose', preview['text'])

    def test_invalid_class_index_is_rejected_before_opening_photos(self):
        result = self.processing_fixture()
        result['images']['img_01.jpg']['models']['defect']['detections'][0]['class_index'] = 'bad'
        self.write_fixture(result)
        with mock.patch('PIL.Image.open', wraps=Image.open) as opened:
            with self.assertRaisesRegex(ValueError, 'class_index'):
                generate_notification(self.result_path)
            opened.assert_not_called()

    def test_missing_result_and_all_images_return_structured_skips(self):
        self.assertEqual(generate_notification(self.result_path),
                         {'ok': False, 'reason': 'result_not_found'})
        result = self.processing_fixture()
        for image in result['images'].values():
            image['artifacts'] = {}
        self.write_fixture(result)
        before = set(self.root.iterdir())
        self.assertEqual(generate_notification(self.result_path),
                         {'ok': False, 'reason': 'no_images'})
        self.assertEqual(set(self.root.iterdir()), before)

    def test_cjk_font_renders_distinct_readable_glyphs_and_uncropped_captions(self):
        result = self.processing_fixture()
        first = result['images']['img_01.jpg']['models']
        first['color']['class_name'] = 'bad_color'
        first['wrinkle']['display_label'] = '有皺褶（壞）'
        first['defect']['detections'] = [{'class_name': name, 'class_index': index}
                                        for index, name in enumerate((
            'abrasion', 'anthracnose', 'insect_bite', 'insect_track'))]
        self.write_fixture(result)
        preview = generate_notification(self.result_path)
        font = ImageFont.truetype(preview['font_path'], 28)
        missing = bytes(font.getmask('\u0378'))
        for char in '照片擦傷疑似炭疽病未完成檢測取得影像':
            with self.subTest(char=char):
                glyph = font.getmask(char)
                self.assertIsNotNone(glyph.getbbox())
                self.assertNotEqual(bytes(glyph), missing)
        self.assertNotEqual(bytes(font.getmask('擦')), bytes(font.getmask('傷')))
        with Image.open(preview['image_path']) as image:
            header_height = image.height - 480
            for index in range(3):
                with image.crop((index * 640, 0, (index + 1) * 640, header_height)).convert('L') as caption:
                    pixels = caption.load()
                    dark = [(x, y) for y in range(caption.height)
                            for x in range(caption.width) if pixels[x, y] < 100]
                    self.assertGreater(len(dark), 100)
                    self.assertGreater(min(x for x, _ in dark), 0)
                    self.assertLess(max(x for x, _ in dark), caption.width - 1)
                    self.assertGreater(min(y for _, y in dark), 0)
                    self.assertLess(max(y for _, y in dark), caption.height - 1)
            with Image.open(preview['preview_path']) as smaller:
                self.assertAlmostEqual(image.width / image.height,
                                       smaller.width / smaller.height, places=2)

    def test_invalid_paths_contracts_and_fonts_raise_explicit_errors(self):
        with self.assertRaises(FileNotFoundError):
            generate_notification(self.root / 'absent' / 'result.json')
        with self.assertRaisesRegex(ValueError, 'JSON 檔案'):
            generate_notification(self.root)
        result = self.processing_fixture()
        for field, value, message in (
            ('contract_version', 999, '契約版本'),
            ('result_id', '../unsafe', '穩定結果識別'),
            ('hardware_mode', 'unknown', '拍攝來源'),
        ):
            with self.subTest(field=field):
                self.write_fixture({**result, field: value})
                with self.assertRaisesRegex(ValueError, message):
                    generate_notification(self.result_path)
        self.write_fixture(result)
        with self.assertRaisesRegex(FileNotFoundError, '中文字型'):
            generate_notification(self.result_path, font_path=self.root / 'missing.ttf')
        with self.assertRaisesRegex(ValueError, '缺少中文字形'):
            generate_notification(self.result_path, font_path=Path('C:/Windows/Fonts/arial.ttf'))
        with self.assertRaises(NotADirectoryError):
            generate_notification(self.result_path, output_dir=self.result_path)
        result['images']['img_01.jpg']['artifacts']['defect_annotated'] = '../outside.jpg'
        self.write_fixture(result)
        with self.assertRaisesRegex(ValueError, '影像檔名'):
            generate_notification(self.result_path)

    def test_unsaved_final_classification_does_not_notify(self):
        result = self.processing_fixture()
        result['storage']['status'] = 'failed'
        self.write_fixture(result)
        self.assertEqual(generate_notification(self.result_path),
                         {'ok': False, 'reason': 'result_not_saved'})

    def test_management_command_writes_preview_in_explicit_directory_without_network(self):
        result = self.manual_fixture()
        self.write_fixture(result)
        original = self.result_path.read_bytes()
        output = self.root / 'previews'
        stdout = StringIO()
        with mock.patch('socket.socket.connect', side_effect=AssertionError('No network')):
            call_command('preview_processing_notification', str(self.result_path),
                         output_dir=str(output), stdout=stdout)
        report = json.loads(stdout.getvalue())
        self.assertTrue(report['ok'])
        self.assertEqual(len(list(output.iterdir())), 3)
        for key in ('text_path', 'image_path', 'preview_path'):
            path = Path(report[key])
            self.assertEqual(path.parent, output)
            self.assertTrue(path.is_file())
        self.assertEqual(self.result_path.read_bytes(), original)
        stdout = StringIO()
        call_command('preview_processing_notification', str(self.root / 'absent.json'),
                     stdout=stdout)
        self.assertEqual(json.loads(stdout.getvalue()),
                         {'ok': False, 'reason': 'result_not_found'})

    def test_malformed_nested_result_fields_raise_descriptive_value_errors(self):
        result = self.processing_fixture()
        result['storage'] = 'saved'
        self.write_fixture(result)
        with self.assertRaisesRegex(ValueError, 'storage'):
            generate_notification(self.result_path)
        for field in ('roi', 'models', 'artifacts'):
            result = self.processing_fixture()
            result['images']['img_01.jpg'][field] = 'invalid'
            self.write_fixture(result)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                generate_notification(self.result_path)
        result = self.processing_fixture()
        result['images']['img_01.jpg']['models']['defect']['detections'] = ['invalid']
        self.write_fixture(result)
        with self.assertRaisesRegex(ValueError, 'detections'):
            generate_notification(self.result_path)
