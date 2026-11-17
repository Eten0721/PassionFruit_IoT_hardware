"""用實機照片校定超廣角校正的兩個設定值。

用法（PF 環境，於 Repository 根目錄）：
    python scripts/calibrate_wide_angle.py Django_Server/dataset/detection_校正廣角用

資料夾內每顆果實一個子資料夾，含 img_01／img_02／img_03（或 *_original）原圖。
對每個候選 focal_ratio 以 ROI.pt 找果實框，選出讓外側站點果實的拉長程度
與中央站點一致的值；再以該值算出 fruit_area_ratio 的中位數，換算成
DETECTION_FRUIT_AREA_SCALE，使其中位數對齊訓練資料。結果寫入
<資料夾>/_calibration/，含 result.json 與幾張校正前後對照圖。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'Django_Server'))

from fruit_app import wide_angle  # noqa: E402

# XGBoost 訓練資料（per_view.csv，294 張）的 fruit_area_ratio 中位數。
TRAINING_AREA_MEDIAN = 0.0879
VIEW_PATTERN = re.compile(r'^img_0([123])(?:_original)?\.(?:jpe?g|png)$', re.I)
EDGE_MARGIN_PX = 3


def find_fruits(root: Path) -> dict[Path, dict[int, Path]]:
    fruits: dict[Path, dict[int, Path]] = {}
    for path in sorted(root.rglob('*')):
        if '_calibration' in path.parts or not path.is_file():
            continue
        match = VIEW_PATTERN.match(path.name)
        if match is None:
            continue
        views = fruits.setdefault(path.parent, {})
        # A raw capture wins over the copy saved beside detection results.
        if int(match[1]) not in views or '_original' not in path.stem:
            views[int(match[1])] = path
    return {folder: views for folder, views in fruits.items() if len(views) >= 2}


def read(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'無法讀取圖片：{path}')
    return image


def measure(model, image: np.ndarray, focal_ratio: float) -> dict | None:
    """Return the compensated area and radial stretch of the fruit, if whole."""
    rectified = wide_angle.rectify(image, focal_ratio)
    height, width = rectified.shape[:2]
    result = model.predict(rectified, conf=0.25, imgsz=640, verbose=False)[0]
    if result.boxes is None or not len(result.boxes):
        return None
    best = int(result.boxes.conf.argmax())
    x1, y1, x2, y2 = result.boxes.xyxy[best].tolist()
    if (x1 < EDGE_MARGIN_PX or y1 < EDGE_MARGIN_PX
            or x2 > width - EDGE_MARGIN_PX or y2 > height - EDGE_MARGIN_PX):
        return None  # A fruit cut by the frame edge has no usable size.
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    box_w, box_h = x2 - x1, y2 - y1
    radial, tangential = (box_h, box_w) if height >= width else (box_w, box_h)
    scale = wide_angle.station_area_scale(rectified.shape, focal_ratio, cx, cy)
    return {
        'offset': math.hypot(cx - (width - 1) / 2, cy - (height - 1) / 2)
        / max(height, width),
        'area': box_w * box_h / (width * height) * scale,
        'stretch': math.log(radial / tangential),
    }


def evaluate(model, fruits, images, focal_ratio):
    stretch_gaps, area_gaps, areas = [], [], []
    for folder, views in fruits.items():
        measured = [
            m for m in (measure(model, images[path], focal_ratio)
                        for path in views.values()) if m is not None
        ]
        areas.extend(m['area'] for m in measured)
        if len(measured) < 2:
            continue
        centre = min(measured, key=lambda m: m['offset'])
        for outer in measured:
            if outer is centre or outer['offset'] < centre['offset'] + 0.15:
                continue
            stretch_gaps.append(outer['stretch'] - centre['stretch'])
            area_gaps.append(math.log(outer['area'] / centre['area']))
    if not stretch_gaps:
        return None
    return {
        'focal_ratio': focal_ratio,
        'pairs': len(stretch_gaps),
        'stretch_gap_median': statistics.median(stretch_gaps),
        'area_gap_median': statistics.median(area_gaps),
        'area_median': statistics.median(areas),
        'views_measured': len(areas),
    }


def save_previews(fruits, images, focal_ratio, target: Path, count=4):
    target.mkdir(parents=True, exist_ok=True)
    for folder, views in list(fruits.items())[:count]:
        rows = []
        for path in views.values():
            image = images[path]
            row = np.hstack([image, wide_angle.rectify(image, focal_ratio)])
            rows.append(cv2.resize(row, None, fx=0.35, fy=0.35))
        width = max(row.shape[1] for row in rows)
        rows = [cv2.copyMakeBorder(row, 0, 0, 0, width - row.shape[1],
                                   cv2.BORDER_CONSTANT) for row in rows]
        cv2.imencode('.jpg', np.vstack(rows))[1].tofile(
            target / f'{folder.name}_f{focal_ratio:g}.jpg')


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('folder', type=Path)
    parser.add_argument('--model', type=Path,
                        default=ROOT / 'Django_Server' / 'models' / 'ROI.pt')
    parser.add_argument('--start', type=float, default=0.28)
    parser.add_argument('--stop', type=float, default=0.50)
    parser.add_argument('--step', type=float, default=0.01)
    args = parser.parse_args(arguments)

    fruits = find_fruits(args.folder)
    if not fruits:
        raise FileNotFoundError(f'{args.folder} 內找不到至少兩站照片的果實資料夾。')
    images = {path: read(path) for views in fruits.values() for path in views.values()}
    sizes = {image.shape[:2] for image in images.values()}
    print(f'果實 {len(fruits)} 顆、照片 {len(images)} 張、尺寸 {sorted(sizes)}')

    from ultralytics import YOLO
    model = YOLO(str(args.model))

    rows = []
    for focal_ratio in np.arange(args.start, args.stop + 1e-9, args.step):
        row = evaluate(model, fruits, images, round(float(focal_ratio), 3))
        if row is None:
            continue
        rows.append(row)
        print(f"f={row['focal_ratio']:.3f}  外側拉長差 {row['stretch_gap_median']:+.3f}"
              f"  外側面積差 {row['area_gap_median']:+.3f}"
              f"  面積中位數 {row['area_median']:.4f}  配對 {row['pairs']}")
    if not rows:
        raise RuntimeError('沒有任何果實同時有中央與外側站點的完整果實框。')

    best = min(rows, key=lambda row: abs(row['stretch_gap_median']))
    area_scale = round(TRAINING_AREA_MEDIAN / best['area_median'], 2)
    output = args.folder / '_calibration'
    save_previews(fruits, images, best['focal_ratio'], output)
    summary = {
        'DETECTION_WIDE_ANGLE_FOCAL_RATIO': best['focal_ratio'],
        'DETECTION_FRUIT_AREA_SCALE': area_scale,
        'chosen': best, 'training_area_median': TRAINING_AREA_MEDIAN,
        'fruits': len(fruits), 'image_sizes': sorted(sizes), 'sweep': rows,
    }
    (output / 'result.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n建議 DETECTION_WIDE_ANGLE_FOCAL_RATIO = {best['focal_ratio']}")
    print(f'建議 DETECTION_FRUIT_AREA_SCALE = {area_scale}')
    print(f'對照圖與 result.json：{output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
