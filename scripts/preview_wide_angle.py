"""預覽超廣角校正效果，用來決定 DETECTION_WIDE_ANGLE_FOCAL_RATIO。

用法（PF 環境）：
    python scripts/preview_wide_angle.py 第三站照片.jpg 0.34 0.376 0.42

每個數值輸出一張「原圖｜校正後」並排圖到照片旁；選第三站果實最接近
圓形的數值。數值太小果實會被壓扁，太大則仍是拉長的蛋形。
"""

from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'Django_Server'))

from fruit_app import wide_angle  # noqa: E402


def main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print(__doc__)
        return 2
    source = Path(arguments[0])
    image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'無法讀取圖片：{source}')
    for value in arguments[1:]:
        ratio = float(value)
        preview = np.hstack([image, wide_angle.rectify(image, ratio)])
        target = source.with_name(f'{source.stem}_wide_angle_{ratio:g}.jpg')
        cv2.imencode('.jpg', preview)[1].tofile(target)
        print(f'已輸出：{target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
