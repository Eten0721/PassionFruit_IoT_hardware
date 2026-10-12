"""Local four-model inference and durable capture-session results."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from time import perf_counter
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


def _predict_batch(model, role, images, device, model_options):
    if not images:
        return [], 0.0
    options = {'device': device, 'verbose': False}
    settings = (model_options or {}).get(role, {})
    confidence = settings.get('confidence', 0.30 if role == 'roi' else None)
    if confidence is not None:
        options['conf'] = float(confidence)
    if settings.get('imgsz') is not None:
        options['imgsz'] = int(settings['imgsz'])
    started = perf_counter()
    results = model.predict(source=images, **options)
    if len(results) != len(images):
        raise RuntimeError(
            f'{role} batch 推論回傳數量（{len(results)}）與輸入數量'
            f'（{len(images)}）不一致。'
        )
    return results, round((perf_counter() - started) * 1000 / len(images), 2)


def _prepare_roi(image, result, elapsed_ms):
    boxes = getattr(result, 'boxes', None)
    if boxes is None or not len(boxes):
        return {
            'status': 'error', 'reason': 'roi_not_detected',
            'elapsed_ms': elapsed_ms,
        }, {}
    confidences = boxes.conf.detach().cpu().numpy().reshape(-1)
    best = int(np.argmax(confidences))
    bbox = boxes.xyxy[best].detach().cpu().numpy().reshape(-1)
    if bbox.size != 4 or not np.isfinite(bbox).all():
        raise ValueError('ROI 模型回傳的 bbox 格式不正確。')
    height, width = image.shape[:2]
    dx1, dy1, dx2, dy2 = map(float, bbox)
    x1, x2 = (int(round(np.clip(value, 0, width))) for value in sorted((dx1, dx2)))
    y1, y2 = (int(round(np.clip(value, 0, height))) for value in sorted((dy1, dy2)))
    if x2 <= x1 or y2 <= y1:
        raise ValueError('ROI bbox 經邊界修正後沒有有效面積。')
    crop = image[y1:y2, x1:x2]
    crop_height, crop_width = crop.shape[:2]
    mask = np.zeros((crop_height, crop_width), dtype=np.uint8)
    cv2.ellipse(
        mask, (crop_width // 2, crop_height // 2),
        (max(1, int(crop_width / 2 * 1.01)),
         max(1, int(crop_height / 2 * 1.01))),
        0, 0, 360, 255, -1,
    )
    masked = cv2.bitwise_and(crop, crop, mask=mask)
    roi = {
        'bbox': [round(value, 2) for value in (dx1, dy1, dx2, dy2)],
        'crop_bbox': [x1, y1, x2, y2],
        'confidence': round(float(confidences[best]), 6),
        'roi_area_ratio': round(
            max(0.0, dx2 - dx1) * max(0.0, dy2 - dy1) / (width * height), 8,
        ),
        'status': 'ok', 'reason': None, 'elapsed_ms': elapsed_ms,
    }
    annotated = image.copy()
    ax1, ay1, ax2, ay2 = (int(round(value)) for value in roi['bbox'])
    cv2.rectangle(annotated, (ax1, ay1), (ax2, ay2), (0, 220, 0), 3)
    cv2.putText(
        annotated, f"PF {roi['confidence']:.1%}",
        (max(0, ax1), max(25, ay1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
        0.8, (0, 220, 0), 2, cv2.LINE_AA,
    )
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
        cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
    )
    return roi, {
        'roi_annotated': annotated, 'masked_roi': masked,
        'roi_mask': mask, 'wrinkle_gray': gray,
    }


def _classification_summary(result, elapsed_ms):
    probabilities = getattr(result, 'probs', None)
    if probabilities is None:
        raise ValueError('分類模型沒有回傳 probabilities。')
    index = int(probabilities.top1)
    return {
        'status': 'ok', 'class_name': str(result.names[index]),
        'class_index': index,
        'confidence': round(float(probabilities.top1conf.item()), 6),
        'elapsed_ms': elapsed_ms, 'reason': None,
    }


def _segmentation_summary(result, elapsed_ms, roi_mask):
    boxes = getattr(result, 'boxes', None)
    masks = getattr(result, 'masks', None)
    area_ratios = {name: 0.0 for name in sorted(DEFECT_CLASSES)}
    max_confidences = dict(area_ratios)
    detections = []
    if boxes is not None and len(boxes):
        if masks is None or getattr(masks, 'data', None) is None:
            raise ValueError('局部瑕疵模型有偵測結果，但沒有回傳 segmentation masks。')
        classes = boxes.cls.detach().cpu().numpy().astype(int)
        confidences = boxes.conf.detach().cpu().numpy()
        mask_data = masks.data.detach().cpu().numpy()
        if len(mask_data) != len(classes) or len(confidences) != len(classes):
            raise ValueError('局部瑕疵模型回傳的 boxes 與 masks 數量不一致。')
        denominator = int(np.count_nonzero(roi_mask))
        if denominator <= 0:
            raise ValueError('果實 ROI 面積為 0，無法計算局部瑕疵比例。')
        union_masks = {
            name: np.zeros(roi_mask.shape, dtype=bool) for name in area_ratios
        }
        class_indexes = {}
        for class_index, confidence, instance in zip(
            classes, confidences, mask_data, strict=True,
        ):
            class_index = int(class_index)
            name = str(result.names[class_index])
            if name not in union_masks:
                raise ValueError(f'未知的局部瑕疵類別：{name}')
            resized = cv2.resize(
                instance, (roi_mask.shape[1], roi_mask.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
            union_masks[name] |= (resized > 0.5) & (roi_mask > 0)
            class_indexes[name] = class_index
            max_confidences[name] = max(
                max_confidences[name], round(float(confidence), 6),
            )
        for name, union in union_masks.items():
            area_ratios[name] = round(
                min(1.0, np.count_nonzero(union) / denominator), 8,
            )
            if name in class_indexes:
                detections.append({
                    'class_name': name, 'class_index': class_indexes[name],
                    'confidence': max_confidences[name],
                    'mask_area_ratio': area_ratios[name],
                })
    return {
        'status': 'ok', 'detections': detections, 'area_ratios': area_ratios,
        'max_confidences': max_confidences, 'elapsed_ms': elapsed_ms,
        'reason': None,
    }


def execute_model_batch(
    image_paths: list[Path],
    model_paths: dict[str, str],
    *,
    model_options: dict[str, dict] | None = None,
):
    """Run the fixed deployment pipeline using installed YOLO and local weights."""
    paths = {}
    for role in MODEL_ROLES:
        path = model_paths.get(role, '')
        if not path:
            raise FileNotFoundError(f'未設定 {role} 模型路徑。')
        paths[role] = Path(path).expanduser().resolve()
        if not paths[role].is_file():
            raise FileNotFoundError(f'找不到 {role} 模型：{paths[role]}')

    import torch
    from ultralytics import YOLO

    device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    tasks = {'roi': 'detect', 'color': 'classify',
             'wrinkle': 'classify', 'defect': 'segment'}
    models = {}
    for role, path in paths.items():
        model = YOLO(str(path))
        actual_task = getattr(model, 'task', None)
        if actual_task != tasks[role]:
            raise ValueError(
                f"{role} 應為 {tasks[role]} 模型，目前權重是 {actual_task or 'unknown'}。"
            )
        _validate_class_semantics(role, _class_names(model))
        models[role] = model

    started = perf_counter()
    sources = [Path(path).resolve() for path in image_paths]
    source_images = []
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f'找不到圖片：{source}')
        image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f'無法讀取圖片：{source}')
        source_images.append(image)
    reports = [{
        'ok': False, 'source': str(source), 'mode': 'pipeline_batch',
        'device': device, 'model_paths': {role: str(path) for role, path in paths.items()},
        'parameters': {
            'confidence': 0.30, 'shrink_ratio': 0.0, 'mask_scale': 1.01,
            'clahe_clip_limit': 2.0, 'clahe_tile_size': 8,
        },
        'roi': None, 'models': {},
    } for source in sources]
    artifacts_list = [{'original': image} for image in source_images]
    ready_indices = []
    try:
        results, elapsed_ms = _predict_batch(
            models['roi'], 'roi', source_images, device, model_options,
        )
    except Exception as error:
        reason = f'{type(error).__name__}: {error}'
        for report in reports:
            report['roi'] = {'status': 'error', 'reason': reason}
    else:
        for index, (image, result) in enumerate(zip(source_images, results, strict=True)):
            try:
                roi, artifacts = _prepare_roi(image, result, elapsed_ms)
                reports[index]['roi'] = roi
                artifacts_list[index].update(artifacts)
                if roi['status'] == 'ok':
                    ready_indices.append(index)
            except Exception as error:
                reports[index]['roi'] = {
                    'status': 'error', 'reason': f'{type(error).__name__}: {error}',
                }
    for index, report in enumerate(reports):
        if index in ready_indices:
            continue
        report['reason'] = report['roi']['reason']
        report['models'] = {
            role: {'status': 'skipped', 'reason': 'roi_unavailable'}
            for role in MODEL_ROLES[1:]
        }
    for role in MODEL_ROLES[1:]:
        inputs = [
            cv2.cvtColor(artifacts_list[index]['wrinkle_gray'], cv2.COLOR_GRAY2BGR)
            if role == 'wrinkle' else artifacts_list[index]['masked_roi']
            for index in ready_indices
        ]
        try:
            results, elapsed_ms = _predict_batch(
                models[role], role, inputs, device, model_options,
            )
        except Exception as error:
            for index in ready_indices:
                reports[index]['models'][role] = {
                    'status': 'error', 'reason': f'{type(error).__name__}: {error}',
                }
            continue
        for index, result in zip(ready_indices, results, strict=True):
            try:
                if role == 'defect':
                    summary = _segmentation_summary(
                        result, elapsed_ms, artifacts_list[index]['roi_mask'],
                    )
                    artifacts_list[index]['defect_annotated'] = result.plot()
                else:
                    summary = _classification_summary(result, elapsed_ms)
                reports[index]['models'][role] = summary
            except Exception as error:
                reports[index]['models'][role] = {
                    'status': 'error', 'reason': f'{type(error).__name__}: {error}',
                }
    for report in reports:
        report['ok'] = report['roi']['status'] == 'ok' and all(
            summary['status'] == 'ok' for summary in report['models'].values()
        )
        if not report['ok'] and report.get('reason') is None:
            report['reason'] = 'model_inference_failed'
        report['elapsed_ms'] = round((perf_counter() - started) * 1000, 2)
    return reports, artifacts_list


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
    save_errors = []
    for source in image_paths:
        source = Path(source)
        target_name = f'{source.stem}_original{source.suffix.lower()}'
        images[source.name] = {
            'roi': {'status': 'pending', 'reason': None},
            'models': {},
            'artifacts': {},
        }
        try:
            _copy_original(source, run_dir / target_name)
            images[source.name]['artifacts']['original'] = target_name
        except Exception as error:
            save_errors.append(
                f'{source.name}/original: {type(error).__name__}: {error}'
            )

    try:
        reports, artifacts_list = execute_model_batch(
            image_paths,
            model_paths,
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
    return result


def interrupted_result(
    *, fruit_id: str, image_paths: list[Path], save_errors=None
) -> dict:
    return {
        'fruit_id': fruit_id,
        'status': 'failed',
        'complete': False,
        'reason': 'service_restarted',
        'save_errors': list(save_errors or []),
        'images': {
            Path(source).name: {
                'roi': {'status': 'error', 'reason': 'service_restarted'},
                'models': {
                    role: {'status': 'skipped', 'reason': 'service_restarted'}
                    for role in MODEL_ROLES[1:]
                },
                'artifacts': {},
            }
            for source in image_paths
        },
    }


def save_interrupted_result(
    *, fruit_id: str, image_paths: list[Path], output_root: Path
) -> dict:
    """Persist a restart terminal state without re-running any model."""
    run_dir = Path(output_root).resolve() / fruit_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result = interrupted_result(fruit_id=fruit_id, image_paths=image_paths)
    for source in map(Path, image_paths):
        artifacts = result['images'][source.name]['artifacts']
        original_name = f'{source.stem}_original{source.suffix.lower()}'
        original_path = run_dir / original_name
        try:
            if not original_path.is_file():
                _copy_original(source, original_path)
            artifacts['original'] = original_name
        except Exception as error:
            result['save_errors'].append(
                f'{source.name}/original: {type(error).__name__}: {error}'
            )
        for name in ARTIFACT_NAMES:
            filename = f'{source.stem}_{name}.jpg'
            if (run_dir / filename).is_file():
                artifacts[name] = filename
    _atomic_json(run_dir / 'result.json', result)
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
