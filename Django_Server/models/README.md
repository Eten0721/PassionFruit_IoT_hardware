# 正式部署模型

Django 只從本目錄載入四個經驗收的正式權重：

- `ROI.pt`
- `Color.pt`
- `Wrinkle.pt`
- `Defect.pt`

檔案由 Git LFS 管理。開發者直接以這四個固定檔名替換本目錄權重後重啟 Django，不需要外部模型 Repository 或資料。新權重的類別與任務須符合 [CAPTURE_SPEC.md](../../docs/CAPTURE_SPEC.md#檢測保存與結束)，並重新完成四模型載入、三張照片推論及產物驗收。
