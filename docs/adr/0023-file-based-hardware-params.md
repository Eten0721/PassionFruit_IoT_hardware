# ADR-0023：硬體參數改由檔案設定、重新啟動生效

- Status: Accepted
- Date: 2026-10-17
- Supersedes: [ADR-0006](0006-dashboard-managed-capture-timing.md)

## Context

ADR-0006 讓 Dashboard 在執行中修改三站 timing 與送料脈波，並要求每次修改送料參數後，操作員都要先測試送料、再勾選目視確認，Django 才允許正式運轉。

實際使用時，這套動態修改與校正確認經常因 Django 狀態不一致而卡住，導致無法開始檢測。另一方面，這些參數只有開發與維護者會調整，一般農夫不應接觸。

## Decision

- 八項硬體參數由 `Django_Server/hardware_params.toml` 提供。檔案只放參數本身，推薦值與範圍寫在註解，並納入 Git。
- Django 只在啟動時讀取並驗證；檔案遺失、格式錯誤、出現未知欄位或數值不合法時，拒絕啟動並指出原因。執行中不提供修改 API，修改後必須重新啟動 Django。
- Revision 由參數內容計算：內容不變則 revision 不變，重啟時 ESP32 不會重複套用；內容改變後，ESP32 會在下一次安全閒置時自動套用並回報 ACK。韌體協定沿用 ADR-0006，不需修改。
- 取消 `feeder_calibrated` 與人工確認 API；正式運轉不再要求送料校正，但仍要求 ESP32 已 ACK 最新 revision。
- Dashboard 的「停穩設定與送料校正」改為唯讀，與診斷區一起預設隱藏，雙擊頁首標題才會顯示；保留「測試送料一顆」供維護者驗證。

## Consequences

- 改送料脈波或最長運轉時間後，「恰好送出一顆」改由修改檔案的維護者自行用測試送料確認，系統不再強制。ESP32 本機的最長運轉時間與 HC-SR04 停止保護不受影響。
- 舊的 `runtime_config/capture_timing.json` 不再讀取，可以手動刪除。
- 現行參數範圍、ACK 流程與開始條件以 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md#硬體參數) 為準。
