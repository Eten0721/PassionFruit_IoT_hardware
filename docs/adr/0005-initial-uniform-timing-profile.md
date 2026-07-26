# ADR-0005：初始統一 timing profile

- Status: Superseded
- Date: 2026-07-10
- Superseded by: [ADR-0006](0006-dashboard-managed-capture-timing.md)

## Context

首站捷徑導入時，需要一組可部署的機構停穩起點。

## Decision

最初讓四項機構 timing 使用相同的 `200 ms` 基準。

## Consequences

實機量測證明各 phase 需要不同校正值。本 ADR 只保存歷史決策，不可作為現行設定來源。
