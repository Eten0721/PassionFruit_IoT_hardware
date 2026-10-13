"""Two-stage XGBoost view grading and worst-view fruit classification."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np


RAW_FEATURES = [
    'fruit_area_ratio',
    'color_bad',
    'color_confidence',
    'wrinkle',
    'wrinkle_confidence',
    'abrasion_area_ratio',
    'abrasion_max_confidence',
    'anthracnose_area_ratio',
    'anthracnose_max_confidence',
    'insect_bite_area_ratio',
    'insect_bite_max_confidence',
    'insect_track_area_ratio',
    'insect_track_max_confidence',
]
AREA_FEATURES = [
    'abrasion_area_ratio',
    'anthracnose_area_ratio',
    'insect_bite_area_ratio',
    'insect_track_area_ratio',
]
MODEL_FEATURES = RAW_FEATURES + [
    'local_defect_area_sum',
    'local_defect_area_max',
    'local_defect_type_count',
]
BINARY_FEATURES = {'color_bad', 'wrinkle'}
STAGE1_THRESHOLD = 0.5
# Index is the delivered grade: 0 is the worst, 3 the best.
GRADES = (('加工', 'processing'), ('下等', 'low'),
          ('中等', 'medium'), ('上等', 'high'))
CLASSIFICATION_CODES = dict(GRADES)


@dataclass(frozen=True)
class GradeModels:
    stage1: object
    stage2: object
    feature_names: list[str]
    versions: dict


def grade_label(grade: int) -> tuple[str, str]:
    return GRADES[grade]


def combine(p_nonzero: float, stage2_probabilities) -> int:
    """Stage 1 routes processing fruit; stage 2 classes 0/1/2 are grades 1/2/3."""
    if p_nonzero < STAGE1_THRESHOLD:
        return 0
    return int(np.argmax(stage2_probabilities)) + 1


def load_models(paths: dict[str, str]) -> GradeModels:
    resolved = {}
    for stage in ('stage1', 'stage2'):
        path = Path(paths.get(stage) or '').expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'找不到 XGBoost {stage} 模型：{path}')
        resolved[stage] = path
    stat = {stage: path.stat() for stage, path in resolved.items()}
    return _load_models(
        str(resolved['stage1']), stat['stage1'].st_mtime_ns,
        str(resolved['stage2']), stat['stage2'].st_mtime_ns,
    )


@lru_cache(maxsize=2)
def _load_models(stage1_path, _stage1_mtime, stage2_path, _stage2_mtime):
    import xgboost

    boosters = {}
    versions = {'xgboost': xgboost.__version__,
                'stage1_threshold': STAGE1_THRESHOLD}
    for stage, path in (('stage1', stage1_path), ('stage2', stage2_path)):
        booster = xgboost.Booster()
        booster.load_model(path)
        if list(booster.feature_names or []) != MODEL_FEATURES:
            raise ValueError(
                f'XGBoost {stage} 模型特徵名稱或順序與部署契約不符。'
            )
        boosters[stage] = booster
        versions[stage] = {
            'file': Path(path).name,
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        }
    return GradeModels(boosters['stage1'], boosters['stage2'],
                       list(MODEL_FEATURES), versions)


def _model_row(raw: dict) -> dict | str:
    """Return the 16 ordered model features, or the first invalid feature."""
    features = {}
    for name in RAW_FEATURES:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f'feature_invalid:{name}'
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return f'feature_invalid:{name}'
        if name in BINARY_FEATURES and value not in (0.0, 1.0):
            return f'feature_invalid:{name}'
        features[name] = int(value) if name in BINARY_FEATURES else value
    areas = [features[name] for name in AREA_FEATURES]
    features['local_defect_area_sum'] = sum(areas)
    features['local_defect_area_max'] = max(areas)
    features['local_defect_type_count'] = sum(area > 0 for area in areas)
    return features


def unavailable_view(reason: str) -> dict:
    return {'status': 'unavailable', 'reason': reason,
            'features': None, 'grade': None}


def needs_review(reason: str) -> dict:
    return {
        'status': 'needs_review', 'grade': None, 'label': None, 'code': None,
        'source': None, 'worst_view': None, 'reason': reason,
    }


def grade_views(views: dict[str, dict], model_paths: dict[str, str]) -> dict:
    """Grade each view and classify the fruit by its worst view.

    ``views`` maps image names to raw features, or to
    ``{'unavailable': reason}`` when detection could not produce them.
    """
    models = load_models(model_paths)
    graded = {}
    rows = {}
    for name, raw in views.items():
        if 'unavailable' in raw:
            graded[name] = unavailable_view(raw['unavailable'])
            continue
        row = _model_row(raw)
        if isinstance(row, str):
            graded[name] = unavailable_view(row)
            continue
        rows[name] = row
    if rows:
        import xgboost

        matrix = xgboost.DMatrix(
            np.array([[row[feature] for feature in MODEL_FEATURES]
                      for row in rows.values()], dtype=float),
            feature_names=MODEL_FEATURES,
        )
        p_nonzero = models.stage1.predict(matrix)
        stage2 = models.stage2.predict(matrix)
        for index, (name, row) in enumerate(rows.items()):
            grade = combine(float(p_nonzero[index]), stage2[index])
            label, code = grade_label(grade)
            graded[name] = {
                'status': 'ok', 'reason': None, 'features': row,
                'stage1_p_nonzero': round(float(p_nonzero[index]), 6),
                'stage2_probabilities': (
                    [round(float(value), 6) for value in stage2[index]]
                    if grade else None
                ),
                'grade': grade, 'label': label, 'code': code,
            }
    graded = {name: graded[name] for name in views}
    return {
        'views': graded,
        'classification': _classify(graded),
        'model': models.versions,
    }


def _classify(graded: dict[str, dict]) -> dict:
    if not graded or any(view['status'] != 'ok' for view in graded.values()):
        return needs_review('view_features_unavailable')
    worst_view = min(graded, key=lambda name: graded[name]['grade'])
    grade = graded[worst_view]['grade']
    label, code = grade_label(grade)
    return {
        'status': 'graded', 'grade': grade, 'label': label, 'code': code,
        'source': 'model', 'worst_view': worst_view, 'reason': None,
    }
