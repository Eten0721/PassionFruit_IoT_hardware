# ADR-0008：舊四級分類 command mapping

- Status: Superseded
- Date: 2026-07-16
- Superseded by: [ADR-0009](0009-canonical-four-class-contract.md)

## Context

中文分類由舊級距改為上等、中等、下等、加工時，曾暫時沿用舊 ASCII code 與出口角度。

## Decision

過渡期使用 `high_medium` 與 `discard` 表示部分新中文分類，避免同時部署 Firmware 與 Django。

## Consequences

語意與實體出口不直覺，因此已被現行 canonical code 與角度契約取代。新程式不得依本 ADR 產生命令。
