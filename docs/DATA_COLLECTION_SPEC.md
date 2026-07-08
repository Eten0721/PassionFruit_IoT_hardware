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
- 站點就緒：ESP32 回報百香果已在某站可拍攝。

## 硬體與 timing 參數

正式 firmware：`firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`

- `HOME_ANGLE = 0`：閘門攔截／歸位。
- `RELEASE_ANGLE = 90`：閘門放行。
- `triggerDistanceCM = 6.0`
- `rearmDistanceCM = 8.0`
- `sensorReadIntervalMS = 50`
- `commandPollIntervalMS = 5000`
- `startSequenceCommandPollIntervalMS = 100`
- `activeCommandPollIntervalMS = 120`
- `servo_settle_ms = 150`
- `fruit_settle_ms = 300`
- `firstStationSettleMS = 100`
- `finalGateReturnDelayMS = 300`
- `autoTriggerReportTimeoutMS = 1000`
- `commandHttpTimeoutMS = 1500`
- `reportHttpTimeoutMS = 5000`

`servo_settle_ms` 與 `fruit_settle_ms` 只用於硬體動作與果實停穩，不用來判斷手機是否拍攝完成。

## 觸發入口

### 自動模式

1. HC-SR04 偵測距離進入觸發範圍。
2. ESP32 queue `hcsr04_trigger`。
3. ESP32 POST `/api/esp32/report/` 回報 `hcsr04_trigger`。
4. Django 若允許新資料，建立 active fruit 與 `start_sequence` motor command。
5. 若 Django 回應內已包含 `start_sequence`，ESP32 可直接執行；否則進入 fast command polling。

### 手動模式

1. 使用者在 dashboard 按下手動拍攝。
2. Django 建立 active fruit 與 `start_sequence` motor command。
3. ESP32 透過 command polling 取得 `start_sequence`。
4. 後續流程與自動模式相同。

手動拍攝只取代 HC-SR04 開始訊號，不可繞過 ESP32 三閘門流程。

## 三站握手流程

1. Django 建立 `start_sequence`，並分配 `command_id`。
2. ESP32 取得 `start_sequence` 後回報 `station_1_ready`，必須帶同一個 `command_id`。
3. Django 設定第 1 站拍攝請求。
4. 手機輪詢 `/api/state/`，拍攝並上傳 `img_01.jpg`。
5. Django 保存成功後建立 `release_gate_1`。
6. ESP32 取得 `release_gate_1`，Gate 1 轉到 `90` 度放行。
7. ESP32 等待 `servo_settle_ms` 與 `fruit_settle_ms`，回報 `station_2_ready`，必須帶 `release_gate_1` 的 `command_id`。
8. 第 2 站重複：手機上傳 `img_02.jpg`，Django 建立 `release_gate_2`，ESP32 回報 `station_3_ready`。
9. 第 3 站重複：手機上傳 `img_03.jpg`，Django 建立 `release_gate_3`。
10. ESP32 放行 Gate 3，等待百香果滾出，再額外等待 `finalGateReturnDelayMS`，三顆馬達歸位。
11. ESP32 回報 `capture_sequence_finished`，必須帶 `release_gate_3` 的 `command_id`。
12. Dashboard 顯示三張照片，等待使用者分類或刪除。

command id 規則：

```text
start_sequence -> station_1_ready 使用 start_sequence command_id
release_gate_1 -> station_2_ready 使用 release_gate_1 command_id
release_gate_2 -> station_3_ready 使用 release_gate_2 command_id
release_gate_3 -> capture_sequence_finished 使用 release_gate_3 command_id
```

## 自動觸發鎖定與冪等

Django 是是否允許開始新 fruit 的唯一狀態來源。

以下情況 `auto_trigger_enabled=0`：

- 已有 active fruit。
- `dataset/temp/fruit_XXX/` 有未分類暫存資料。
- 流程處於 `waiting_esp32_start`、`waiting_camera`、`uploaded`、`incomplete` 或 `error`。

當 `auto_trigger_enabled=0` 時，`hcsr04_trigger` 回 `200 ignored`。ESP32 收到 ignored 後清除 pending，不應建立新 fruit，也不應覆蓋既有 motor command。

若 `hcsr04_trigger` 重送時 Django 已在 `waiting_esp32_start` 且既有 `start_sequence`，Django 回 `duplicate_trigger_waiting_start_sequence` 並保留既有 `motor_command`，ESP32 可據此進入 fast polling 或直接執行 response 內的 `start_sequence`。

## Timeout 與延遲處理

- `hcsr04_trigger` POST timeout 不代表 Django 一定沒收到。ESP32 會設定等待 `start_sequence` 狀態，優先 fast command polling。
- `hcsr04_trigger` pending retry 會保留，但不應每 `100 ms` 重送。
- `waitingStartSequenceCommand=true` 時，command GET timeout 不應進入 `10000 ms` idle backoff。
- ESP32 預設只印 response 摘要；若需要完整 Django JSON，才將 `verboseHttpResponseLog` 改為 `true`。

## 手機相機頁

- `/camera/` 以 single in-flight polling 輪詢 `/api/state/`。
- 目前 idle / active 輪詢約 `200 ms`。
- 每次只在 Django 指定站點時拍攝並上傳 1 張照片。
- 上傳必須包含 `fruit_id`、`capture_token`、`station_index`。
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
上中等
下等
加工
廢棄
```

`metadata.csv` 欄位：

```csv
fruit_id,label,capture_time,path,capture_count,station_01_ok,station_02_ok,station_03_ok,note
```

目前 `capture_count` 預期為 `3`。

## 驗證指令

Django 測試：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

啟動 HTTPS 開發伺服器時，依目前專案環境使用既有 `runsslserver` 設定。
