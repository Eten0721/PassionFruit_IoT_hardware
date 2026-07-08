# 個人身分
我的使用名稱為伊藤，逢甲大學資訊工程學系三年級學生。

# 偏好回覆方式
- 請使用繁體中文回覆問題。
- 中文之間使用全形標點符號，英文或者數字之兩側加上半形空白。
- 請無須在開頭加上「當然可以!」「沒問題!」等客套話，直接切入重點。

--- project-doc ---

## 專案背景

開始修改程式前，請先閱讀：

1. `docs/PROJECT_CONTEXT.md`
2. `docs/DATA_COLLECTION_SPEC.md`
3. `docs/CURRENT_STATUS.md`
4. `docs/DECISIONS.md`

目前資料採集流程已從「滾動中連拍 6 張」改為「三段 SG90 閘門停止拍攝 3 張」。舊版六連拍只保留為歷史背景，不得作為新功能的主要實作方向。

截至 `v1.1.5`，照片蒐集系統的最小可用流程已完成：手動拍攝、自動 HC-SR04 觸發、Django 中央狀態機、手機單張輪詢拍攝、ESP32 三閘門控制與三張照片採集皆可運作。

新的資料採集基準流程：

1. HC-SR04 偵測百香果滾入拍攝軌道，或使用者在 dashboard 按下手動拍攝。
2. HC-SR04 trigger 與 dashboard 手動拍攝只差在觸發來源，後續都必須走同一套 Django 中央狀態機流程。
3. ESP32 以 HTTPS client 身分輪詢 Django 取得馬達命令，不在 ESP32 上架設 API server。
4. 手機相機頁輪詢 Django 取得拍攝請求，每次只拍攝並上傳一個拍攝站點的單張照片。
5. Django 確認當站照片保存成功後，才設定下一個 ESP32 馬達命令。
6. 每顆百香果預設只保留 3 張照片，分別對應三個拍攝站點。
7. 照片先暫存在 `dataset/temp/fruit_XXX/`；使用者確認後再分類到「上中等 / 下等 / 加工 / 廢棄」。
8. 未分類暫存資料存在時，Django 會讓 `auto_trigger_enabled=0`，ESP32 不應開始下一顆自動拍攝流程。
9. 不得使用固定延遲猜測手機是否拍攝完成；手機拍攝完成必須以 Django 收到照片並保存成功為準。

Django 手動拍攝流程只取代 HC-SR04 的開始請求，不可繞過 ESP32 閘門控制，也不要再用短時間連續回傳 6 張照片作為主要流程。

## 目前硬體與 timing 基準

正式 firmware 位於 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。

- `HOME_ANGLE = 0`：攔截／歸位角度。
- `RELEASE_ANGLE = 90`：放行角度。
- HC-SR04 目前觸發距離為 `6.0 cm`，重新待命距離為 `8.0 cm`。
- `servo_settle_ms = 150`：SG90 轉動後的機械穩定時間。
- `fruit_settle_ms = 300`：第 2、3 站百香果停穩時間。
- `firstStationSettleMS = 100`：第 1 站開始拍攝前的停穩測試值。
- `finalGateReturnDelayMS = 300`：第 3 站放行後，三顆馬達歸位前的額外等待。
- ESP32 command polling：idle 約 `5000 ms`，等待 `start_sequence` 約 `100 ms`，流程進行中約 `120 ms`。
- HTTP timeout：`hcsr04_trigger` POST 約 `1000 ms`，command GET 約 `1500 ms`，一般 station report POST 約 `5000 ms`。
- 手機相機頁 `/api/state/` 採 single in-flight polling，idle / active 目前約 `200 ms`。

`hcsr04_trigger` POST 回應若已包含 `motor_command.command=start_sequence` 與 `command_id`，ESP32 會直接執行 `start_sequence`，不再多等一次 command polling。若 `hcsr04_trigger` POST timeout，ESP32 會先進入 fast command polling 嘗試找回 Django 已建立的 `start_sequence`，並保留 pending retry 作為容錯。

## Git 使用規則

- 修改程式前，先執行 `git status`。
- 大幅修改前，先說明預計修改的檔案與策略。
- 修改完成後，先顯示 `git diff` 或變更摘要，不要直接 commit。
- commit 前必須確認沒有提交 `secrets.h`、`.env`、Wi-Fi 密碼、API key、dataset 原始照片、模型權重或大型訓練輸出。
- 不要使用 `git reset --hard`，除非使用者明確要求。
- 回溯版本時，優先使用 `git revert` 或恢復指定檔案。

## 目前開發優先順序

1. 穩定照片蒐集流程：降低第一張照片體感延遲、改善 HTTPS timeout 韌性、調整照片清晰度。
2. 依實測結果微調 `triggerDistanceCM`、`firstStationSettleMS`、`fruit_settle_ms` 與閘門機構位置。
3. 強化錯誤復原：ESP32 斷線、手機未上傳、錯站照片、重複 trigger、未分類資料鎖定。
4. 維持 `metadata.csv` 與 `dataset/temp/fruit_XXX/` 到分類資料夾的資料一致性。
5. 三站資料採集穩定後，再整合 AI 推論與分類器控制。

## Python 執行環境

本專案使用 Anaconda 環境 `pf_iot_env`。
在 Windows 上執行 Django 測試時，一律使用：

```powershell
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

不要直接使用 `python manage.py test`，避免跑到系統預設 Python。
若需要安裝套件，使用：

```powershell
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe -m pip install <package>
```

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

- `Django_Server/`：電腦端 Django 伺服器，包含 API、前端頁面、dataset 管理與未來 AI 整合入口。
- `firmware/`：ESP32 Arduino code；正式三閘門資料採集 firmware 位於 `firmware/Three_Gate_Data_Collection/`。
- `model_training/`：YOLO training 相關資料。
- `decision_layer/`：機器學習決策層，例如 XGBoost 相關資料。
- `hardware_notes/`：硬體實作筆記。
- `docs/`：repo 文件。
- `Necessary_library/`：ESP32 周邊功能需要安裝的 library。
