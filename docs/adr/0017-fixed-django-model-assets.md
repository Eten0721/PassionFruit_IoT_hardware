# ADR-0017：Django 固定四模型部署權重

- Status: Accepted
- Date: 2026-09-28

## Context

自動檢測部署若依賴 ignored 的 `external/` checkout，新的環境無法只靠硬體 Repository 還原推論程式與正式權重。系統現階段的推論角色固定為 ROI、色澤、皺褶與局部瑕疵四種。

## Decision

正式部署推論由硬體 Repository 的 Django 維護，權重只從 `Django_Server/models/` 載入，檔名固定為 `ROI.pt`、`Color.pt`、`Wrinkle.pt`、`Defect.pt`，並使用 Git LFS。Django 不提供逐模型路徑設定，也不載入外部 checkout 的推論程式或資料；模型訓練、候選 checkpoint 與研究推論 pipeline 留在獨立模型 Repository。

## Consequences

部署 checkout 可直接取得推論程式與四個權重。開發者直接以固定檔名替換部署資產、重啟 Django，並重新執行真實四模型整合驗證；權重替換不要求模型 Repository 或外部資料。替換與驗收操作見 [`Django_Server/models/README.md`](../../Django_Server/models/README.md)。
