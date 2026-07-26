# Domain Docs

工程技能探索 codebase 前，依此 single-context 配置讀取文件。

## Before exploring

- 先讀取 [`docs/PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md)。
- 依工作範圍讀取 [`docs/adr/`](../adr/) 中相關 ADR。
- 現行流程與硬體契約讀取 [`docs/DATA_COLLECTION_SPEC.md`](../DATA_COLLECTION_SPEC.md)。
- 檔案不存在時直接繼續，不要求預先建立替代文件。

本 Repository 不另建重複的根目錄 `CONTEXT.md`；`docs/PROJECT_CONTEXT.md` 是 single-context 架構來源。

## Consumer rules

- Issue、規格、重構、假設與測試名稱沿用專案文件既有詞彙。
- 新詞彙先確認是否真是新的 domain concept。
- 工作若與既有 ADR 衝突，必須明確指出，不可靜默覆寫。
- 現行規格與歷史決策衝突時，以 Accepted ADR 與 `DATA_COLLECTION_SPEC.md` 為準。
