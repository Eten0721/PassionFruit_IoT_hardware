# 外部 Repository 工作區

`external/` 用來放置需要與本硬體系統共同開發、但仍由其他 Git repository 獨立維護的專案。父層 `Eten0721/PassionFruit_IoT_hardware` 不追蹤這些 child repository 的內容。

## 模型與檢測層

- GitHub：[`fcu-passionfruit-project/ps-quality-detection-system`](https://github.com/fcu-passionfruit-project/ps-quality-detection-system)
- 本機位置：`external/ps-quality-detection-system/`
- 模型權重：目前不隨 repository 發布；prototype 只留本機。未來通過驗收的正式權重才由模型 repository 使用 Git LFS 管理。
- 原始照片、dataset、`runs/` 與歷史 checkpoint：不進 Git。原始快照實體放在 `D:\passion-fruit-datasets\`，模型 repository 的 ignored `dataset/` junction 指向該位置，並以 `datasets/*.yaml` manifest 記錄版本、checksum 與切分狀態。

目前凍結快照 `pf-20260716-v001` 含 `327` 顆百香果與 `981` 張三站照片，可由 `external/ps-quality-detection-system/dataset/pf-20260716-v001/` 存取。該 junction 不是備份；照片仍須上傳 Roboflow 或團隊共用儲存空間建立第二份副本。

若本機尚未有模型 repository，請在硬體 repository 根目錄執行：

```powershell
git clone https://github.com/fcu-passionfruit-project/ps-quality-detection-system.git external\ps-quality-detection-system
```

硬體與模型是兩個獨立 repository。修改後必須分別在各自根目錄檢查、commit 與 push；不可在硬體 repository 執行 `git add external/ps-quality-detection-system`。

## 未來 Django 整合

模型 repository 整理成可匯入的 Python package 後，可在硬體專案虛擬環境執行：

```powershell
python -m pip install -e .\external\ps-quality-detection-system
```

Django adapter 只接收每站 ROI、原始影像尺寸、`roi_area_ratio`、顏色、二分類皺褶、四種局部瑕疵 mask 面積比例、confidence、耗時與模型版本等結構化結果。瑕疵比例以果實 ROI 面積正規化；未偵測時為 `0`，ROI 或推論失敗時為 `null` 並進入人工覆核。所有有效果實均交由 XGBoost 四分類，不設炭疽病一票否決。AI 必須透過既有 `classify_fruit`、command ID、互斥與 timeout 協定，不得直接控制 ESP32 GPIO 或 MG996R。
