# 外部 Repository 本機操作

Repository 責任邊界以 [`docs/PROJECT_CONTEXT.md`](../docs/PROJECT_CONTEXT.md) 為準。本文件只說明本機 checkout 與 Dataset junction 的操作方式。

## 模型 Repository

- GitHub：[`fcu-passionfruit-project/ps-quality-detection-system`](https://github.com/fcu-passionfruit-project/ps-quality-detection-system)
- 本機位置：`external/ps-quality-detection-system/`
- Dataset 實體：`D:\passion-fruit-datasets\`
- 模型端 `dataset/`：ignored Windows junction，指向 Dataset 實體目錄

首次建立 checkout：

```powershell
git clone https://github.com/fcu-passionfruit-project/ps-quality-detection-system.git external\ps-quality-detection-system
```

修改後必須在硬體與模型 Repository 各自執行 `git status`、commit 與 push。不可在父層執行 `git add external/ps-quality-detection-system`。

Junction 不是備份。快照數量、切分與備份狀態以 [`docs/CURRENT_STATUS.md`](../docs/CURRENT_STATUS.md) 為準。

## Django 開發整合

模型 Repository 整理成可匯入 package 後，可在硬體專案環境安裝 editable checkout：

```powershell
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe -m pip install -e .\external\ps-quality-detection-system
```

Adapter 的特徵與輸出契約見 [`decision_layer/README.md`](../decision_layer/README.md)。正式整合前不得讓模型直接控制硬體。
