# 模型訓練與檢測層

完整 YOLO 模型訓練與 Multi-stage Pipeline 不放在此硬體 repository，統一由團隊 repository 維護：

- GitHub：[`fcu-passionfruit-project/ps-quality-detection-system`](https://github.com/fcu-passionfruit-project/ps-quality-detection-system)
- 本機硬體 workspace：`D:\PassionFruit_IoT_hardware\`
- 本機模型 workspace：`D:\ps-quality-detection-system\`
- 本機原始照片資料：`D:\passion-fruit-datasets\`

硬體 repository `Eten0721/PassionFruit_IoT_hardware` 只負責三站照片蒐集、Django、Dashboard、ESP32、MG996R 與未來的薄整合介面，不複製模型訓練程式或權重。

## Repository 邊界

模型 repository 負責：

- YOLO 訓練、驗證、準確率評估與獨立推論工具。
- ROI 裁切、圓形遮罩、灰階 CLAHE、顏色、皺褶與局部瑕疵 pipeline。
- Dataset manifest、模型 manifest 與正式模型版本。
- 經過驗收的正式權重；正式權重使用 Git LFS，照片、`runs/` 與歷史 checkpoint 不進 Git。

硬體 repository 負責：

- 手機相機在三站各拍攝一張照片。
- Django dataset／metadata 與流程狀態管理。
- 未來呼叫檢測層的 Django adapter。
- 決策層、`classify_fruit`、ESP32 command protocol 與 MG996R。

AI 模型不可直接控制 GPIO。正式整合路徑固定為：

```text
三張照片
-> 檢測層結構化特徵
-> 決策層
-> high_medium / low / processing / discard
-> Django classify_fruit
-> ESP32 與 MG996R
```

目前不使用 Git submodule。開發期間兩個 repository 保持同層或以明確路徑連接；待檢測層整理成可匯入的 Python package 並建立版本 tag 後，硬體端再固定使用指定版本。
