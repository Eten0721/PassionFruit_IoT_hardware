# ADR-0017：Django 固定四模型部署權重

- Status: Accepted
- Date: 2026-09-28

## Context

自動檢測部署若直接讀取 ignored 的 `external/` checkout，新的環境無法只靠硬體 Repository 還原正式權重。系統現階段的推論角色固定為 ROI、色澤、皺褶與局部瑕疵四種。

## Decision

正式部署權重由硬體 Repository 的 `Django_Server/models/` 管理，檔名固定為 `ROI.pt`、`Color.pt`、`Wrinkle.pt`、`Defect.pt`，並使用 Git LFS。Django 不提供逐模型路徑設定；模型訓練、候選 checkpoint 與推論 pipeline 仍留在獨立模型 Repository。

## Consequences

部署 checkout 可直接取得版本一致的四個權重，不再依賴 `external/` 內的模型檔案。更新權重必須先在模型 Repository 完成驗收，再以固定檔名替換部署資產，重新執行真實四模型整合驗證後分別提交兩個 Repository。
