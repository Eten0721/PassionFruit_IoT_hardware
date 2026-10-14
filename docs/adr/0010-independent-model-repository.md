# ADR-0010：模型與硬體採獨立 Repository

- Status: Accepted
- Date: 2026-07-15

## Context

模型訓練需要自己的 Dataset、Git LFS、版本與協作節奏，直接納入硬體 Repository 會混淆責任並放大體積。

## Decision

模型訓練與研究推論 pipeline 維持獨立 Git Repository，本機 checkout 放在硬體 Repository 之外，不巢狀於硬體工作樹。原始照片、訓練輸出與 prototype 權重不進 Git。Django 的本地部署推論與固定權重介面由 ADR-0017 定義，部署不需要模型 checkout。

## Consequences

兩邊分別 commit 與 push。Repository 責任邊界見 [`PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md)，本機操作見 [模型開發環境](../模型開發環境.md)，正式部署權重見 [ADR-0017](0017-fixed-django-model-assets.md)。
