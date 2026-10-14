"""Read the shared result contract and create a local farmer preview."""

from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import grading
from .detection import CONTRACT_VERSION, DEFECT_COLORS, DEFECT_MARKED_SUFFIX


DEFECT_NAMES = {
    'abrasion': '擦傷', 'anthracnose': '炭疽病',
    'insect_bite': '蟲咬', 'insect_track': '畫圖蟲',
}
PHOTO_NAMES = ('img_01.jpg', 'img_02.jpg', 'img_03.jpg')
PANEL_WIDTH = 640
PHOTO_HEIGHT = 480
FONT_SIZE = 28
LINE_HEIGHT = 40


def generate_notification(result_path: Path, *, output_dir: Path | None = None,
                          font_path: Path | None = None) -> dict:
    """Return local UTF-8/JPEG paths, or ``{ok: False, reason: ...}``.

    Only the saved final classification is used. Expected missing results or
    images skip the preview; invalid contracts, paths and fonts raise explicitly.
    Input files and model predictions stay unchanged. No runtime state or
    credential is accessed.
    """
    result_path = Path(result_path).resolve()
    if not result_path.parent.is_dir():
        raise FileNotFoundError(f'找不到結果目錄：{result_path.parent}')
    if not result_path.is_file():
        if result_path.exists():
            raise ValueError(f'結果路徑必須是 JSON 檔案：{result_path}')
        return {'ok': False, 'reason': 'result_not_found'}
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if not isinstance(result, dict) or result.get('contract_version') != CONTRACT_VERSION:
        raise ValueError('不支援的檢測結果契約版本。')
    classification = result.get('classification')
    if not isinstance(classification, dict):
        raise ValueError('檢測結果缺少最終分類。')
    if classification.get('status') in ('needs_review', 'abandoned'):
        return {'ok': False, 'reason': 'not_graded'}
    grade = classification.get('grade')
    if (classification.get('status') != 'graded' or type(grade) is not int
            or grade not in range(4)):
        raise ValueError('最終分類狀態或等級不正確。')
    label, code = grading.grade_label(grade)
    if (classification.get('label') != label or classification.get('code') != code
            or classification.get('source') not in ('model', 'manual')):
        raise ValueError('最終分類名稱、代碼或來源不符合共同契約。')
    if label != '加工':
        return {'ok': False, 'reason': 'not_processing'}
    storage = result.get('storage')
    if not isinstance(storage, dict):
        raise ValueError('檢測結果 storage 必須是物件。')
    if storage.get('status') != 'saved':
        return {'ok': False, 'reason': 'result_not_saved'}
    result_id = result.get('result_id')
    if not isinstance(result_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result_id):
        raise ValueError('缺少可用的穩定結果識別。')
    if result.get('hardware_mode') not in ('hardware', 'photo_only'):
        raise ValueError('拍攝來源不符合共同契約。')
    images = result.get('images')
    if not isinstance(images, dict) or set(images) - set(PHOTO_NAMES):
        raise ValueError('檢測視角名稱不符合共同契約。')
    if any(not isinstance(image, dict) for image in images.values()):
        raise ValueError('檢測結果 images 的每個視角必須是物件。')
    capture_time = result.get('capture_time')
    if capture_time is not None and not isinstance(capture_time, str):
        raise ValueError('檢測結果 capture_time 必須是文字或 null。')
    views = []
    photos = []
    try:
        for index, name in enumerate(PHOTO_NAMES, 1):
            view, photo = _view(index, images.get(name) or {}, result_path.parent)
            views.append(view)
            photos.append(photo)
        if not any(photo is not None for photo in photos):
            return {'ok': False, 'reason': 'no_images'}
        heading = '測試 |瑕疵通知|' if result['hardware_mode'] == 'photo_only' else '|瑕疵通知|'
        decision = '加工（人工判定）' if classification['source'] == 'manual' else '加工'
        observed = list(dict.fromkeys(
            observation for view in views if view['detection_complete']
            for observation in view['observations']
        ))
        unfinished = [str(view['photo_number']) for view in views if not view['detection_complete']]
        findings = '、'.join(observed) or (
            '無可用檢測結果' if len(unfinished) == 3 else
            '已完成檢測的照片未偵測到瑕疵' if unfinished else '未偵測到瑕疵'
        )
        detected_at = datetime.fromisoformat(capture_time).strftime('%Y-%m-%d %H:%M:%S') if capture_time else '未記錄'
        text = '\n'.join([
            heading, f'偵測時間：{detected_at}', f'等級：{decision}', f'發現瑕疵：{findings}',
            *([f"備註：照片 {'、'.join(unfinished)} 未完成檢測，未列入瑕疵觀察"] if unfinished else []),
        ]) + '\n'
        font_path = Path(font_path) if font_path is not None else Path('C:/Windows/Fonts/msjh.ttc')
        font = _font(font_path, text + ''.join(view['label'] for view in views))
        output_dir = Path(output_dir).resolve() if output_dir is not None else result_path.parent
        if output_dir.exists() and not output_dir.is_dir():
            raise NotADirectoryError(f'輸出路徑必須是目錄：{output_dir}')
        paths = {
            'text_path': output_dir / f'notification_{result_id}.txt',
            'image_path': output_dir / f'notification_{result_id}.jpg',
            'preview_path': output_dir / f'notification_{result_id}_preview.jpg',
        }
        protected = {result_path}
        for image in images.values():
            protected.update(_artifact_path(result_path.parent, filename)
                             for filename in (image.get('artifacts') or {}).values())
        if any(path.resolve() in protected for path in paths.values()):
            raise ValueError('通知輸出不得覆蓋原始結果或影像。')
        with _compose(views, photos, font) as composite:
            full_jpeg = _jpeg(composite, 10_000_000)
            with ImageOps.contain(composite, (960, 960)) as preview:
                preview_jpeg = _jpeg(preview, 1_000_000)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths['text_path'].write_text(text, encoding='utf-8')
        paths['image_path'].write_bytes(full_jpeg)
        paths['preview_path'].write_bytes(preview_jpeg)
        return {'ok': True, 'reason': None, 'result_id': result_id, 'text': text,
                'views': views, 'font_path': str(font_path.resolve()),
                **{key: str(path) for key, path in paths.items()}}
    finally:
        for photo in photos:
            if photo is not None:
                photo.close()


def _artifact_path(root, filename):
    if (not isinstance(filename, str) or not filename
            or '/' in filename or '\\' in filename or ':' in filename
            or filename in ('.', '..')):
        raise ValueError(f'不合法的影像檔名：{filename!r}')
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError('影像路徑不得離開結果目錄。')
    return path


def _read_photo(path):
    try:
        with Image.open(path) as source:
            with ImageOps.exif_transpose(source) as oriented:
                return oriented.convert('RGB')
    except OSError:
        return None


def _view(index, image, root):
    for field in ('roi', 'models', 'artifacts'):
        if field in image and not isinstance(image[field], dict):
            raise ValueError(f'照片 {index} 的 {field} 必須是物件。')
    models = image.get('models') or {}
    for role in ('color', 'wrinkle', 'defect'):
        if role in models and not isinstance(models[role], dict):
            raise ValueError(f'照片 {index} 的 models.{role} 必須是物件。')
    observations = []
    color = models.get('color') or {}
    wrinkle = models.get('wrinkle') or {}
    defect = models.get('defect') or {}
    if color.get('status') == 'ok' and color.get('class_name') == 'bad_color':
        observations.append('色澤異常')
    if wrinkle.get('status') == 'ok' and wrinkle.get('display_label') == '有皺褶（壞）':
        observations.append('皺褶')
    if defect.get('status') == 'ok':
        detections = defect.get('detections')
        if not isinstance(detections, list) or any(not isinstance(item, dict) for item in detections):
            raise ValueError(f'照片 {index} 的 defect.detections 必須是物件陣列。')
        for detection in detections:
            name = detection.get('class_name')
            if name not in DEFECT_NAMES:
                raise ValueError(f'未知的局部瑕疵類別：{name!r}')
            if DEFECT_NAMES[name] not in observations:
                observations.append(DEFECT_NAMES[name])
    complete = (image.get('roi') or {}).get('status') == 'ok' and all(
        (models.get(role) or {}).get('status') == 'ok'
        for role in ('color', 'wrinkle', 'defect')
    )
    artifacts = image.get('artifacts') or {}
    marked_filename = artifacts.get('defect_marked')
    marked_available = (isinstance(marked_filename, str)
                        and marked_filename.endswith(DEFECT_MARKED_SUFFIX))
    legend = []
    if complete and marked_available:
        for detection in defect['detections']:
            class_index = detection.get('class_index')
            if type(class_index) is not int or class_index < 0:
                raise ValueError(f'照片 {index} 的 defect.detections.class_index 必須是非負整數。')
            legend.append({
                'label': DEFECT_NAMES[detection['class_name']],
                'class_index': class_index,
                'color': list(DEFECT_COLORS[detection['class_name']]),
            })
    photo = None
    source_path = None
    if complete and marked_available:
        source_path = _artifact_path(root, artifacts['defect_marked'])
        photo = _read_photo(source_path)
    marked = photo is not None
    if photo is None:
        legend = []
        if artifacts.get('original'):
            source_path = _artifact_path(root, artifacts['original'])
            photo = _read_photo(source_path)
    labels = []
    if not complete:
        labels.append('未完成檢測')
    elif not marked:
        labels.append('無標示圖')
    if photo is None:
        labels.append('未取得影像')
    if observations:
        labels.append('、'.join(observations))
    elif complete:
        labels.append('未偵測到瑕疵')
    caption_labels = labels
    if legend:
        local_names = {item['label'] for item in legend}
        caption_labels = [
            '、'.join(name for name in observations if name not in local_names)
        ]
    return {
        'photo_number': index, 'observations': observations,
        'label': '；'.join(labels), 'detection_complete': complete,
        'caption': '；'.join(caption_labels), 'legend': legend,
        'image_path': str(source_path) if photo is not None else None,
    }, photo


def _font(path, text):
    if not path.is_file():
        raise FileNotFoundError(f'找不到中文字型：{path}；請指定微軟正黑體 msjh.ttc。')
    try:
        font = ImageFont.truetype(str(path), FONT_SIZE)
    except OSError as error:
        raise ValueError(f'無法載入中文字型：{path}') from error
    missing = font.getmask('\u0378')
    for char in set(text):
        if '\u3400' <= char <= '\u9fff':
            glyph = font.getmask(char)
            if not glyph.getbbox() or (glyph.size == missing.size and bytes(glyph) == bytes(missing)):
                raise ValueError(f'字型缺少中文字形：{char}（{path}）。')
    return font


def _wrap(text, font):
    lines = ['']
    for char in text:
        if font.getlength(lines[-1] + char) > PANEL_WIDTH - 32:
            lines.append('')
        lines[-1] += char
    return lines


def _compose(views, photos, font):
    captions = [
        [f"照片 {view['photo_number']}",
         *(_wrap(view['caption'], font) if view['caption'] else [])]
        for view in views
    ]
    header_height = max(len(lines) + len(view['legend'])
                        for lines, view in zip(captions, views, strict=True)) * LINE_HEIGHT + 32
    composite = Image.new('RGB', (PANEL_WIDTH * 3, header_height + PHOTO_HEIGHT), 'white')
    draw = ImageDraw.Draw(composite)
    for index, (view, photo, lines) in enumerate(zip(views, photos, captions, strict=True)):
        left = index * PANEL_WIDTH
        for line_index, line in enumerate(lines):
            draw.text((left + 16, 16 + line_index * LINE_HEIGHT), line,
                      font=font, fill='#222222')
        for legend_index, entry in enumerate(view['legend']):
            top = 16 + (len(lines) + legend_index) * LINE_HEIGHT
            draw.rectangle((left + 16, top + 6, left + 39, top + 29),
                           fill=tuple(entry['color']), outline='#222222', width=2)
            draw.text((left + 52, top), entry['label'], font=font, fill='#222222')
        if photo is None:
            draw.rectangle((left, header_height, left + PANEL_WIDTH - 1, composite.height - 1),
                           fill='#eeeeee')
            draw.text((left + 180, header_height + 210), '未取得影像', font=font, fill='#555555')
            continue
        with ImageOps.contain(photo, (PANEL_WIDTH, PHOTO_HEIGHT)) as scaled:
            composite.paste(scaled, (left + (PANEL_WIDTH - scaled.width) // 2,
                                    header_height + (PHOTO_HEIGHT - scaled.height) // 2))
    return composite


def _jpeg(image, limit):
    for quality in (90, 80, 70, 60):
        with BytesIO() as buffer:
            image.save(buffer, format='JPEG', quality=quality, optimize=True)
            data = buffer.getvalue()
        if len(data) <= limit:
            return data
    raise ValueError(f'合成圖無法壓縮至 {limit} bytes 以內。')
