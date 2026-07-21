# 決策層

`decision_layer/` 預留給未來的機器學習決策流程，例如 XGBoost、Random Forest 或其他分級模型。目前尚未在正式系統中實作 AI 自動推論，Dashboard 仍由使用者按下人工分類按鈕決定結果。

未來決策層應遵守下列整合邊界：

- 輸入來自三站照片的結構化模型結果；目前 `metadata.csv` 只有人工分類 label 與未正規化 note，不能直接當成完整 XGBoost 特徵表。
- 大小特徵使用每張原始照片的 `roi_area_ratio = bbox_width × bbox_height / (image_width × image_height)`，分別保存 `station_01_roi_area_ratio`、`station_02_roi_area_ratio`、`station_03_roi_area_ratio`。它只代表照片內的相對佔比，不是真實面積。
- 皺褶正式目標為 `smooth`／`wrinkle` 二分類；局部瑕疵只考慮炭疽病 `anthracnose`、畫圖蟲 `insect_track`、擦傷 `abrasion` 與蟲咬 `insect_bite`。
- 經已驗證的信心與面積門檻判定有炭疽病時，先以硬規則直接輸出加工 `processing`；其餘灰色地帶才交由 XGBoost 進行四分類。
- 輸出中文語意必須為上等、中等、下等或加工，再依序映射至正式分類代碼 `high`、`medium`、`low`、`processing`。`high_medium` 與 `discard` 僅是新版 firmware 為舊 Django 保留的輸入 alias，新的決策層不得再產生這兩個歷史代碼。
- 上等、中等與下等可使用皺褶、擦傷及顏色特徵；顏色需涵蓋綠色、橘色與黃色等情形。系統不再提供廢棄級距，原本應判為廢棄的果實歸入加工。
- AI 決策完成後應沿用 Django 的 `classify_fruit` 命令、單一 motor command slot、command ID、sorter 狀態與 timeout 規則。
- 決策層不可直接控制 ESP32 GPIO、MG996R 角度或 PWM，也不可繞過照片分類、metadata 與 dataset 一致性流程。
- 硬體 repository 不追蹤模型權重、訓練輸出或 dataset；模型 repository 目前也不發布 prototype 權重。未來通過驗收的正式權重才可使用 Git LFS，且正式整合前必須定義模型版本、信心門檻與人工覆核方式。
