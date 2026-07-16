# 決策層

`decision_layer/` 預留給未來的機器學習決策流程，例如 XGBoost、Random Forest 或其他分級模型。目前尚未在正式系統中實作 AI 自動推論，Dashboard 仍由使用者按下人工分類按鈕決定結果。

未來決策層應遵守下列整合邊界：

- 輸入可來自三站照片的模型推論、外觀瑕疵、大小或其他結構化特徵。
- 輸出中文語意必須為上等、中等、下等或加工，再依序映射至相容分類代碼 `high_medium`、`discard`、`low`、`processing`。其中 `discard` 是歷史協定名稱，目前代表中等，不可解讀為廢棄。
- 上等、中等與下等可使用皺褶、擦傷及顏色特徵；顏色需涵蓋綠色、橘色與黃色等情形。系統不再提供廢棄級距，原本應判為廢棄的果實歸入加工。
- AI 決策完成後應沿用 Django 的 `classify_fruit` 命令、單一 motor command slot、command ID、sorter 狀態與 timeout 規則。
- 決策層不可直接控制 ESP32 GPIO、MG996R 角度或 PWM，也不可繞過照片分類、metadata 與 dataset 一致性流程。
- 硬體 repository 不追蹤模型權重、訓練輸出或 dataset；模型 repository 僅以 Git LFS 管理已驗收的正式權重，正式整合前必須定義模型版本、信心門檻與人工覆核方式。
