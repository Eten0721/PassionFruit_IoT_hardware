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

截至 `v1.2.7`，系統已完成三站停止拍攝、首站加速、人工分類與 MG996R 實體分類整合：Django 中央狀態機、手機單張輪詢拍攝、ESP32 三閘門控制、三張照片採集、`trigger_id` 首站捷徑冪等處理、transition trace、照片原子保存、單一 motor command slot 互斥、持久化 command ID 與可調整的閒置命令輪詢皆可運作；並依 `2026-07-16` 實地考察結果將中文分類與 dataset 更新為上等、中等、下等、加工。

模型訓練與檢測層採獨立 repository：`fcu-passionfruit-project/ps-quality-detection-system`。模型 repository 本機放在 `external/ps-quality-detection-system/`，保留自己的 `.git`、GitHub 遠端與 Git LFS，並由父層硬體 repository 忽略；兩邊仍須分別 commit 與 push。本機硬體 workspace 位於 `D:\PassionFruit_IoT_hardware\`，照片資料位於 `D:\passion-fruit-datasets\`。

首張照片加速以「受守門的自動首站捷徑」為預設策略。它只壓縮第 1 站前的 HTTPS 控制往返，不得改變「站點停穩 → 手機單張照片保存成功 → 放行下一閘門」的安全規則。

新的資料採集基準流程：

1. HC-SR04 偵測百香果滾入拍攝軌道，或使用者在 dashboard 按下手動拍攝。
2. 自動模式只有在 firmware 為 idle、沒有進行中的 sequence、三個 Gate 都追蹤為 `HOME_ANGLE`，且已等待 `firstStationSettleMS` 後，才可送出 `hcsr04_station_1_ready`。此捷徑依賴 Gate 1 已由實際機構攔住果實的部署前提，並非額外的果實存在感測。
3. 首站捷徑 report 必須帶同一個 `trigger_id`、`gates_home=1`、`station_settled=1` 與第 1 站資訊。收到可回退的協定拒絕時才回退 `hcsr04_trigger → start_sequence → station_1_ready`；HTTP timeout 只能重送原本的 `trigger_id`，不得直接假定失敗。收到 `ignored` 時停止本次 trigger，等待感測器重新待命。
4. 手動拍攝與首站捷徑回退流程仍由 Django 中央狀態機建立 `start_sequence`，不可繞過 ESP32 閘門控制。
5. ESP32 以 HTTPS client 身分輪詢 Django 取得馬達命令，不在 ESP32 上架設 API server。
6. 手機相機頁輪詢 Django 取得拍攝請求，每次只拍攝並上傳一個拍攝站點的單張照片。
7. Django 確認當站照片保存成功後，才設定下一個 ESP32 馬達命令。
8. 每顆百香果預設只保留 3 張照片，分別對應三個拍攝站點。
9. 照片先暫存在 `dataset/temp/fruit_XXX/`；使用者確認後再分類到「上等 / 中等 / 下等 / 加工」。上等、中等與下等依皺褶、擦傷及顏色差異判斷；不再提供廢棄級距，原本應判為廢棄的果實歸入加工。
10. 未分類暫存資料存在時，Django 會讓 `auto_trigger_enabled=0`，ESP32 不應開始下一顆自動拍攝流程。
11. 不得使用固定延遲猜測手機是否拍攝完成；手機拍攝完成必須以 Django 收到照片並保存成功為準。

Django 手動拍攝流程只取代 HC-SR04 的開始請求，不可繞過 ESP32 閘門控制，也不要再用短時間連續回傳 6 張照片作為主要流程。

## 目前硬體與 timing 基準

正式 firmware 位於 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。入口檔只能建立 `CaptureController` 並呼叫 `begin()`／`tick()`；控制邏輯依責任放入下列模組，不得再分散至多個 `.ino` tab：

- `Config.h`：腳位、timing、timeout 與 feature flag。
- `ProtocolTypes.h`：馬達命令與 HTTP 結果型別。
- `DistanceSensor.*`：HC-SR04 Trigger／Echo 讀值。
- `GateController.*`：SG90 閘門與角度追蹤。
- `ClassifierController.*`：MG996R 分類角度、歸位與非阻塞狀態機。
- `DjangoApiClient.*`：Wi-Fi、HTTPS、keep-alive 與協定解析。
- `CaptureController.*`：三站狀態機、首站捷徑、回退、分類命令互斥與 pending retry。

Django 已將可獨立責任抽出為 `capture_session.py`、`dataset_store.py` 與 `webrtc_signaling.py`；`views.py` 仍負責 HTTP 整合與資料集生命週期，後續修改時應優先擴充對應模組，不要誤稱為已完全薄化的 view 層。

- `HOME_ANGLE = 0`：攔截／歸位角度。
- `RELEASE_ANGLE = 90`：放行角度。
- MG996R 使用 GPIO `25`，Home 為 `85°`；上等、中等、下等、加工分別為 `25°`、`145°`、`55°`、`115°`。為相容既有 ESP32，ASCII code 仍依序使用 `high_medium`、`discard`、`low`、`processing`；`discard` 現在代表中等。分類位置保持 `1000 ms`，歸位穩定 `500 ms`，動作 timeout 為 `5000 ms`。
- HC-SR04 目前觸發距離為 `6.0 cm`，重新待命距離為 `8.0 cm`。
- Dashboard timing profile 的推薦值依序為：`first_station_settle_ms = 300`、`servo_settle_ms = 200`、`fruit_settle_ms = 350`、`final_gate_return_delay_ms = 300`、`idle_command_poll_interval_ms = 250`（單位皆為 `ms`）。`Config.h` 只保留斷線／首次刷入時的 fallback。
- `POST /api/capture_timing/` 只能在 Django 為 `idle`、沒有 active fruit 或 motor command 時更新完整五項設定。所有數值必須為 `50 ms` 的倍數；四項機構 timing 的上限為 `3000 ms`，最終歸位延遲可為 `0 ms`，idle command polling 範圍為 `100～5000 ms`。
- Django 以 `Django_Server/runtime_config/capture_timing.json` 持久化 timing revision；每次 Dashboard 更新只原子覆寫此固定單一檔案，舊版 `dataset/capture_timing.json` 首次升級時會遷移後移除。ESP32 必須僅在 idle 且所有 Gate home 時套用，並回報 `timing_config_applied`。流程中不得覆寫目前 fruit 已 snapshot 的 timing。
- 成功跳過／刪除 temp fruit 後必須透過 `_clear_active_state(..., status='idle')` 清除等待計時器、馬達命令、capture token 與 fast-path 狀態，讓下一顆可立即開始。
- ESP32 command polling：idle 預設 `250 ms` 且可由 Dashboard 調整，等待 `start_sequence` 固定 `100 ms`，等待 `release_gate` 固定 `50 ms`；伺服移動、果實停穩與 report pending 階段不輪詢 command。
- HC-SR04 使用直接 `10 us` Trigger pulse 搭配 `pulseIn(..., 12000UL)`；無回波回傳 `0.0F`。Echo 等待上限為 `12 ms`，完整 `sensor_read_us` warning 門檻約為 `12.1 ms`。
- Wi-Fi 重連、伺服 phase 與 retry 使用 deadline 驅動；不得在主迴圈以多秒同步等待。
- HTTPS connect 與 read 都使用 request deadline：自動 trigger／首站捷徑約 `1000 ms`，command GET 約 `1500 ms`，一般 station report 約 `5000 ms`。正常情況重用同 origin HTTP/1.1 TLS 連線；Wi-Fi 斷線、timeout、client 失效或 `Connection: close` 時，必須關閉 client 後安全重建。
- 手機相機頁使用 `/api/camera/state/` 的 single in-flight polling：idle 約 `250 ms`，有 fruit／拍攝請求約 `50 ms`，實際擷取或上傳期間約 `250 ms`，state request timeout 為 `1000 ms`。頁面進入背景時 polling 會停止，回到前景才重啟，因此手機不可鎖屏或背景化。

人工分類成功後，Django 才會將中文分類映射為固定 ASCII code，並嘗試建立 `classify_fruit`。Sorter 為 pending／running 時必須鎖住自動觸發、手動拍攝、recapture、reset 與第二筆分類命令；硬體失敗或 timeout 不得回滾已搬移的照片、metadata 或 counter。ESP32 只依 command ID 去重實體動作，report retry 不得讓 MG996R 重複轉動。

MG996R 必須使用獨立外部電源並與 ESP32 共地，不得由 ESP32 供電。分類器目前沒有位置回授，因此 completed 只代表控制時序完成並成功回報，不代表已量測到機械實際到位。

`hcsr04_trigger` POST 回應若已包含 `motor_command.command=start_sequence` 與 `command_id`，ESP32 會直接執行 `start_sequence`，不再多等一次 command polling。若 `hcsr04_trigger` POST timeout，ESP32 會先進入 fast command polling 嘗試找回 Django 已建立的 `start_sequence`，並保留 pending retry 作為容錯。

`trigger_id` 的 Django 端冪等僅適用首站捷徑 `hcsr04_station_1_ready`；legacy `hcsr04_trigger` 沿用 Django 的 active fruit 與既有 motor command 狀態防止重複建立資料。

HC-SR04 的 `5 V` Echo 不可直接接 ESP32 GPIO `27`。必須使用分壓或邏輯電平轉換，正常目標為 ≤ `3.3 V`，絕不可超過 `3.6 V`；HC-SR04、ESP32 與伺服外接電源必須共地。刷入新版 firmware 前，依 `hardware_notes/HC-SR04_ESP32_3V3_安全檢查.md` 完成接線與電壓檢查。

## Git 使用規則

- 修改程式前，先執行 `git status`。
- 大幅修改前，先說明預計修改的檔案與策略。
- 修改完成後，先顯示 `git diff` 或變更摘要，不要直接 commit。
- 硬體 repository commit 前必須確認沒有提交 `secrets.h`、`.env`、Wi-Fi 密碼、API key、dataset 原始照片、模型權重或大型訓練輸出。團隊模型 repository 僅能以 Git LFS 提交已驗收的正式權重，仍不得提交照片、`runs/`、`last.pt` 或歷史 checkpoint。
- firmware 或 Django 狀態機變更後，至少執行 Django 測試、目前板型的 firmware 編譯與 `git diff --check`。
- commit 前使用 `git diff --cached --name-only` 與 `git diff --cached` 檢查 staged 內容；優先明確指定 `git add <檔案>`，不要未檢查地提交所有未追蹤檔案。
- 不得提交 `firmware/**/secrets.h`，只可提交 `secrets.example.h`。
- 影響首站捷徑、Echo 電壓或三站交握時，必須同步更新 `PROJECT_CONTEXT.md`、`DATA_COLLECTION_SPEC.md`、`CURRENT_STATUS.md`、`DECISIONS.md` 與必要的 hardware note。
- 不要使用 `git reset --hard`，除非使用者明確要求。
- 回溯版本時，優先使用 `git revert` 或恢復指定檔案。

## 目前開發優先順序

1. 實機驗收首站加速：空軌 `100` 次確認 Echo timeout、健康網路下 `20` 次自動採集確認首張延遲、以高速錄影確認第 2、3 站 ready 前已停止。
2. 依實測結果微調 `triggerDistanceCM`、`firstStationSettleMS`、`fruit_settle_ms` 與閘門機構位置。
3. 強化錯誤復原：ESP32 斷線、手機未上傳、錯站照片、重複 trigger、未分類資料鎖定。
4. 維持 `metadata.csv` 與 `dataset/temp/fruit_XXX/` 到分類資料夾的資料一致性。
5. 維持目前人工分類按鈕與 MG996R 命令協定；三站資料採集穩定後，再將 AI 推論結果接到同一套 `classify_fruit` 流程。

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
- `external/`：放置不由父層 Git 追蹤的外部 repository；目前模型 workspace 位於 `external/ps-quality-detection-system/`，相關邊界見 `external/README.md`。
- `decision_layer/`：機器學習決策層，例如 XGBoost 相關資料。
- `hardware_notes/`：硬體實作筆記。
- `docs/`：repo 文件。
- `Necessary_library/`：ESP32 快速燒錄與必要依賴說明；舊函式庫、安裝檔與編譯產物不納入 Git。
