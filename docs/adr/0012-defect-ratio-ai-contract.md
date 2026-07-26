# ADR-0012：局部瑕疵比例與人工覆核 AI 契約

- Status: Accepted
- Date: 2026-07-22

## Context

品質判斷取決於瑕疵範圍與其他外觀特徵；現有自由文字 note 也不是可直接訓練的結構化特徵。

## Decision

局部瑕疵使用果實 ROI 內的 segmentation 面積比例，與相對大小、顏色及皺褶共同進入四分類。未偵測記為 `0`，ROI 或推論失敗記為 `null` 並交由人工覆核，不使用炭疽病一票否決。

## Consequences

現行特徵名稱、正規化與輸出介面由 [`decision_layer/README.md`](../../decision_layer/README.md) 定義。模型不得直接控制硬體。
