# ADR-0020：網路安全鎖集中為單一復原政策

- Status: Proposed
- Date: 2026-10-14

## Context

失聯安全鎖同時存在 Django 保存的 `network_recovery` 與 ESP32 NVS 的 interlock，兩者須經 `recover_network` 歸位交握一起解除。「鎖住時禁止什麼」分散在 `network_required` 的路徑例外、選項切換原因、狀態 payload 覆寫、裝置回報後重新上鎖與復原確認條件，模式規則多為事後補上。ESP32 能力與感測狀態只存在記憶體，Django 重啟後須等下一次特定請求才恢復。

2026-10-14 實機出現三次卡死：Django 重啟後能力欄位為空、純拍攝復原後被裝置 interlock 立即重新上鎖、與硬體無關的通知開關被鎖擋住。現場也難以分辨正式開始條件與復原條件。

## Decision

建議由 Django 單一模組負責鎖的生命週期、保存與阻擋政策，以一個依 `hardware_mode` 判斷的政策回答「目前的鎖禁止哪些操作、原因為何、如何解除」，其他 view、payload 與 UI 只讀取此結果，不各自判斷。

政策區分兩種鎖：資料鎖保護本輪暫存資料與相機流程，任何模式都要人工清理確認；馬達鎖對應 ESP32 interlock，只限制硬體模式，且只能經既有歸位交握解除。純拍攝只受資料鎖限制。復原確認與正式開始各有獨立條件。每次裝置請求都經同一觀察路徑更新能力、感測與 interlock，不受鎖狀態影響。

Wi-Fi、配網與 [ADR-0018](0018-local-network-onboarding.md) 的連線層不在範圍內。交握、ACK 與 wire contract 不變，仍以 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md)「失聯門檻與安全交握」為準。

## Consequences

既有 `network_recovery.json` 需相容讀取或一次轉換，不得因升級遺失鎖。測試改以「模式 × 鎖種類 × 操作」矩陣驗證，取代逐一端點的例外測試。重構期間行為須與 commit `cb97758` 後的規則一致，再另行調整政策；安全相關變更須實機驗收硬體復原交握。
