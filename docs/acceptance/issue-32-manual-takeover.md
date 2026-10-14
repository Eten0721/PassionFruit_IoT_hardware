# Issue #32 純拍攝人工接手驗收

契約唯一來源：[CAPTURE_SPEC.md：檢測與判級](../CAPTURE_SPEC.md#檢測與判級)、[檢測資料](../CAPTURE_SPEC.md#檢測資料與-line-通知)。本文件只記錄驗證結果與人工操作。

## 自動化驗證

2026-10-14 於 `PF` 環境：

- `python -s manage.py test fruit_app`：233 項全數通過。`fruit_app.test_detection` 經 HTTP 驗證人工四級、`model_classification` 與原失敗證據保留、`manual_completed` 與 `manual` 來源、同顆 JSON／總表單列、不寫入蒐集分區、過期 `result_id`／`fruit_id`／資料模式、同級重送與改級、模型已判級、新一輪後舊結果、JSON 或總表保存失敗後維持待人工處理並可重試，放棄本輪（`abandoned`／`manual_abandoned`、不分級、照片與原因保留、總表單列），檢測分區不建立四級目錄與 `metadata.csv`（空殼移除、有內容保留），以及硬體模式回 `hardware_takeover_pending` 且 Gate 3 鎖不變。
- 正式 ESP32 板型 `esp32:esp32:esp32` 編譯通過（本 Issue 未修改 Firmware）；`git diff --check` 通過。
- 瀏覽器預覽以正式 YOLO 與 XGBoost 跑一輪無果實照片：操作列收合時右側仍顯示「待人工處理」、三張 `roi:roi_not_detected` 與四個人工按鈕；選「加工」後顯示「加工（人工判定）」與原因 `detection_incomplete`，重新整理後結果仍在，總表僅一列（`manual_completed`、`manual`），無 motor command、未開始下一輪。

## 操作員紀錄

使用一般 Dashboard，選「自動檢測＋純拍攝」，不需連接 ESP32。

**準備失敗案例：** 放一顆果實按「拍攝本輪三張」，第 1 張拍完後（每張間隔約 1 秒）把果實移出畫面，讓第 2、3 張只拍到空平台。這樣第 1 張有真實 ROI 與檢測圖，其餘兩張保留原圖並顯示 `roi:roi_not_detected`。若來不及移出，也可全程對準空平台，或對準 `firmware/tests/build/issue30_acceptance/no_fruit/` 內的無果實照片。

| 步驟 | 檢查方式 | 預期 | 實際結果 |
|---|---|---|---|
| 查看失敗案例 | 本輪結束後，收合「目前流程與操作設定」，查看左側三張與右側結果卡 | 右側顯示「待人工處理」與失敗原因；左側可看到三張原圖及已完成的檢測圖，不是空白或只有錯誤代碼 | 通過（操作員 2026-10-14 確認） |
| 人工選級 | 在右側「人工接手判級」選一個等級 | 顯示「該等級（人工判定）」與原失敗原因；`detection/results/fruit_NNN/result.json` 的 `classification.source` 為 `manual`，`model_classification` 仍是原 `needs_review` | 通過（操作員 2026-10-14 確認） |
| 放棄本輪 | 另跑一輪全程無果實，於右側按「放棄本輪（不分級）」並確認 | 顯示「已放棄本輪」與原失敗原因；總表該列 `result_status=manual_abandoned`、等級欄位空白；不啟動硬體或下一輪 | 通過（操作員 2026-10-14 確認） |
| 重新整理與總表 | 重新整理頁面，再開 `detection/detection_summary.csv` | 結果仍顯示；這顆只有一列，`result_status=manual_completed`、`classification_source=manual`；沒有啟動硬體或下一輪 | 通過（操作員 2026-10-14 確認） |

操作員於 2026-10-14 回報四項皆通過。
