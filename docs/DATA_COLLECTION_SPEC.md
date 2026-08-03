# 送料、三站資料採集與分類規格

## 目標與範圍

正式流程由 Django 中央狀態機、ESP32 以 360° MG996R 執行上游單顆送料、HC-SR04、手機單站拍攝、三段 SG90 閘門與位置型 MG996R 分類器組成。每顆百香果在三個固定站點各保存一張照片，人工分類完成且實體分類器回報成功後才允許送入下一顆。本文是送料、三站流程、API、command ID、GPIO、角度、timing 與分類契約的唯一來源。

上游送料機構由 GitHub Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 開發；本文件記錄已接受的目標契約，實作與實機驗收進度以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 為準。分類器擋臂使用第 4 顆 SG90，仍由 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 規劃。

## 名詞

- Capture session：一顆果實從觸發至三站完成的工作階段。
- Station ready：ESP32 宣告果實已在指定站點停穩。
- Capture request：Django 通知手機拍攝指定站點。
- Upload complete：Django 驗證並原子保存指定照片。
- 自動運轉：操作員啟用後，Django 只在安全邊界建立下一次送料命令的運轉模式；Django 或 ESP32 重新啟動後不自動恢復。
- 送料循環：連續旋轉 MG996R 從開始驅動至停止的一次 `feed_one` 實體動作；成功契約是實體恰好送出一顆，不是旋轉固定角度。HC-SR04 只確認有果實抵達，不能計數。
- 感測區清空：HC-SR04 取得一次有效且大於 `8.0 cm` 的距離；`0 cm`／Echo timeout 不是清空。
- 果實抵達確認：送料期間 HC-SR04 取得一次有效且小於等於 `6.0 cm` 的距離；同一筆讀值正常終止送料並啟動首站流程。
- 送料安全逾時：`feeder_max_run_ms` 先於果實抵達確認到期；ESP32 立即停止馬達，Django 暫停自動送料且不得自動補轉。
- 進料未確認：送料因安全逾時、重新啟動或不確定結果停止，但 HC-SR04 尚未確認果實抵達；不得推測為料斗已空。
- 優雅暫停：立即禁止建立後續送料命令，已承諾送出的目前果實仍完成拍攝與實體分類。
- Motor command：`feed_one`、`start_sequence`、`release_gate_N` 或 `classify_fruit`。
- Command slot：送料、Capture 與 sorter 共用的單一命令槽。

## 硬體設定

正式 Firmware：`firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`

| 裝置 | GPIO | 現行設定 |
|---|---:|---|
| Gate 1 SG90 | `18` | Home `0°`，Release `90°` |
| Gate 2 SG90 | `19` | Home `0°`，Release `90°` |
| Gate 3 SG90 | `21` | Home `0°`，Release `90°` |
| Upstream feeder MG996R 360° | `23` | HC-SR04 回授停止，最大運轉時間安全保護 |
| HC-SR04 Trigger | `26` | `10 us` pulse |
| HC-SR04 Echo | `27` | `12000 us` timeout，輸入必須安全降壓 |
| Position-control MG996R sorter | `25` | Home `85°` |

第 4 顆 SG90 作為分類器擋臂；其 GPIO、角度與 timing 尚未接受，由 Issue #2 決定前不得寫入正式 Firmware 或本規格。

送料方向由馬達安裝位置及 `feeder_drive_us` 位於校正停止值的哪一側共同決定，Firmware 不固定順時針對應的脈波側。正式流程不提供反轉清料、位置 Home 或自動補轉；更換馬達位置或修改驅動脈波後必須重新測試送料。

位置型 MG996R 分類位置：

| 中文分類 | Command code | 角度 |
|---|---|---:|
| 上等 | `high` | `25°` |
| 中等 | `medium` | `55°` |
| 下等 | `low` | `115°` |
| 加工 | `processing` | `145°` |

Firmware 可接受舊輸入 alias `high_medium` 與 `discard`，分別套用上等與中等的新角度；Django 與新決策層不得再產生 alias。位置型 MG996R 分類位置保持 `1000 ms`、Home 穩定 `500 ms`、Firmware 動作 timeout `5000 ms`。

HC-SR04 觸發距離為 `6.0 cm`，重新待命距離為 `8.0 cm`，讀取間隔為 `50 ms`。一次有效觸發即可立即停止送料；只有有效距離大於 `8.0 cm` 才可重新待命。`0 cm`／Echo timeout 代表感測器異常或線材問題，送料前必須拒絕命令，送料中必須立即停止。Echo 分壓、伺服供電與機械驗收見 [`hardware_notes/硬體接線與驗收摘要.md`](../hardware_notes/硬體接線與驗收摘要.md)。

## Runtime profile

| 設定 | 推薦值 | 允許範圍 |
|---|---:|---:|
| `first_station_settle_ms` | `300 ms` | `50～3000 ms` |
| `servo_settle_ms` | `200 ms` | `50～3000 ms` |
| `fruit_settle_ms` | `350 ms` | `50～3000 ms` |
| `final_gate_return_delay_ms` | `300 ms` | `0～3000 ms` |
| `idle_command_poll_interval_ms` | `250 ms` | `100～5000 ms` |

上述拍攝值必須為 `50 ms` 的倍數。送料校正值沿用同一份 runtime profile 與 revision：

| 設定 | 推薦值 | 允許範圍 | 間距 |
|---|---:|---:|---:|
| `feeder_stop_us` | `1500 us` | `1400～1600 us` | `5 us` |
| `feeder_drive_us` | `1300 us` | `1000～2000 us` | `10 us` |
| `feeder_max_run_ms` | `150 ms` | `50～500 ms` | `5 ms` |

`feeder_drive_us` 不得等於 `feeder_stop_us`，但不限制兩者差值或驅動脈波位於停止值的哪一側。`feeder_max_run_ms` 是唯一運轉上限；Dashboard 與 Firmware 不得另設隱藏上限或靜默截斷。Dashboard 必須在欄位旁固定顯示「警告 : 若設定過長將導致連續送料之情形發生」。

`POST /api/capture_timing/` 只能在自動運轉停止、Django idle、沒有 active fruit、sorter 動作與 motor command 時更新完整 profile。Django 原子保存至 `Django_Server/runtime_config/capture_timing.json` 並提高 revision；ESP32 只在 idle、沒有 sequence／pending report、三閘門 Home 且送料馬達停止時套用，再回報 `timing_config_applied`。

每顆 fruit 開始時 snapshot 前四項拍攝機構 timing；每次建立 `feed_one` 時 snapshot 三項送料設定，流程中不得覆寫。

`feeder_calibrated` 預設為 false，並與 profile 一起原子保存。操作員先在空料斗確認停止脈波沒有爬行，再以滿載漏斗執行「測試送料一次」；驅動脈波由 `1300 us` 起測，校正為滿載下能可靠起轉的最慢值，最大運轉時間則由 `150 ms` 起由低往高增加。測試成功觸發 HC-SR04 後，只開放同一 revision 的「送料校正已確認」checkbox；操作員目視確認恰好送出一顆後才可勾選。測試模式不得建立 Capture session。

修改 `feeder_stop_us`、`feeder_drive_us` 或 `feeder_max_run_ms` 都會取消確認。舊設定檔升級時保留停止與驅動脈波，移除 `feeder_run_ms` 與 `fruit_arrival_warning_ms`，將 `feeder_max_run_ms` 設為 `150 ms`、取消確認並提高 revision。設定檔遺失、損壞或驗證失敗時載入推薦值、取消確認並在 Dashboard 明確警示，不得靜默恢復正式送料。ESP32 尚未 ACK 最新 revision 時不得測試送料；本階段正式「開始執行」固定停用。

其他 interval 與 request deadline：

| 行為 | 值 |
|---|---:|
| 等待 `start_sequence` polling | `100 ms` |
| 等待 `release_gate` polling | `50 ms` |
| 自動 trigger／首站捷徑 request | `1000 ms` |
| Command GET | `1500 ms` |
| 一般 station report | `5000 ms` |
| 手機 idle／active／busy polling | `250 / 50 / 250 ms` |
| 手機 state request timeout | `1000 ms` |
| 相機 ready heartbeat freshness | `5000 ms` |
| Sorter pending／running deadline | 各 `30 s` |

伺服移動、果實停穩與 report pending 階段不輪詢 command。Wi-Fi、HTTPS、感測、伺服 phase 與 retry 必須使用 deadline；timeout 或 `Connection: close` 後關閉並安全重建 client。

## API

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/state/` | Dashboard 完整狀態 |
| `GET` | `/api/camera/state/` | 手機精簡 capture state 與相機 ready heartbeat |
| `POST` | `/api/capture_timing/` | 更新 runtime profile 與送料校正確認 |
| `POST` | `/api/auto_run/` | 本階段拒絕啟用正式自動運轉 |
| `POST` | `/api/feeder/test/` | 在安全 idle 狀態測試一次 HC-SR04 回授送料 |
| `POST` | `/api/capture_started/` | Best-effort 拍攝 telemetry |
| `POST` | `/api/upload_images/` | 上傳指定站點照片 |
| `GET` | `/api/esp32/command/?format=text` | ESP32 取得 motor command |
| `POST` | `/api/esp32/report/` | ESP32 回報 trigger、station、完成或失敗 |
| `POST` | `/api/discard/` | 跳過／刪除目前暫存果實 |

`/api/camera/state/` 禁止快取且只讀記憶體狀態與套用 session timeout，不得掃描 Dataset 或同步 metadata／counter。手機 polling 必須 single in-flight；頁面進入背景時停止，回到前景才重啟。只有相機 stream 存在、video track 為 live、已有可擷取影格與有效尺寸時，手機才在 polling query 回報 `camera_ready=1`；Django 只在記憶體保存最後 ready 時間。每次 upload 必須帶 `fruit_id`、`capture_token` 與 `station_index`。

ESP32 command polling 必須附帶本次開機唯一的 `boot_id`、固定能力 `capability=feeder_v1`、`feeder_state=idle|awaiting_fruit`、`feeder_sensor_state=clear|blocked|unavailable` 與 `last_feed_command_id`。`clear` 只代表最近一次有效距離大於 `8.0 cm`；`0 cm`／Echo timeout 必須回報 `unavailable`，其餘為 blocked。Django 未收到 `feeder_v1`、ESP32 離線、感測器不是 `clear`、最新 revision 尚未 ACK，或 boot／feeder state 尚未完成復原時，不得建立測試 `feed_one`。

## 自動運轉與單顆送料

### 開始條件

本階段只開放 calibration `feed_one`。Dashboard「開始執行」固定停用，`POST /api/auto_run/` 的啟用請求回傳 `feeder_hardware_validation_required`；不得建立 production `feed_one`。

以下開始條件、下一顆送料、優雅暫停與重新啟動條款是通過 HC-SR04 實機驗證後另案開放的正式運轉目標契約；本階段 API 不執行。

操作員按下 Dashboard「開始執行」時，Django 必須在同一個 `STATE_LOCK` critical section 內確認：

- ESP32 在線且回報 `feeder_v1`。
- ESP32 已 ACK 最新 runtime profile，且送料校正已確認。
- 相機最後一次 ready heartbeat 未超過 `5000 ms`。
- 沒有 active fruit、未分類 temp fruit、Capture、sorter 或 motor command。
- ESP32 已取得一次有效且大於 `8.0 cm` 的 HC-SR04 距離；感測區未清空或讀值為 `0 cm` 時，Firmware 必須拒絕 `feed_one`，Django 關閉自動運轉並通知操作者。
- 若有重新啟動或進料未確認狀態，操作員已依 Dashboard 提示檢查送料區域，並以本次「開始執行」明確確認復原。

條件成立後，Django 設定自動運轉並建立第一個 `feed_one`。自動運轉狀態只存在記憶體，不跨 Django 或 ESP32 重新啟動恢復。

### `feed_one` 契約

1. Django 建立 `feed_one`，snapshot `feeder_stop_us`、`feeder_drive_us` 與 `feeder_max_run_ms`，並帶 `feed_context=production|calibration`。
2. ESP32 再次確認 HC-SR04 已以有效距離大於 `8.0 cm` 重新待命；未清空回報 `feeder_sensor_not_clear`，`0 cm`／Echo timeout 回報 `feeder_sensor_unavailable`，兩者都不得啟動馬達。
3. ESP32 寫入 `feeder_drive_us` 並記錄開始時間。每次控制迴圈先判斷 `feeder_max_run_ms`，尚未到期才讀取 HC-SR04；任一停止分支都必須先在本機寫入 `feeder_stop_us`，不得等待網路。
4. 一次有效的 `HC-SR04 <= 6.0 cm` 先發生時，ESP32 立即停止、鎖存 trigger、回報 `feed_cycle_completed`，並附 `feeder_elapsed_ms`、`feeder_max_run_ms` 與 `feeder_stop_reason=hcsr04`。正式送料以同一筆 trigger 啟動既有首站流程。
5. `feeder_max_run_ms` 先到時，ESP32 立即停止並回報 `feeder_max_run_timeout`；`0 cm`／Echo timeout 在運轉中出現時立即停止並回報 `feeder_sensor_unavailable`。兩者都附實際運轉時間與設定上限。
6. Report timeout 只重送同一個 terminal report 與鎖存 trigger，不得再次執行送料動作。

「測試送料一次」重用完整停止邏輯，command context 為 calibration。HC-SR04 成功觸發後 ESP32 回到 `idle`，Django 保持自動運轉關閉，只開放人工確認 checkbox，不建立 Capture session。測試逾時只顯示停止原因與實際時間，不建立正式流程的 blocking error；感測器異常仍必須顯示可操作的錯誤。

### 下一顆果實

人工分類仍先提交 Dataset，再建立 `classify_fruit`。只有相符的 `classification_sorter_completed` 已清除 command slot，且自動運轉、相機 readiness 與全部開始條件仍成立時，Django 才建立下一個 `feed_one`。Sorter failed／timeout、Capture timeout、相機 stale 或任何 blocking error 都不得建立下一次送料；相機恢復時可自動繼續，其他錯誤由操作員優雅暫停並排除。

### 安全逾時、感測異常與延遲抵達

正式送料發生 `feeder_max_run_timeout` 時，Django 清除 command slot、關閉自動運轉並顯示「進料未確認」；ESP32 維持 HC-SR04 待命，但不得自動補轉。逾時不取消 `feeder_calibrated`，因為原因也可能是料斗已空或果實卡住；操作者修改任一送料參數時才依校正規則取消。

逾時與有效觸發落在同一次控制迴圈時，以逾時優先。若果實之後才觸發 HC-SR04，仍接手既有三站拍攝與分類，但自動送料保持關閉，完成後由操作員檢查送料區域並重新按「開始執行」。

送料前的 `0 cm`／Echo timeout 直接拒絕命令；送料中的相同錯誤立即停止馬達並關閉自動運轉。Dashboard 必須指示操作者檢查 HC-SR04 線材、供電、Echo 分壓與感測方向，不得將無回音解讀為感測區清空。

### 優雅暫停

暫停是 Django 控制旗標，不是 motor command，不占用、取消或覆寫單一 command slot，API 必須立即回應。與 `feed_one` 建立使用同一把 `STATE_LOCK`：

- 暫停旗標先寫入時，不建立下一次送料。
- `feed_one` 先建立時，視為已承諾；目前果實完成拍攝與實體分類後停止。
- 安全逾時後仍保持感測器待命；若實際沒有果實，不建立 Capture session，也不自動補轉。

暫停可發生於送料後等待、三站拍攝、等待人工分類、實體分類或分類後尚未建立下一次送料。Dashboard 主操作區只使用同一按鈕：停止時顯示「開始執行」、運轉時顯示「暫停」、正在完成目前果實時顯示 disabled「正在完成目前果實」。本軟體暫停不是實體緊急斷電。

重拍與刪除成功時都關閉自動運轉。重拍由操作員將同一顆果實放回 Gate 1，再走既有 `start_sequence`，不驅動送料機構；完成後由操作員重新按「開始執行」。正式 Dashboard 與 API 完整移除 manual capture。

### 重新啟動與重複動作

ESP32 每次開機產生新的 `boot_id`。Django 若在未完成 `feed_one` 期間看到 boot 改變，必須關閉自動運轉、不向新 boot 重送該命令，保留 HC-SR04 待命並顯示操作員檢查提示；原則是寧可少送一次，也不能自動重複送料。

Django 重新啟動後，依 ESP32 polling 的 `feeder_state` 與 `last_feed_command_id` 重建「進料未確認」。看到 `awaiting_fruit` 時保持自動運轉關閉、允許 HC-SR04 接手，但不建立新 `feed_one`。重複或舊的 feeder terminal report 回 `200 ignored`，不得推進流程或造成 ESP32 永久 retry。

### Dashboard 錯誤提示

Blocking error 必須以可存取的 `role="alert"` 區塊顯示發生位置、具體 reason、目前 fruit／command 與操作指引。Reason 至少區分 `feeder_max_run_timeout`、`feeder_sensor_not_clear`、`feeder_sensor_unavailable`、`camera_upload_timeout`、`classifier_timeout`、`feeder_capability_missing`、`feeder_calibration_required` 與 `esp32_restarted_during_feed`。送料結果另顯示 `feeder_elapsed_ms`、`feeder_max_run_ms` 與停止原因。標準操作文字為「暫停 → 排除／重新拍攝／刪除 → 開始執行」；存在 active fruit 時，「開始執行」保持停用。

## 觸發與三站流程

### 自動首站捷徑

1. 正式送料期間 HC-SR04 進入觸發範圍時，ESP32 先停止馬達並鎖存 `trigger_id`；非送料觸發沿用既有 trigger 建立方式。
2. 只有送料 terminal report 已安全處理、沒有 sequence、三閘門追蹤為 Home 且已等待首站停穩時，才可使用同一筆鎖存 trigger 回報 `hcsr04_station_1_ready`。Calibration context 不得進入此流程。
3. Report 必須帶同一個 `trigger_id`、`station_index=1`、`gates_home=true` 與 `station_settled=true`。
4. Django 驗證鎖定條件後建立或找回 active fruit，開放第 1 站拍攝。
5. 可回退的協定拒絕才改走標準流程；HTTP timeout 只能重送原 `trigger_id`。收到 `ignored` 時停止本次 trigger 並等待重新待命。

Gate 1 已實際攔住果實是部署前提，並非額外位置感測。捷徑只縮短第 1 站前控制往返，不放寬照片保存交握。

### 標準與重拍流程

重拍只取代 HC-SR04 開始請求，且不驅動送料機構。Django 建立 `start_sequence`，ESP32 取得後回報 `station_1_ready`。Legacy `hcsr04_trigger` response 若已含 `start_sequence`，ESP32 可直接執行；POST timeout 時先 fast poll 既有 command，不立即假定失敗。

### 三站交握

1. Django 開放目前站點 capture request。
2. 手機拍攝並上傳該站單張照片。
3. Django 驗證 fruit、token、站點與狀態，原子保存成功後建立 `release_gate_N`。
4. ESP32 放行目前 Gate，等待伺服與果實停穩後回報下一站 ready。
5. 第 3 站保存後放行 Gate 3，等待果實離開，三閘門歸位並回報 `capture_sequence_finished`。
6. Dashboard 等待使用者分類或刪除。

任何固定延遲都不能取代 Django 確認照片保存成功。

## Command ID、互斥與冪等

```text
feed_one -> feed_cycle_completed | feeder_max_run_timeout | feeder_sensor_not_clear | feeder_sensor_unavailable 使用 feed_one command_id
start_sequence -> station_1_ready 使用 start_sequence command_id
release_gate_1 -> station_2_ready 使用 release_gate_1 command_id
release_gate_2 -> station_3_ready 使用 release_gate_2 command_id
release_gate_3 -> capture_sequence_finished 使用 release_gate_3 command_id
classify_fruit -> sorter terminal report 使用 classify_fruit command_id
```

Django 建立 command 前，取記憶體與 `Django_Server/runtime_config/motor_command_sequence.json` 的較大值加一並原子保存，成功後才公開。檔案不存在時從 `1` 開始；損壞或寫入失敗時回傳 `motor_command_id_persist_failed` 並拒絕建立。Dataset reset 不重設序列。每個 command 只能有一個 terminal report；相同 ID 的 retry 必須冪等。

以下狀況禁止新的 `feed_one`、自動 capture 或重拍：

- 已有 active fruit 或未分類 temp fruit。
- Capture 不在 idle。
- Sorter 為 pending／running。
- 單一 motor command slot 尚有命令。
- 相機 readiness stale、送料設定未 ACK／未確認，或 ESP32 未回報 `feeder_v1`。

首站捷徑以 `trigger_id` 做 server-side 冪等；legacy trigger 依 active fruit 與既有 command 防重。Firmware 以 command ID 去重實體動作，report retry 不得再次轉動伺服。Terminal sorter 收到 late duplicate report 時回 `200 ignored`，不得改寫既有結果。

## 分類與 Dataset

三站完成後，Django 先搬移照片、寫入 `metadata.csv`、推進 counter 並清除 active Dataset 狀態，再嘗試建立 `classify_fruit`。資料操作失敗時不得建立分類 command；資料已提交但 command 建立失敗時，不得反向搬移或回滾 metadata。

Sorter 使用 `idle／pending／running／completed／failed／timeout`，不覆蓋 capture status。Pending／running 期間拒絕下一次送料、capture、recapture、reset 與第二筆分類。MG996R 無位置回授，completed 只代表控制時序完成並成功回報。

```text
dataset/
  temp/fruit_XXX/
    img_01.jpg
    img_02.jpg
    img_03.jpg
  上等/
  中等/
  下等/
  加工/
  metadata.csv
  counter.json
```

`metadata.csv` schema：

```csv
fruit_id,label,capture_time,path,capture_count,station_01_ok,station_02_ok,station_03_ok,note
```

跳過／刪除先關閉自動運轉，再嘗試實體刪除；Windows 檔案占用時移至 `_delete_pending`，仍失敗則持久標記待清理並跳過該 fruit ID。成功結果必須清除 capture token、等待計時器、fast-path 與 motor command，安全回到 idle，且不得自動送料。

## 驗收

### 自動化

- Django：`C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app`
- Firmware：以 `esp32:esp32:esp32` 與 `ESP32Servo 3.2.1` 編譯正式 sketch。
- Repository：`git diff --check`

### 實機

1. 空軌連續 `100` 次，確認有效距離大於 `8.0 cm` 才重新待命；斷開 HC-SR04 或造成 Echo timeout 時必須回報異常，不得解讀為清空。
2. 健康網路連續 `20` 次自動採集；實體偵測至首張保存的 median ≤ `1.0 s`、p95 ≤ `1.5 s`。
3. 高速錄影確認第 2、3 站 ready 前果實已停止，且三張照片清晰。
4. 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站、重複 trigger 與 duplicate command。
5. 驗證未分類鎖定、刪除復原、四個分類出口、Home 歸位與 report retry 不重複作動。
6. 以滿載漏斗由 `1300 us` 起校正能可靠起轉的最慢驅動脈波，並由 `150 ms` 起逐步增加最大運轉時間；測試送料成功後，只有人工確認恰好一顆才可完成校正。
7. 以混合尺寸、形狀與蒂頭方向的果實連續完成 `20` 顆送料與完整分類；每個 `feed_one` 都由一次有效 HC-SR04 讀值正常停止且恰好一顆，無漏送／雙送、無須人工重對送料桿。
8. 模擬最大運轉逾時、送料中 Echo timeout，以及逾時後果實才抵達；確認馬達先停止、不自動補轉，延遲果實仍完成三站流程且自動送料保持關閉。
9. 同一個 `20` 顆測試確認送料與分類器兩顆 MG996R、三顆拍攝平台 SG90 及分類器擋臂 SG90 均無抖動／異音，ESP32 無 reset，配電端子與線材無異常溫升；兩組 `4 × AA` 電池盒的空載與動作中電壓都須保持在所接伺服的額定範圍。任一項失敗時，校正或修正後重新累計連續 `20` 顆。
10. 模擬 ESP32 與 Django 分別在 `feed_one` 回報前後重新啟動，確認不會自動重複送料，且 Dashboard 顯示可操作的復原提示。
