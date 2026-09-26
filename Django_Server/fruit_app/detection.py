"""Adapter between Django capture sessions and the model repository."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ARTIFACT_NAMES = (
    'roi_annotated',
    'masked_roi',
    'wrinkle_gray',
    'defect_annotated',
)
MODEL_ROLES = ('roi', 'color', 'wrinkle', 'defect')
DEFECT_CLASSES = {'abrasion', 'anthracnose', 'insect_bite', 'insect_track'}


def _model_modules(repository_root: Path):
    root = str(Path(repository_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    pipeline = importlib.import_module('passionfruit.inference.pipeline')
    manager_module = importlib.import_module(
        'passionfruit.inference.model_manager'
    )
    image_processing = importlib.import_module(
        'passionfruit.inference.image_processing'
    )
    return pipeline, manager_module, image_processing


def _class_names(model) -> set[str]:
    names = getattr(model, 'names', {})
    values = names.values() if isinstance(names, dict) else names
    return {str(value) for value in values}


def _validate_class_semantics(role: str, names: set[str]) -> None:
    if role == 'roi' and len(names) != 1:
        raise ValueError('ROI 模型必須只有一個果實類別。')
    if role == 'color' and names != {'good_color', 'bad_color'}:
        raise ValueError(
            'color 模型類別必須是 good_color 與 bad_color。'
        )
    if role == 'wrinkle':
        mapped = {_wrinkle_label(name) for name in names}
        if mapped != {'無皺褶（好）', '有皺褶（壞）'}:
            raise ValueError('wrinkle 模型類別語意無法對應皺褶好壞。')
    if role == 'defect' and names != DEFECT_CLASSES:
        raise ValueError('defect 模型類別與四類局部瑕疵契約不符。')


def _wrinkle_label(class_name: str) -> str | None:
    normalized = class_name.casefold().replace('-', '_').replace(' ', '_')
    good_markers = ('沒皺褶', '無皺褶', 'smooth', 'no_wrinkle', 'false')
    bad_markers = ('有皺褶', 'wrinkle', 'true')
    if any(marker in normalized for marker in good_markers):
        return '無皺褶（好）'
    if any(marker in normalized for marker in bad_markers):
        return '有皺褶（壞）'
    return None


def execute_model_batch(
    image_paths: list[Path],
    model_paths: dict[str, str],
    *,
    repository_root: Path,
    model_options: dict[str, dict] | None = None,
):
    """Call the existing batch pipeline after validating actual weights."""
    pipeline, manager_module, _ = _model_modules(repository_root)
    manager = manager_module.PassionFruitModelManager()
    for role in MODEL_ROLES:
        path = model_paths.get(role, '')
        if not path:
            raise FileNotFoundError(f'未設定 {role} 模型路徑。')
        model = manager.load(role, path)
        _validate_class_semantics(role, _class_names(model))
    return pipeline.run_pipeline_batch(
        image_paths,
        model_paths,
        manager=manager,
        model_options=model_options,
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    staging = path.with_suffix(path.suffix + '.tmp')
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _copy_original(source: Path, target: Path) -> None:
    staging = target.with_suffix(target.suffix + '.tmp')
    try:
        shutil.copyfile(source, staging)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)


def _write_artifact(path: Path, image: np.ndarray) -> None:
    success, encoded = cv2.imencode(path.suffix, image)
    if not success:
        raise ValueError(f'圖片編碼失敗：{path.name}')
    staging = path.with_suffix(path.suffix + '.tmp')
    try:
        encoded.tofile(staging)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _display_models(models: dict[str, dict]) -> dict[str, dict]:
    displayed = {role: dict(models.get(role) or {}) for role in MODEL_ROLES[1:]}
    color = displayed['color']
    if color.get('status') == 'ok':
        color['display_label'] = {
            'good_color': '好',
            'bad_color': '壞',
        }.get(color.get('class_name'))
        if color['display_label'] is None:
            color.update(status='error', reason='color_class_unknown')

    wrinkle = displayed['wrinkle']
    if wrinkle.get('status') == 'ok':
        wrinkle['display_label'] = _wrinkle_label(
            str(wrinkle.get('class_name', ''))
        )
        if wrinkle['display_label'] is None:
            wrinkle.update(status='error', reason='wrinkle_class_unknown')

    defect = displayed['defect']
    if defect.get('status') == 'ok':
        detections = defect.get('detections') or []
        defect['display_label'] = (
            '未偵測到局部瑕疵' if not detections else '偵測到局部瑕疵'
        )
    return displayed


def _image_complete(image: dict) -> bool:
    if image['roi'].get('status') != 'ok':
        return False
    if any(
        image['models'][role].get('status') != 'ok'
        for role in MODEL_ROLES[1:]
    ):
        return False
    return all(name in image['artifacts'] for name in ARTIFACT_NAMES)


def run_and_save(
    *,
    fruit_id: str,
    image_paths: list[Path],
    output_root: Path,
    repository_root: Path,
    model_paths: dict[str, str],
    model_options: dict[str, dict] | None = None,
) -> dict:
    """Run one three-image batch and save one durable fruit-level result."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / fruit_id
    if run_dir.exists():
        raise FileExistsError(f'檢測輸出已存在：{run_dir}')
    run_dir.mkdir()

    images: dict[str, dict] = {}
    for source in image_paths:
        source = Path(source)
        target_name = f'{source.stem}_original{source.suffix.lower()}'
        _copy_original(source, run_dir / target_name)
        images[source.name] = {
            'roi': {'status': 'pending', 'reason': None},
            'models': {},
            'artifacts': {'original': target_name},
        }

    try:
        reports, artifacts_list = execute_model_batch(
            image_paths,
            model_paths,
            repository_root=repository_root,
            model_options=model_options,
        )
        if len(reports) != len(image_paths) or len(artifacts_list) != len(image_paths):
            raise RuntimeError('批次推論結果數量與三張輸入不一致。')
    except Exception as error:
        reason = f'{type(error).__name__}: {error}'
        reports = [
            {
                'roi': {'status': 'error', 'reason': reason},
                'models': {
                    role: {'status': 'skipped', 'reason': 'batch_unavailable'}
                    for role in MODEL_ROLES[1:]
                },
            }
            for _ in image_paths
        ]
        artifacts_list = [{} for _ in image_paths]

    save_errors = []
    for source, report, artifacts in zip(
        image_paths, reports, artifacts_list, strict=True
    ):
        source = Path(source)
        image = images[source.name]
        image['roi'] = dict(report.get('roi') or {
            'status': 'error', 'reason': 'roi_result_missing',
        })
        image['models'] = _display_models(report.get('models') or {})
        for name in ARTIFACT_NAMES:
            artifact = artifacts.get(name)
            if artifact is None:
                continue
            relative = f'{source.stem}_{name}.jpg'
            try:
                _write_artifact(run_dir / relative, artifact)
                image['artifacts'][name] = relative
            except Exception as error:
                save_errors.append(
                    f'{source.name}/{name}: {type(error).__name__}: {error}'
                )

    complete = not save_errors and all(
        _image_complete(image) for image in images.values()
    )
    result = {
        'fruit_id': fruit_id,
        'status': 'completed' if complete else 'failed',
        'complete': complete,
        'reason': None if complete else (
            'artifact_save_failed' if save_errors else 'detection_incomplete'
        ),
        'save_errors': save_errors,
        'images': images,
    }
    _atomic_json(run_dir / 'result.json', result)
    _atomic_json(root / '.current.json', {
        'fruit_id': fruit_id,
        'result': f'{fruit_id}/result.json',
    })
    return result


def load_result(output_root: Path, fruit_id: str) -> dict | None:
    try:
        return json.loads(
            (Path(output_root) / fruit_id / 'result.json').read_text(
                encoding='utf-8'
            )
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
