## 專案背景

開始修改程式前，請先閱讀：

1. `docs/PROJECT_CONTEXT.md`
2. `docs/DATA_COLLECTION_SPEC.md`
3. `docs/CURRENT_STATUS.md`
4. `docs/DECISIONS.md`

目前資料採集流程已從「滾動中連拍 6 張」改為「三段 SG90 閘門停止拍攝 3 張」。請不要再依照舊版六連拍流程實作新功能。

## Git說明

請在每次要大改架構前，先讓我查看狀態、改完後讓我確認好後，再commit

## 目前開發優先順序

1. 更新專案文件，移除舊版「滾動中連拍 6 張」作為主要流程的描述。
2. 以「三段 SG90 閘門停止拍攝」作為新的資料採集流程。
3. 先完成 Django 手機相機拍照與 dashboard 控制流程。
4. 每顆百香果在三個拍攝站點各拍攝 1 張照片，預設共 3 張。
5. 照片先暫存在 `dataset/temp/fruit_XXX/`。
6. 使用者確認照片後，再將整個 `fruit_XXX` 資料夾分類到「上中等 / 下等 / 加工 / 廢棄」其中一個目錄。
7. 每次成功分類後，必須寫入 `metadata.csv`。
8. Django 手動流程穩定後，再加入 ESP32 控制三段 SG90 閘門。
9. ESP32 三段閘門流程穩定後，再整合 AI 推論與分類器控制。

## Python 執行環境

本專案使用 Anaconda 環境 `pf_iot_env`。
在 Windows 上執行 Django 測試時，一律使用：
C:\Users\qoqo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app

不要直接使用 `python manage.py test`，避免跑到系統預設 Python。
若需要安裝套件，使用：
C:\Users\qoqo\anaconda3\envs\pf_iot_env\python.exe -m pip install <package>

## 程式風格

- 優先使用 guard clauses / early returns 處理錯誤輸入與前置條件失敗。
- 避免過深的 nested if/else。
- 主要成功流程應保持扁平、清楚、可測試。
- 不要過度使用 guard clauses 破壞主要分類邏輯的連貫性。
- 函式應盡量短小，單一函式只處理一個明確責任。
- 對於無效參數、設定檔錯誤、模型路徑不存在等程式設計或設定問題，使用明確 exception。
- 對於相機 unavailable、影像 capture failed、fruit not detected、low confidence、motor failed 等預期可能發生的問題，使用結構化回傳值，例如：
  `{ "ok": false, "reason": "camera_unavailable" }`
- 錯誤原因要具體，不要只回傳 `False` 或 `None`。

## 硬體與資源管理

- 控制 camera、serial port、GPIO、motor controller、file 時，必須確保資源會被正確釋放。
- 若有 early return，不能跳過必要的 cleanup。
- 優先使用 context manager 或 try/finally。
- 硬體狀態異常時，安全性優先於繼續執行。

## 資料夾結構與用途
- `Django_Server/`: 放電腦端的伺服器，內含有API函式、YOLO模型、決策模型、前端網站程式碼等內容。
- `firmware/`: 存放ESP32的Arduino Code。
- `model_training/`: 存放著YOLO training的資料。
- `decision_layer`: 存放著機器學習(XGBoost)的資料。
- `hardware_notes/`: 存放著硬體實作的筆記內容。
- `docs/`: 存放repo用文件。
- `Necessary_library/`: 存放為了開發ESP32周邊功能需要安裝的library。
