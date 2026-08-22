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

## 共用 `PF` 開發環境

硬體與模型 Repository 共用 Python `3.14.4` 的 `PF` Conda 環境，但各自維護直接依賴。在 Repository 根目錄執行：

```powershell
C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s -m pip install -r .\Django_Server\requirements.txt
C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s -m pip install -r .\external\ps-quality-detection-system\requirements.txt
```

全新重建 `PF` 時，先依模型 Repository 的 [`README.md`](ps-quality-detection-system/README.md) 從 PyTorch 官方 CUDA `12.8` index 安裝 PyTorch，再安裝兩邊的 `requirements.txt`。模型端尚未提供可安裝 package，因此目前不執行 editable install。

指令使用 Python `-s` 停用 user-site，避免使用者層套件遮蔽 `PF` 內的版本。

Adapter 的特徵與輸出契約見 [`decision_layer/README.md`](../decision_layer/README.md)。正式整合前不得讓模型直接控制硬體。
