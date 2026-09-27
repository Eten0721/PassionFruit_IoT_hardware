# 正式部署模型

Django 只從本目錄載入四個經驗收的正式權重：

- `ROI.pt`
- `Color.pt`
- `Wrinkle.pt`
- `Defect.pt`

檔案由 Git LFS 管理。模型訓練、候選權重與推論 pipeline 仍由獨立模型 Repository 維護；替換本目錄權重前必須重新完成四模型載入、三張照片推論及產物驗收。
