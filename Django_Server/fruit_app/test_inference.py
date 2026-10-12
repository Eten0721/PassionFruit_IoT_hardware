"""Production inference runs with only local weights and installed packages."""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np
import torch
from django.test import SimpleTestCase

from . import detection


class StandaloneInferenceTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.paths = []
        for index in range(3):
            path = root / f'img_{index + 1:02d}.jpg'
            image = np.full((24, 32, 3), 128, dtype=np.uint8)
            cv2.imencode('.jpg', image)[1].tofile(path)
            self.paths.append(path)
        self.weights = {
            role: str(root / f'{role}.pt') for role in detection.MODEL_ROLES
        }
        for path in self.weights.values():
            Path(path).touch()
        self.models = {}
        names = {
            'roi': {0: 'fruit'},
            'color': {0: 'good_color', 1: 'bad_color'},
            'wrinkle': {0: 'false(沒皺褶)', 1: 'true(有皺褶)'},
            'defect': dict(enumerate(sorted(detection.DEFECT_CLASSES))),
        }
        for role in detection.MODEL_ROLES:
            model = mock.Mock()
            model.names = names[role]
            model.task = {'roi': 'detect', 'color': 'classify',
                          'wrinkle': 'classify', 'defect': 'segment'}[role]
            model.predict.side_effect = (
                lambda source, role=role, **_: [
                    self._result(role, image) for image in source
                ]
            )
            self.models[role] = model
        self.loader = mock.patch(
            'ultralytics.YOLO',
            side_effect=lambda path: self.models[Path(path).stem],
        ).start()
        self.addCleanup(mock.patch.stopall)

    def _result(self, role, image):
        if role == 'roi':
            boxes = mock.MagicMock()
            boxes.__len__.return_value = 2
            boxes.conf = torch.tensor([0.5, 0.9])
            boxes.xyxy = torch.tensor([[0, 0, 32, 24], [4, 3, 28, 21]])
            return SimpleNamespace(boxes=boxes)
        if role in ('color', 'wrinkle'):
            return SimpleNamespace(
                names=self.models[role].names,
                probs=SimpleNamespace(top1=0, top1conf=torch.tensor(0.9)),
            )
        return SimpleNamespace(
            boxes=None, masks=None, names=self.models[role].names,
            orig_shape=image.shape[:2], plot=lambda: image.copy(),
        )

    def test_batch_needs_no_external_repository_or_import_path_changes(self):
        before = list(sys.path)
        reports, artifacts = detection.execute_model_batch(
            self.paths, self.weights,
            model_options={'roi': {'confidence': 0.25, 'imgsz': 640}},
        )
        self.assertEqual(sys.path, before)
        self.assertEqual(len(reports), 3)
        self.assertTrue(all(report['roi']['status'] == 'ok' for report in reports))
        self.assertEqual(reports[0]['roi']['crop_bbox'], [4, 3, 28, 21])
        self.assertEqual(artifacts[0]['masked_roi'].shape, (18, 24, 3))
        self.assertEqual(artifacts[0]['wrinkle_gray'].shape, (18, 24))
        self.assertEqual(artifacts[0]['masked_roi'][0, 0].tolist(), [0, 0, 0])
        roi_call = self.models['roi'].predict.call_args.kwargs
        self.assertEqual(roi_call['conf'], 0.25)
        self.assertEqual(roi_call['imgsz'], 640)
        for role in detection.MODEL_ROLES:
            self.models[role].predict.assert_called_once()
        self.assertEqual(self.loader.call_count, 4)
        self.assertEqual(reports[0]['models']['defect']['area_ratios']['abrasion'], 0)

    def test_missing_weight_does_not_download_or_load_a_model(self):
        Path(self.weights['defect']).unlink()
        with self.assertRaises(FileNotFoundError):
            detection.execute_model_batch(self.paths, self.weights)
        self.loader.assert_not_called()

    def test_replacement_weight_must_match_role_and_class_semantics(self):
        for invalid in ('task', 'names'):
            with self.subTest(invalid=invalid):
                model = self.models['color']
                previous = getattr(model, invalid)
                setattr(model, invalid, 'detect' if invalid == 'task' else {0: 'unknown'})
                with self.assertRaises(ValueError):
                    detection.execute_model_batch(self.paths, self.weights)
                setattr(model, invalid, previous)

    def test_roi_failure_keeps_other_images_in_their_original_positions(self):
        results = [self._result('roi', np.zeros((24, 32, 3))) for _ in range(3)]
        results[1].boxes = None
        self.models['roi'].predict.side_effect = None
        self.models['roi'].predict.return_value = results
        reports, artifacts = detection.execute_model_batch(self.paths, self.weights)
        self.assertEqual(reports[1]['roi']['reason'], 'roi_not_detected')
        self.assertTrue(all(item['status'] == 'skipped' for item in reports[1]['models'].values()))
        self.assertNotIn('masked_roi', artifacts[1])
        self.assertEqual(reports[2]['models']['color']['class_name'], 'good_color')
        self.assertEqual(len(self.models['color'].predict.call_args.kwargs['source']), 2)

    def test_overlapping_defect_masks_use_union_within_the_roi(self):
        boxes = mock.MagicMock()
        boxes.__len__.return_value = 2
        boxes.cls = torch.tensor([0, 0])
        boxes.conf = torch.tensor([0.7, 0.9])
        model = self.models['defect']
        model.predict.side_effect = lambda source, **_: [
            SimpleNamespace(
                boxes=boxes, names=model.names, orig_shape=image.shape[:2],
                masks=SimpleNamespace(data=torch.ones((2, *image.shape[:2]))),
                plot=lambda: image.copy(),
            ) for image in source
        ]
        reports, _ = detection.execute_model_batch(self.paths, self.weights)
        defect = reports[0]['models']['defect']
        self.assertEqual(defect['area_ratios']['abrasion'], 1.0)
        self.assertEqual(len(defect['detections']), 1)
        self.assertAlmostEqual(defect['max_confidences']['abrasion'], 0.9)

    def test_failed_model_does_not_discard_successful_models(self):
        self.models['defect'].predict.side_effect = RuntimeError('inference failed')
        reports, artifacts = detection.execute_model_batch(self.paths, self.weights)
        self.assertEqual(reports[0]['models']['defect']['status'], 'error')
        self.assertIn('inference failed', reports[0]['models']['defect']['reason'])
        self.assertEqual(reports[0]['models']['color']['status'], 'ok')
        self.assertIn('wrinkle_gray', artifacts[0])
        self.assertNotIn('defect_annotated', artifacts[0])
