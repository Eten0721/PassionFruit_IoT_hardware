# ADR-0026：長按 BOOT 後「確認安全復原」直接完成

- Status: Accepted
- Date: 2026-11-20
- Extends: [ADR-0020](0020-single-network-recovery-policy.md)、[ADR-0025](0025-esp32-held-boot-local-reset.md)

## Context

2026-11-20 `test_operation_fuzz.py` 找到最短重現：運轉中失聯 → ESP32 上鎖 → 長按 BOOT `10 s` → 按「確認安全復原」。Django 仍鎖定並送出 `recover_network`，但 Firmware 在未上鎖時忽略該命令，交握永遠不會完成，只能改按 runtime reset。操作員長按 BOOT 後自然會按確認，畫面卻沒有反應。

## Decision

Django 鎖定期間，若曾在這把鎖的 recovery generation 下收到 ESP32 回報已上鎖（`board_lock_seen`，隨復原紀錄持久保存），之後同一 generation 的 ESP32 在線、回報未上鎖且 `motion=idle`，人工確認時直接解除失聯鎖與分類、Gate 3 復原紀錄，不建立 `recover_network`，並依原復原政策恢復自動運轉。

- 只有長按 BOOT 能讓已上鎖的 ESP32 在同一 generation 回到未上鎖，而長按 BOOT 已讓閘門與分類器歸位。
- 資料鎖不變：仍須先清理本輪暫存資料並明確確認。
- ESP32 仍上鎖、非閒置或從未在這把鎖下回報上鎖時，照舊走歸位交握。

契約只在 [CAPTURE_SPEC.md](../CAPTURE_SPEC.md#失聯門檻與安全交握) 維護。

## Consequences

長按 BOOT 後兩個按鈕都能脫離：「確認安全復原」或 runtime reset。歸位交握的安全保證改由長按 BOOT 的本機歸位與操作員確認取代，只適用上述條件。不需重新燒錄 Firmware。
