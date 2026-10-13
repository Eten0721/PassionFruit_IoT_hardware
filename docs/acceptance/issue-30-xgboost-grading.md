# Issue #30 純拍攝 XGBoost 判級驗收

契約唯一來源：[CAPTURE_SPEC.md：檢測與判級](../CAPTURE_SPEC.md#檢測與判級)、[檢測資料](../CAPTURE_SPEC.md#檢測資料與-line-通知)；特徵決策見 [ADR-0019](../adr/0019-deployed-two-stage-xgboost-grading.md)。本文件只記錄驗證結果與人工操作。

## 自動化驗證

2026-10-14 於 `PF` 環境（Python `3.14.4`、xgboost `3.2.0`，與模型存檔版本相同）：

- `python -s manage.py test fruit_app`：220 項全數通過。`fruit_app.test_grading` 涵蓋第一階段 `0.5` 門檻兩側、第二階段三類映射、canonical code、衍生特徵、特徵順序、最差視角、缺值與無效值；`fruit_app.test_detection` 經 HTTP 上傳三張驗證 JSON／CSV、缺 ROI 待人工處理、判級模型缺失、JSON 或 CSV 保存失敗（失敗輪次仍留總表列）、重複上傳、重置後 `result_id` 不碰撞、刪除與重啟不重複列。
- 正式 ESP32 板型編譯通過（本 Issue 未修改 Firmware）；`git diff --check` 通過。

## 真實模型比對

以正式六個模型對模型 Repository `DecisionLayerTrain/test` 的 48 顆、144 張照片執行完整流程：

- 以組員交付範例的兩階段算法重算同一批特徵，144 張單張等級與 48 顆最差視角等級全數一致。
- 48 顆皆可判級，四級都有輸出；與舊人工級距（不是單張等級，不能當準確率）對照時，舊「加工」13/15 顆預測為加工、舊「上等」15/15 顆預測為上等。
- 由實照背景裁切的三張無果實圖片，三張皆為 `roi:roi_not_detected`，整顆 `needs_review`，未產生等級。

限制：交付模型的訓練 CSV、所用 YOLO 權重與前處理紀錄不在本機任一 Repository。部署特徵依 Decision Dataset 單張欄位定義與交付範例核對，正式 YOLO 權重與模型 Repository manifest 的 SHA-256 相同，但尚無訓練資料可證明輸入分布一致，因此不宣稱模型一致性已通過。

## 操作員紀錄

使用一般 Dashboard，選「自動檢測＋純拍攝」，不需連接 ESP32。無果實測試照片放在 ignored 的 `firmware/tests/build/issue30_acceptance/no_fruit/`；手機無法直接選檔，請讓相機對準空平台，或對準在螢幕上顯示的這些照片。

| 步驟 | 檢查方式 | 預期 | 實際結果 |
|---|---|---|---|
| 一輪判級 | 放一顆果實，按一次開始，等三張完成 | 三張各顯示等級，上方顯示整顆等級；只完成一輪，不自動開始下一輪，沒有 ESP32 命令 | 未執行 |
| 對照結果 | 開啟資料位置的 `detection/results/fruit_NNN/result.json` 與 `detection/detection_summary.csv` | 整顆等級等於三張中最低級；CSV 只有這顆一列，`result_json` 指向該 JSON | 未執行 |
| 無果實 | 相機對準空平台或無果實照片再跑一輪 | 顯示 `roi:roi_not_detected` 與「待人工處理」，不顯示任何等級或檢測完成 | 未執行 |

實際結果由操作員填寫，不預先勾選通過。
