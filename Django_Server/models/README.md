# 正式部署模型

Django 只從本目錄載入經驗收的正式模型：

- YOLO：`ROI.pt`、`Color.pt`、`Wrinkle.pt`、`Defect.pt`
- 兩階段 XGBoost 判級：`GradeStage1.json`（加工／非加工）、`GradeStage2.json`（下等／中等／上等）

檔案由 Git LFS 管理。開發者直接以相同固定檔名替換後重啟 Django，不需要外部模型 Repository 或資料。新 YOLO 權重的類別與任務、新 XGBoost 模型的 16 個特徵名稱與順序須符合 [CAPTURE_SPEC.md](../../docs/CAPTURE_SPEC.md#檢測與判級) 與 [ADR-0019](../../docs/adr/0019-deployed-two-stage-xgboost-grading.md)，並重新完成模型載入、`fruit_app.test_grading` 與三張照片推論驗收。結果以模型檔 SHA-256 識別版本。
