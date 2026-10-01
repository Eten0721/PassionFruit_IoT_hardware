# ADR-0004：HC-SR04 Echo 電壓安全

- Status: Accepted
- Date: 2026-07-10

## Context

HC-SR04 Echo 的邏輯電壓高於 ESP32 GPIO 可安全承受的範圍。

## Decision

Echo 接入 ESP32 前必須使用分壓或邏輯電平轉換，並在上電前實際量測。接線、電阻與安全門檻由 [`record_image/硬體接線與驗收摘要.md`](../../record_image/硬體接線與驗收摘要.md) 定義。

## Consequences

軟體 timeout、retry 或首站捷徑不能取代電氣安全。HC-SR04、ESP32 與伺服外部電源必須共地。
