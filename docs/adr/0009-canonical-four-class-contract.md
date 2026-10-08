# ADR-0009：現行四級分類契約

- Status: Accepted
- Date: 2026-07-18

## Context

舊 code 名稱、中文語意與實體出口順序不一致，增加部署與模型整合風險。

## Decision

Django、Dataset 與未來決策層統一使用上等、中等、下等、加工四級及對應 canonical ASCII code。Firmware 僅在輸入端保留歷史 alias。

## Consequences

現行 code、角度與 alias 對應只由 [`CAPTURE_SPEC.md`](../CAPTURE_SPEC.md) 定義。部署時先更新可接受新舊輸入的 Firmware，再更新 Django。
