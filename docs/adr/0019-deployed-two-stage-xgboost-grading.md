# ADR-0019：部署兩階段 XGBoost 判級與固定資產

- Status: Accepted
- Date: 2026-10-14

## Context

組員交付的兩階段 XGBoost 要求 13 個原始特徵（含 color、wrinkle 與四類瑕疵 confidence）及 3 個衍生面積特徵。模型 Repository 的 Decision Dataset 規格仍寫著第一版 baseline 只用 7 個特徵、禁止 confidence；若部署沿用該限制，模型無法以訓練時的輸入執行。

## Decision

- 部署判級以交付模型的特徵名稱與順序為契約：16 個特徵含 confidence，模型檔內的 `feature_names` 必須完全相符，否則拒絕載入。7 特徵 baseline 只描述模型 Repository 的早期研究，不約束本次交付模型。
- 特徵語意沿用 Decision Dataset 的單張欄位定義（ROI bbox 面積比、`bad_color=1`、`true(有皺褶)=1`、top-1 confidence、ROI 內 mask union 面積比與最高 confidence）；成功無瑕疵為 `0`，缺值不補零並交由人工處理，延續 [ADR-0012](0012-defect-ratio-ai-contract.md)。
- 兩個模型固定命名為 `Django_Server/models/GradeStage1.json`、`GradeStage2.json`，與 [ADR-0017](0017-fixed-django-model-assets.md) 的四個 YOLO 權重一樣由 Git LFS 管理。Django 以 `xgboost.Booster` 載入，不引入 scikit-learn；結果以檔案 SHA-256 與 xgboost 版本識別模型。

## Consequences

替換判級模型只需以相同檔名覆蓋並重啟 Django，但新模型必須維持同一組特徵契約，並重新完成固定特徵與真實照片比對。交付模型的訓練資料、所用 YOLO 權重與訓練前處理證據目前不在任一 Repository；部署特徵與訓練一致性只能依規格與交付範例核對，尚未經訓練資料驗證。
