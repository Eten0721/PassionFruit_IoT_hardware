# 決策層

`decision_layer/` 預留給未來機器學習分級流程。目前正式系統仍由使用者人工分類，尚未啟用自動推論。

## 輸入契約

- 輸入必須來自三站影像 pipeline 的結構化結果；自由文字 note 不能直接作為完整模型特徵。
- 相對大小使用每站 `roi_area_ratio = bbox_width × bbox_height / (image_width × image_height)`，只代表照片中的相對佔比。
- 皺褶使用 `smooth`／`wrinkle` 二分類。
- 局部瑕疵使用 `anthracnose`、`insect_track`、`abrasion`、`insect_bite` 四類 segmentation。
- 每站保存 `defect_mask_pixels / fruit_roi_pixels`；三站共 `12` 個正式瑕疵比例特徵。
- 未偵測到瑕疵記為 `0`；ROI 或推論失敗記為 `null` 並進入人工覆核。
- Detect bbox 代理值必須使用 `*_bbox_area_ratio`，不得與正式 mask ratio 混用。

## 決策與輸出

所有有效果實由模型結合相對大小、顏色、皺褶與局部瑕疵比例進行四分類，不使用炭疽病一票否決。分類語意與 command code 以 [`docs/DATA_COLLECTION_SPEC.md`](../docs/DATA_COLLECTION_SPEC.md) 為準。

AI 結果必須交回 Django 的既有資料分類與 sorter 邊界，不得直接控制 ESP32。模型失敗、低信心或輸入不完整時保留人工覆核，不猜測分類。

模型版本、信心門檻、校正方式與人工覆核 UI 尚未定案，啟用正式推論前另開 GitHub Issue。
