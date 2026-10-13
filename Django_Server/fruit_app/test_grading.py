"""Two-stage XGBoost grading contract against the delivered models."""

import math

import numpy as np
import xgboost
from django.conf import settings
from django.test import SimpleTestCase

from . import grading


def _row(**overrides):
    row = {
        'fruit_area_ratio': 0.25,
        'color_bad': 0, 'color_confidence': 0.95,
        'wrinkle': 0, 'wrinkle_confidence': 0.9,
        'abrasion_area_ratio': 0.0, 'abrasion_max_confidence': 0.0,
        'anthracnose_area_ratio': 0.0, 'anthracnose_max_confidence': 0.0,
        'insect_bite_area_ratio': 0.0, 'insect_bite_max_confidence': 0.0,
        'insect_track_area_ratio': 0.0, 'insect_track_max_confidence': 0.0,
    }
    row.update(overrides)
    return row


SAMPLE_ROWS = [
    _row(),
    _row(color_bad=1, color_confidence=0.8, wrinkle=1, wrinkle_confidence=0.7),
    _row(abrasion_area_ratio=0.2, abrasion_max_confidence=0.8),
    _row(anthracnose_area_ratio=0.12, anthracnose_max_confidence=0.9,
         insect_track_area_ratio=0.05, insect_track_max_confidence=0.6),
    _row(fruit_area_ratio=0.4, insect_bite_area_ratio=0.3,
         insect_bite_max_confidence=0.95, color_bad=1),
    _row(fruit_area_ratio=0.1, anthracnose_area_ratio=0.3,
         anthracnose_max_confidence=0.85),
    _row(fruit_area_ratio=0.46, color_confidence=0.59, wrinkle=1,
         wrinkle_confidence=0.56, abrasion_area_ratio=0.144,
         abrasion_max_confidence=0.8),
]


def _reference_grades(rows):
    """Teammate's delivered example, with Booster in place of XGBClassifier."""
    x = np.array([[row[name] for name in grading.RAW_FEATURES] for row in rows],
                 dtype=float)
    areas = x[:, [grading.RAW_FEATURES.index(name) for name in grading.AREA_FEATURES]]
    x = np.column_stack([x, areas.sum(axis=1), areas.max(axis=1),
                         (areas > 0).sum(axis=1)])
    paths = settings.GRADE_MODEL_PATHS
    stage1 = xgboost.Booster()
    stage1.load_model(paths['stage1'])
    stage2 = xgboost.Booster()
    stage2.load_model(paths['stage2'])
    matrix = xgboost.DMatrix(x, feature_names=stage1.feature_names)
    p_nonzero = stage1.predict(matrix)
    grades = np.zeros(len(rows), dtype=int)
    nonzero = np.flatnonzero(p_nonzero >= 0.5)
    if len(nonzero):
        grades[nonzero] = stage2.predict(matrix.slice(nonzero)).argmax(axis=1) + 1
    return grades.tolist(), p_nonzero.tolist()


class GradeDecisionTests(SimpleTestCase):
    def test_stage1_threshold_is_inclusive_at_half(self):
        self.assertEqual(grading.combine(0.4999, [0.1, 0.2, 0.7]), 0)
        self.assertEqual(grading.combine(0.5, [0.1, 0.2, 0.7]), 3)

    def test_stage2_classes_map_to_low_medium_high(self):
        self.assertEqual(grading.combine(0.9, [0.6, 0.3, 0.1]), 1)
        self.assertEqual(grading.combine(0.9, [0.2, 0.7, 0.1]), 2)
        self.assertEqual(grading.combine(0.9, [0.2, 0.1, 0.7]), 3)

    def test_grade_labels_follow_canonical_four_class_contract(self):
        self.assertEqual(
            [grading.grade_label(grade) for grade in range(4)],
            [('加工', 'processing'), ('下等', 'low'),
             ('中等', 'medium'), ('上等', 'high')],
        )


class GradeViewsTests(SimpleTestCase):
    def setUp(self):
        self.paths = dict(settings.GRADE_MODEL_PATHS)

    def test_deployed_models_match_feature_contract(self):
        models = grading.load_models(self.paths)
        self.assertEqual(models.feature_names, grading.MODEL_FEATURES)
        self.assertEqual(len(grading.MODEL_FEATURES), 16)
        self.assertIn('color_confidence', grading.MODEL_FEATURES)
        self.assertEqual(len(models.versions['stage1']['sha256']), 64)

    def test_matches_teammate_reference_on_fixed_rows(self):
        expected_grades, expected_p = _reference_grades(SAMPLE_ROWS)
        views = {f'img_{index:02d}.jpg': row
                 for index, row in enumerate(SAMPLE_ROWS[:3], start=1)}
        result = grading.grade_views(views, self.paths)
        for index, name in enumerate(views):
            view = result['views'][name]
            self.assertEqual(view['grade'], expected_grades[index])
            self.assertTrue(math.isclose(
                view['stage1_p_nonzero'], expected_p[index], rel_tol=1e-6,
            ))
            self.assertEqual(list(view['features']), grading.MODEL_FEATURES)
        rest = grading.grade_views(
            {f'v{index}': row for index, row in enumerate(SAMPLE_ROWS)},
            self.paths,
        )
        self.assertEqual(
            [view['grade'] for view in rest['views'].values()],
            expected_grades,
        )
        self.assertEqual(set(expected_grades), {0, 1, 2, 3})

    def test_derived_area_features_are_computed_from_four_ratios(self):
        result = grading.grade_views({'img_01.jpg': SAMPLE_ROWS[3]}, self.paths)
        features = result['views']['img_01.jpg']['features']
        self.assertAlmostEqual(features['local_defect_area_sum'], 0.17)
        self.assertEqual(features['local_defect_area_max'], 0.12)
        self.assertEqual(features['local_defect_type_count'], 2)

    def test_fruit_grade_is_worst_view_not_average_or_majority(self):
        grades, _ = _reference_grades(SAMPLE_ROWS)
        views = {f'v{index}': row for index, row in enumerate(SAMPLE_ROWS)}
        classification = grading.grade_views(views, self.paths)['classification']
        self.assertEqual(classification['status'], 'graded')
        self.assertEqual(classification['grade'], min(grades))
        self.assertEqual(classification['source'], 'model')
        self.assertEqual(
            classification['worst_view'], f'v{grades.index(min(grades))}',
        )
        label, code = grading.grade_label(min(grades))
        self.assertEqual(classification['label'], label)
        self.assertEqual(classification['code'], code)

    def test_missing_view_needs_review_and_keeps_other_predictions(self):
        result = grading.grade_views({
            'img_01.jpg': SAMPLE_ROWS[0],
            'img_02.jpg': {'unavailable': 'roi_not_detected'},
            'img_03.jpg': SAMPLE_ROWS[1],
        }, self.paths)
        classification = result['classification']
        self.assertEqual(classification['status'], 'needs_review')
        self.assertIsNone(classification['grade'])
        self.assertIsNone(classification['source'])
        self.assertEqual(classification['reason'], 'view_features_unavailable')
        self.assertEqual(result['views']['img_02.jpg'], {
            'status': 'unavailable', 'reason': 'roi_not_detected',
            'features': None, 'grade': None,
        })
        self.assertEqual(result['views']['img_01.jpg']['status'], 'ok')

    def test_invalid_feature_values_are_never_graded(self):
        for bad in (None, float('nan'), -0.1, 1.5, 'x'):
            with self.subTest(bad=bad):
                result = grading.grade_views({
                    'img_01.jpg': _row(abrasion_area_ratio=bad),
                }, self.paths)
                view = result['views']['img_01.jpg']
                self.assertEqual(view['status'], 'unavailable')
                self.assertEqual(
                    view['reason'], 'feature_invalid:abrasion_area_ratio',
                )
                self.assertEqual(
                    result['classification']['status'], 'needs_review',
                )

    def test_missing_model_file_raises_explicit_error(self):
        with self.assertRaises(FileNotFoundError):
            grading.grade_views(
                {'img_01.jpg': SAMPLE_ROWS[0]},
                {'stage1': self.paths['stage1'], 'stage2': 'missing.json'},
            )
