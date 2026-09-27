# ADR-0010：模型與硬體採獨立 Repository

- Status: Accepted
- Date: 2026-07-15

## Context

模型訓練需要自己的 Dataset、Git LFS、版本與協作節奏，直接納入硬體 Repository 會混淆責任並放大體積。

## Decision

模型訓練與推論 pipeline 維持獨立 Git Repository，本機可放在硬體 workspace 的 ignored `external/` 目錄。原始照片、訓練輸出與 prototype 權重不進 Git。Django 正式部署權重的例外與固定介面由 ADR-0017 定義。

## Consequences

兩邊分別 commit 與 push。Repository 責任邊界見 [`PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md)，本機操作見 [`external/README.md`](../../external/README.md)，正式部署權重見 [ADR-0017](0017-fixed-django-model-assets.md)。
