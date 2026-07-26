# ADR-0003：受守門的首站捷徑與有界重試

- Status: Accepted
- Date: 2026-07-10

## Context

果實已由 Gate 1 攔住後，標準開始交握仍增加首張照片前的 HTTPS 往返；timeout 又不代表 Django 沒收到 request。

## Decision

Firmware 只在 idle、無 sequence、所有 Gate Home 且首站已停穩時回報首站捷徑。Retry 重用同一 `trigger_id`；可回退的協定拒絕才走標準流程。感測、Wi-Fi、HTTPS 與伺服 phase 都使用 deadline。

## Consequences

捷徑只縮短第 1 站前控制往返，不改變照片保存交握。Django trace 只量測伺服器端鏈路，實體延遲仍須同步量測。
