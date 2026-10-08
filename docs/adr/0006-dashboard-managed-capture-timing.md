# ADR-0006：Dashboard 管理與 snapshot timing

- Status: Accepted
- Date: 2026-07-11

## Context

實體機構會因安裝、摩擦與果實差異需要校正，編譯期常數不適合作為唯一設定。

## Decision

Dashboard 在 idle 時更新完整 timing profile；Django 原子保存單一 runtime 設定並以 revision 下發。ESP32 只在安全 idle 狀態套用，且每顆果實開始時 snapshot 機構 timing。

## Consequences

流程中不混用新版設定。現行值、範圍、步進與 ACK 契約只由 [`CAPTURE_SPEC.md`](../CAPTURE_SPEC.md) 定義。
