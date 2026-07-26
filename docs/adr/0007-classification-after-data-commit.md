# ADR-0007：資料提交後才執行實體分類

- Status: Accepted
- Date: 2026-07-12

## Context

MG996R 無位置回授，若把硬體成功當成資料 transaction 前提，斷線或卡住會使照片與 metadata 不一致。

## Decision

Django 先完成照片搬移、metadata 與 counter，再嘗試建立 `classify_fruit`。Capture 與 sorter 共用單一 command slot，command ID 原子持久化；Firmware 依 ID 去重動作。

## Consequences

硬體失敗或 timeout 不回滾已提交資料。Pending／running sorter 鎖住下一次 capture；report retry 不得再次驅動分類器。
