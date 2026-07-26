# ADR-0001：Django 中央三站採集流程

- Status: Accepted
- Date: 2026-07-06

## Context

滾動連拍容易產生模糊照片，手機與 ESP32 也不能可靠推測彼此何時完成。

## Decision

Django 作為中央狀態來源；ESP32 控制三段閘門，手機依 Django 指定站點各上傳一張照片。手動拍攝只取代開始訊號，後續仍走相同硬體流程。

## Consequences

每站必須等 Django 原子保存照片後才放行。現行事件、API 與硬體契約由 [`DATA_COLLECTION_SPEC.md`](../DATA_COLLECTION_SPEC.md) 定義。
