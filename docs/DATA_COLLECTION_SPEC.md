# 照片蒐集系統規格

## 目標

照片蒐集系統目前以「Django 中央狀態機 + 手機單張輪詢拍攝 + ESP32 三段 SG90 閘門」為正式資料採集流程。每顆百香果在 3 個拍攝站點各拍攝 1 張照片，總共保存 `img_01.jpg`、`img_02.jpg`、`img_03.jpg`。

舊版「滾動中連拍 6 張」不再作為主要流程。

## 名詞

- 拍攝工作階段：一顆百香果從開始觸發到三張照片完成的完整流程。
- 拍攝站點：第 1、2、3 張照片的位置。
- 拍攝請求：Django 狀態中通知手機拍攝某站照片。
- 照片上傳完成：Django 接收到該站照片並保存成功。
- 馬達命令：ESP32 從 Django 輪詢取得的 `start_sequence` 或 `release_gate` 命令。
- 分類器命令：人工資料分類成功後，透過同一命令槽下發的 `classify_fruit`；只帶 `command_id`、fruit id 與固定 ASCII code。
- 站點就緒：ESP32 回報百香果已在某站可拍攝。

## 硬體與 timing 參數

正式 firmware：`firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`

- `HOME_ANGLE = 0`：閘門攔截／歸位。
- `RELEASE_ANGLE = 90`：閘門放行。
- `triggerDistanceCM = 6.0`
- `rearmDistanceCM = 8.0`
- `sensorReadIntervalMS = 50`
- `idle_command_poll_interval_ms = 250`（Dashboard 可調 `100～5000 ms`，步進 `50 ms`）
- `startSequenceCommandPollIntervalMS = 100`
- `awaitReleaseCommandPollIntervalMS = 50`
- `servo_settle_ms = 200`
- `fruit_settle_ms = 350`
- `firstStationSettleMS = 300`
- `finalGateReturnDelayMS = 300`
- `autoTriggerReportTimeoutMS = 1000`
- `commandHttpTimeoutMS = 1500`
- `reportHttpTimeoutMS = 5000`
- `FirmwareConfig::kEnableAutoStation1FastPath = true`
- `FirmwareConfig::kEchoPulseTimeoutUS = 12000UL`

四個停穩設定與 `idle_command_poll_interval_ms` 的推薦值依序為 `300 / 200 / 350 / 300 / 250 ms`，由 Dashboard 的 `POST /api/capture_timing/` 管理並持久化為固定單一的 `runtime_config/capture_timing.json`。設定僅能在 Django 為 `idle` 時更新；停穩值範圍為 `50～3000 ms`、最終歸位可為 `0 ms`，idle polling 範圍為 `100～5000 ms`，全部以 `50 ms` 為步進。Django 以 revision 下發設定，ESP32 僅在 idle、沒有流程或 pending report 且所有 Gate home 時套用，並回報 `timing_config_applied`。舊版四欄設定會保留原值、補入 `250 ms` 並提高 revision。

每顆 fruit 開始後，ESP32 會 snapshot 四項 timing；`servo_settle_ms` 與 `fruit_settle_ms` 只用於硬體動作與果實停穩，不用來判斷手機是否拍攝完成，也不可在流程中覆寫。

HC-SR04 Echo 接到 ESP32 前必須降壓至 `3.3 V` 邏輯；正常目標為 ≤ `3.3 V`，絕不可超過 `3.6 V`。不可將 HC-SR04 的 `5 V` Echo 腳直接接到 GPIO。`12000 us` 是 Echo 等待上限，完整 `sensor_read_us` 的 firmware warning 門檻約為 `12100 us`。

## 觸發入口

### 自動模式

1. HC-SR04 偵測距離進入觸發範圍，ESP32 產生一個 `trigger_id`。
2. 若 `FirmwareConfig::kEnableAutoStation1FastPath=true`、firmware 為 idle、沒有進行中的 sequence、三個 Gate 的角度追蹤都在 `HOME_ANGLE` 且已等待 `firstStationSettleMS`，ESP32 POST `/api/esp32/report/` 回報 `hcsr04_station_1_ready`，並帶 `trigger_id`、`station_index=1`、`gates_home=true`、`station_settled=true`。Gate 1 已攔住果實是部署與機構驗證前提，不是此版本的硬體回授訊號。
3. Django 驗證 ESP32 宣告與自動觸發鎖定後，建立／找回 active fruit，直接開放第 1 站拍攝請求。
4. 捷徑未開啟、本地條件不符或收到可回退的協定拒絕時，ESP32 queue `hcsr04_trigger`，Django 建立 `start_sequence`，再走標準第 1 站交握。HTTP timeout 時只能重送相同 `trigger_id`；收到 `ignored` 時停止本次 trigger 並等待感測器重新待命。
5. `hcsr04_trigger` response 若已含 `start_sequence`，ESP32 直接執行；否則進入 fast command polling。

### 手動模式

1. 使用者在 dashboard 按下手動拍攝。
2. Django 建立 active fruit 與 `start_sequence` motor command。
3. ESP32 透過 command polling 取得 `start_sequence`。
4. 後續流程與自動模式相同。

手動拍攝只取代 HC-SR04 開始訊號，不可繞過 ESP32 三閘門流程。

## 三站握手流程

### 自動首站捷徑

自動模式的首站捷徑只壓縮第 1 站前的控制往返，不放寬拍照完成的判定。`gates_home` 與 `station_settled` 是 ESP32 的軟體安全宣告，不取代實機確認 Gate 1 的攔截位置：

1. ESP32 以同一個 `trigger_id` 與 `station_index=1` 回報 `hcsr04_station_1_ready`。
2. Django 確認 `gates_home` 與 `station_settled`，建立或重用工作階段並設定第 1 站拍攝請求。
3. 手機上傳 `img_01.jpg`，且 Django 原子保存成功後才建立 `release_gate_1`。
4. 第 2、3 站完全沿用下列標準交握。

### 標準與回退流程

1. Django 建立 `start_sequence`，並分配 `command_id`。
2. ESP32 取得 `start_sequence` 後回報 `station_1_ready`，必須帶同一個 `command_id`。
3. Django 設定第 1 站拍攝請求。
4. 手機輪詢 `/api/camera/state/`，拍攝並上傳 `img_01.jpg`。
5. Django 保存成功後建立 `release_gate_1`。
6. ESP32 取得 `release_gate_1`，Gate 1 轉到 `90` 度放行。
7. ESP32 等待 `servo_settle_ms` 與 `fruit_settle_ms`，回報 `station_2_ready`，必須帶 `release_gate_1` 的 `command_id`。
8. 第 2 站重複：手機上傳 `img_02.jpg`，Django 建立 `release_gate_2`，ESP32 回報 `station_3_ready`。
9. 第 3 站重複：手機上傳 `img_03.jpg`，Django 建立 `release_gate_3`。
10. ESP32 放行 Gate 3，等待百香果滾出，再額外等待 `finalGateReturnDelayMS`，三顆馬達歸位。
11. ESP32 回報 `capture_sequence_finished`，必須帶 `release_gate_3` 的 `command_id`。
12. Dashboard 以固定直式 `3:4` 縮圖顯示三張照片，點擊可在網頁內以原始比例彈窗預覽，等待使用者分類或刪除。

## 人工分類器流程

1. `capture_sequence_finished` 完成後，使用者才可按中文分類按鈕。
2. Django 先搬移資料夾、寫入 `metadata.csv`、推進 counter 並清除 active dataset 狀態。
3. 中文 label 依序映射為上等 `high`、中等 `medium`、下等 `low`、加工 `processing`，再建立 `classify_fruit`。新版 firmware 額外接受 `high_medium` 與 `discard` 作為舊 Django 的輸入 alias，但新命令不得再產生這兩個歷史代碼。
4. MG996R 依上等 `25°`、中等 `55°`、下等 `115°`、加工 `145°` 前往分類位置，保持 `1000 ms` 後回到 Home `85°` 並穩定 `500 ms`。
5. ESP32 回報完成或失敗；任何硬體錯誤都不回滾已分類資料。
6. Sorter pending／running 期間，同一 motor command slot 不得被新拍攝或另一筆分類覆蓋。

`classify_fruit` 不帶 `station_index`、GPIO、角度或 PWM。Sorter report 不帶 `station_index`，但必須帶相同 `command_id` 與 `classification_code`。

Sorter 狀態獨立使用 `idle／pending／running／completed／failed／timeout`，不得覆蓋既有拍攝 `status`。等待 ESP32 與執行後 timeout 均為 `30 秒`；firmware 自身動作 timeout 為 `5000 ms`。

照片資料完成分類後才嘗試 queue sorter command。若資料夾搬移、metadata 或 counter 操作失敗，不得建立 `classify_fruit`；若資料已成功分類但命令槽忙碌、command ID 無法持久化或 sorter 功能停用，API 仍回傳資料分類成功，並以 `data_classified=true`、`sorter_command_queued=false` 與具體原因說明硬體命令未建立，不得反向搬回資料或回滾 metadata。

Sorter report 進入既有 pending retry 後，firmware 必須立即消耗一次動作結果；重送 report 不得重新驅動 MG996R。Django 進入 terminal 狀態後收到 late duplicate report 時回傳 `200 ignored`，讓 ESP32 停止重送，但不得把既有 `timeout`、`failed` 或 `completed` 改寫成另一個結果。

command id 規則：

```text
start_sequence -> station_1_ready 使用 start_sequence command_id
release_gate_1 -> station_2_ready 使用 release_gate_1 command_id
release_gate_2 -> station_3_ready 使用 release_gate_2 command_id
release_gate_3 -> capture_sequence_finished 使用 release_gate_3 command_id
```

每次建立 capture 或 sorter command 前，Django 會讀取記憶體與 `Django_Server/runtime_config/motor_command_sequence.json` 的較大值，加一並原子保存，成功後才公開命令。檔案不存在時從 `1` 開始；格式損壞或寫入失敗時回傳 `motor_command_id_persist_failed` 並拒絕建立命令。Dataset reset 不重設 command ID；目前只持久化序列，不承諾 Django 重啟後恢復正在執行的實體命令。

## 自動觸發鎖定與冪等

Django 是是否允許開始新 fruit 的唯一狀態來源。

以下情況 `auto_trigger_enabled=0`：

- 已有 active fruit。
- `dataset/temp/fruit_XXX/` 有未分類暫存資料。
- 流程處於 `waiting_esp32_start`、`waiting_camera`、`uploaded`、`incomplete` 或 `error`。
- Sorter 處於 `pending` 或 `running`，或單一 motor command slot 尚有未清除命令。

當 `auto_trigger_enabled=0` 時，`hcsr04_trigger` 回 `200 ignored`。ESP32 收到 ignored 後清除 pending，等待感測器重新待命；不應建立新 fruit，也不應覆蓋既有 motor command。

Sorter `pending／running` 期間，Dashboard 必須停用手動拍攝與四個分類按鈕；Django 也必須拒絕 manual capture、recapture、dataset reset 與第二筆 sorter command。前端 single in-flight 只能降低連點機率，最終互斥仍由 operation token、active fruit 與單一命令槽 guard 保證。

使用者按「跳過／刪除」後，Django 會先嘗試實體刪除；若檔案仍被占用，則隔離到 `_delete_pending`，隔離也失敗時持久標記為待清理並跳過該 fruit ID。三種結果皆有結構化回應，流程會安全回到 `idle`，讓自動／手動觸發立即重新可用。

若 `hcsr04_trigger` 重送時 Django 已在 `waiting_esp32_start` 且既有 `start_sequence`，Django 回 `duplicate_trigger_waiting_start_sequence` 並保留既有 `motor_command`，ESP32 可據此進入 fast polling 或直接執行 response 內的 `start_sequence`。

## Timeout 與延遲處理

- `hcsr04_trigger` POST timeout 不代表 Django 一定沒收到。ESP32 會設定等待 `start_sequence` 狀態，優先 fast command polling。
- 首站捷徑 `hcsr04_station_1_ready` 的 timeout retry 必須重用原本的 `trigger_id`；不得以新的 id 猜測前一次失敗。legacy `hcsr04_trigger` 的重複防護由 Django active fruit 與既有 motor command 狀態負責。
- `hcsr04_trigger` pending retry 會保留，但不應每 `100 ms` 重送。
- `waitingStartSequenceCommand=true` 時，command GET timeout 不應進入 `10000 ms` idle backoff。
- ESP32 預設只印 response 摘要；若需要完整 Django JSON，才將 `verboseHttpResponseLog` 改為 `true`。
- Wi-Fi 重連與伺服等待以 deadline 驅動，不在主迴圈同步等待數秒。HC-SR04 無回波時最多阻塞 `12 ms`。
- HTTPS transport 的 connect 與 read 都使用該 request 的 deadline：自動 trigger `1000 ms`、command `1500 ms`、一般 report `5000 ms`。正常情況重用同 origin HTTP/1.1 TLS 連線，Wi-Fi 斷線、timeout、client 失效或 `Connection: close` 時停止 client 後重建。
- Sorter 在 ESP32 尚未取走命令時等待 `30 秒`，逾時原因為 `esp32_timeout`；第一次 command GET 取走後重新給 `30 秒` 執行期限，逾時原因為 `classifier_timeout`。Firmware 自身仍以 `5000 ms` 動作 timeout 優先安全歸位並回報。

## 手機相機頁

- `/camera/` 以 single in-flight polling 輪詢 `/api/camera/state/`；此端點僅供相機頁使用，主要傳回 `revision`、fruit、token、站點與 capture request，並保留 `status`、`active_fruit_id`、`pending_capture` 與 nested `capture` 相容欄位，且禁止快取。
- idle polling 為 `250 ms`，有 fruit／capture request 時為 `50 ms`，實際擷取或上傳期間為 `250 ms`；state request 使用 `AbortController`，逾時後才排下一輪。頁面進入背景時 polling 會停止，回到前景才重啟。
- `/api/camera/state/` 的高頻路徑只可讀取記憶體狀態與套用 session timeout，不得重複掃描 dataset、讀取 counter 或驗證 `metadata.csv` schema。dataset 初始化每個 root 只執行一次，完整檔案系統恢復由啟動、dashboard state 與資料異動流程負責。
- ESP32 只有在 idle、等待 `start_sequence` 或等待 `release_gate` 時輪詢 command；伺服移動、果實停穩與 report pending 階段不得送出無效 command GET。
- 每次只在 Django 指定站點時拍攝並上傳 1 張照片。
- 上傳必須包含 `fruit_id`、`capture_token`、`station_index`。
- `capture_started` 是 best-effort timing telemetry，不能等待其 HTTP response 才擷取 canvas 或上傳；`capture_meta` 只保留站點脈絡與 request、影格、blob、upload 開始的 client timing，不再帶舊六連拍的 `capture_interval_ms`、`timestamps_ms` 或 `intervals_ms`。
- Django 仍嚴格檢查 token、fruit id、站點與流程狀態。

## Dataset 與 metadata

暫存路徑：

```text
dataset/temp/fruit_XXX/
  img_01.jpg
  img_02.jpg
  img_03.jpg
```

分類級距：

```text
上等
中等
下等
加工
```

上等、中等與下等目前以皺褶、擦傷及顏色差異作為人工判斷因素，顏色包含綠色、橘色與黃色等情形；尚未定義量化門檻。系統不再提供「廢棄」分類，原本應判為廢棄的果實後續歸入加工。

`metadata.csv` 欄位：

```csv
fruit_id,label,capture_time,path,capture_count,station_01_ok,station_02_ok,station_03_ok,note
```

目前 `capture_count` 預期為 `3`。

`2026-07-16` 的凍結快照 `pf-20260716-v001` 含 `327` 顆與 `981` 張照片，分類分布為上等 `102`、中等 `65`、下等 `56`、加工 `104`。三站各 `327` 張且全部為 `1080 × 1920`；`metadata.csv` 只有人工分類 label 與未正規化 note，不能直接作為完整 XGBoost 特徵表。快照實體保存在 `D:\passion-fruit-datasets\pf-20260716-v001`，透過模型 repository 的 `dataset/pf-20260716-v001` 存取；目前 `split_status` 為 `pending_roboflow`，尚未建立 train／valid／test。

## 驗證指令

Windows Django 快速部署與網路設定見 [`README.md`](../README.md)，ESP32 board、library 與重新燒錄步驟見 [`Necessary_library/README.md`](../Necessary_library/README.md)。

Django 測試：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

啟動 HTTPS 開發伺服器時，依目前專案環境使用既有 `runsslserver` 設定。

## 實機驗收

1. 空軌連續 `100` 次 HC-SR04 讀取，確認 Echo 等待上限為 `12 ms`，完整 `sensor_read_us` 未跨過約 `12.1 ms` warning 門檻。
2. transition trace 可量測「Django 收到 `hcsr04_station_1_ready`」至 `img_01.jpg` 原子保存的伺服器端鏈路。健康網路下連續 `20` 次自動採集的 HC-SR04 實體偵測至保存總延遲，必須以同次 firmware Serial timing 與高速錄影／外部同步量測確認中位數不超過 `1.0 s`、`p95` 不超過 `1.5 s`。
3. 用高速錄影確認第 2、3 站在回報 ready 前已停止，再檢查三張照片的清晰度、temp 鎖定與分類後下一顆可觸發。
4. 分別模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站與重複 trigger，確認不會建立重複 fruit 或提前放行閘門。
