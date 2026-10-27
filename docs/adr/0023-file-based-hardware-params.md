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
- 參數內容對應持久保存的遞增 revision：內容不變則重啟後 revision 不變；參數修改或回復舊值時也必須比前次大，符合既有 Firmware 的比較方式。相符 ACK 綁定 ESP32 boot 保存，Django 重啟後須重新觀察同一 boot 才能恢復同步狀態；新 boot 仍需自行回報。格式、遷移與保存失敗契約只在 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md#硬體參數) 維護，韌體 wire contract 不變。
- 取消 `feeder_calibrated` 與人工確認 API；正式運轉不再要求送料校正，但仍要求 ESP32 已 ACK 最新 revision。
- Dashboard 的「停穩設定與送料校正」改為唯讀，與診斷區一起預設隱藏，雙擊頁首標題才會顯示；保留「測試送料一顆」供維護者驗證。

## Consequences

- 改送料脈波或最長運轉時間後，「恰好送出一顆」改由修改檔案的維護者自行用測試送料確認，系統不再強制。ESP32 本機的最長運轉時間與 HC-SR04 停止保護不受影響。
- 舊的 `runtime_config/capture_timing.json` 不再讀取，可以手動刪除。
- 現行參數範圍、ACK 流程與開始條件以 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md#硬體參數) 為準。
