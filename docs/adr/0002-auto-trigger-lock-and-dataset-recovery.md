# ADR-0002：自動觸發鎖定與 Dataset 復原

- Status: Accepted
- Date: 2026-07-07

## Context

未分類資料或未完成硬體命令存在時開始下一顆，會覆蓋 active fruit、command 或照片狀態。

## Decision

Django 統一決定能否觸發。Active fruit、未分類 temp、非 idle capture、pending／running sorter 或未清命令都鎖住新流程。刪除受 Windows 占用時先隔離，仍失敗則持久標記待清理；成功路徑必須回到 idle。

## Consequences

ESP32 收到 ignored 後等待感測器重新待命。清理失敗不得假裝成功，也不得長期阻塞後續採集。
