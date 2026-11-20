# ADR-0025：ESP32 長按 BOOT 10 秒清除失聯鎖

- Status: Accepted
- Date: 2026-11-20
- Extends: [ADR-0020](0020-single-network-recovery-policy.md)

## Context

ESP32 把失聯鎖存在 NVS，重開機仍保留，只能由 `recover_network` 雙端交握解除。交握任一步卡住時，Dashboard 的 runtime reset 只清 Django，ESP32 下次回報又讓 Django 上鎖；現場重開機也無法脫離。2026-11-18 實機時筆電與手機熱點斷線後發生此情況。

## Decision

ESP32 正常開機後，長按 BOOT `10 s` 即在本機清除失聯鎖：先停止送料與分類器，把 NVS phase 寫為 `0`，再讓閘門與分類器歸位，不送料。現場按住實體按鍵視為人工安全確認，操作前須斷電取出果實、手離開機構。

- 保留 recovery generation：Django 在相同 generation 下不會把未鎖定的閒置 ESP32 重新上鎖，之後再由 Dashboard runtime reset 清除 Django 端的鎖。
- 只計算開機後放開過再按下的長按；開機時已按住（含 GPIO0 燒錄模式 strap 或按鍵卡住）不清鎖。
- 短按仍立即提出配網請求；長按達 `10 s` 時取消尚未完成的配網。
- NVS 寫入失敗時維持上鎖並回報 `recovery_persist_failed`。

操作步驟只在 Dashboard 開發者模式「停穩設定與送料校正」摺疊欄位與 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md#esp32-配網與控制端識別) 維護。

## Consequences

現場人員可在不靠交握的情況下解除任何持久鎖；代價是歸位前的安全確認完全依賴操作員。Django 的 generation 落後 ESP32 且仍上鎖時，第一次重置後 ESP32 會被要求再上鎖一次，須重複長按與 runtime reset 一次才收斂。需重新燒錄 Firmware；未實機驗證。
