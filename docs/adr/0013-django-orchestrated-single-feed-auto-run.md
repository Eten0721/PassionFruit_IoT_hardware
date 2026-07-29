# ADR-0013：Django 協調單顆送料自動運轉

- Status: Accepted
- Date: 2026-07-27

> 上游送料的正常終止條件已由 [ADR-0014](0014-hcsr04-terminated-upstream-feed.md) 改為 HC-SR04 回授；本 ADR 的 Django 協調、互斥、冪等與重新啟動安全原則仍有效。

## Context

上游送料需在不增加步進馬達或 Home 感測器成本的前提下，與既有三站拍攝、人工分類、單一 command slot 及不穩定 Wi-Fi 共存。連續旋轉 SG90 只能以脈波與時間估算轉量，網路 timeout 或控制器重新啟動又可能讓同一顆果實被重複送料。

## Decision

Django 保持自動運轉、互斥與下一顆送料決策的中央狀態來源；ESP32 在 GPIO `23` 執行一次有界 `feed_one`，到期後不依賴網路自行停止，再以相同 command ID 重送 terminal report。送料採可由 Dashboard 保存與 ACK 的開迴路校正值，預設固定順時針，不提供反轉、Home 或自動補轉。暫停只禁止後續送料，已承諾的目前果實完成既有流程；正式 Dashboard 移除 manual capture，重拍只復原同一顆果實且不驅動送料。

ESP32 每次開機回報 `boot_id`、`feeder_v1` capability 與 feeder state。重新啟動或結果不確定時，Django 關閉自動運轉、保留 HC-SR04 接手可能已送出的果實，且不自動重送 `feed_one`；安全原則是寧可少送一次，也不重複送料。

## Consequences

開迴路角度會受電壓、負載、摩擦與機構公差影響，`feed_cycle_completed` 只表示有界控制時序完成。若空料斗校正與連續 `20` 顆驗收仍無法消除漏送、雙送或累積偏移，必須修改機構容差，或重新評估回授感測器與致動器，不能用自動重複驅動掩蓋。
