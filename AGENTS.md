## 專案背景

開始修改程式前，請先閱讀：

1. `docs/PROJECT_CONTEXT.md`
2. `docs/DATA_COLLECTION_SPEC.md`
3. `docs/CURRENT_STATUS.md`
4. `docs/DECISIONS.md`

目前資料採集流程已從「滾動中連拍 6 張」改為「三段 SG90 閘門停止拍攝 3 張」。請不要再依照舊版六連拍流程實作新功能。

新的資料採集基準流程：

1. HC-SR04 偵測百香果滾入拍攝軌道，或使用者在 dashboard 按下手動拍攝。
2. HC-SR04 trigger 與 dashboard 手動拍攝只差在觸發來源，後續都必須走同一套 Django 中央狀態機流程。
3. ESP32 以 HTTPS client 身分輪詢 Django 取得馬達命令，不在 ESP32 上架設 API server。
4. 手機相機頁輪詢 Django 取得拍攝請求，每次只拍攝並上傳一個拍攝站點的單張照片。
5. Django 確認當站照片保存成功後，才設定下一個 ESP32 馬達命令。
6. SG90 閘門以 `45` 度作為攔截角度，以 `0` 度作為放行角度。
7. `servo_settle_ms` 與 `fruit_settle_ms` 只用於馬達轉動、百香果滾動與停穩，初始可調範圍建議 `100` 到 `300` ms。
8. 不得使用固定延遲猜測手機是否拍攝完成；手機拍攝完成必須以 Django 收到照片並保存成功為準。
9. 每顆百香果預設只保留 3 張照片，分別對應三個拍攝站點。

Django 手動拍攝流程只取代 HC-SR04 的開始請求，不可繞過 ESP32 閘門控制，也不要再用短時間連續回傳 6 張照片作為主要流程。

## Git 使用規則

- 修改程式前，先執行 `git status`。
- 大幅修改前，先說明預計修改的檔案與策略。
- 修改完成後，先顯示 `git diff` 或變更摘要，不要直接 commit。
- commit 前必須確認沒有提交 secrets、.env、Wi-Fi 密碼、API key、dataset 原始照片、模型權重或大型訓練輸出。
- 不要使用 `git reset --hard`，除非使用者明確要求。
- 回溯版本時，優先使用 `git revert` 或恢復指定檔案。

## 目前開發優先順序

1. 更新專案文件，移除舊版「滾動中連拍 6 張」作為主要流程的描述。
2. 以「ESP32 三段 SG90 閘門停止拍攝」作為新的資料採集核心流程。
3. 先完成 Django 中央狀態機、手機單張輪詢拍攝、ESP32 輪詢馬達命令能共同運作的最小可用流程。
4. 支援兩種開始方式：HC-SR04 自動觸發，以及 dashboard 手動建立開始請求；兩者後續都進入同一套三站握手流程。
5. 每顆百香果在三個拍攝站點各拍攝 1 張照片，預設共 3 張。
6. 照片先暫存在 `dataset/temp/fruit_XXX/`。
7. 使用者確認照片後，再將整個 `fruit_XXX` 資料夾分類到「上中等 / 下等 / 加工 / 廢棄」其中一個目錄。
8. 每次成功分類後，必須寫入 `metadata.csv`。
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
