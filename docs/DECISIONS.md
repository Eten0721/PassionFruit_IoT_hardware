# 決策紀錄

## 2026-07-06：資料採集流程改為三段 SG90 閘門停止拍攝

### 決策

資料採集流程由「滾動中連拍 6 張」改為「ESP32 三段 SG90 閘門停止拍攝 3 張」。

每顆百香果會依序停在 3 個拍攝站點，每站由手機拍攝並上傳 1 張照片。

### 原因

- 滾動中連拍容易產生模糊照片。
- 三段閘門能讓百香果在固定位置短暫停留，提高資料穩定性。
- 三張站點照片比六連拍更容易對應硬體位置與 metadata。

### 影響

- Django 拍照流程需由六連拍改為三張站點式拍攝。
- 手機頁每次只拍攝並上傳 1 張。
- ESP32 必須控制三段 SG90 閘門並回報站點狀態。

## 2026-07-06：手動拍攝只取代開始訊號

### 決策

Dashboard 手動拍攝不代表純軟體拍攝。使用者按下手動拍攝時，Django 只建立開始請求；後續仍由 ESP32 控制三段閘門。

### 原因

實體軌道上已有 SG90 閘門，若沒有 ESP32 控制放行，百香果會卡在閘門前。

### 影響

手動模式與自動模式必須共用同一套 Django 狀態機、手機拍攝流程與 ESP32 馬達流程。

## 2026-07-06：Django 作為中央狀態機

### 決策

Django 是流程狀態中心；手機與 ESP32 透過輪詢或回報同步狀態。

### 原因

- ESP32 在本專案中是 HTTPS client，比 Django 主動呼叫 ESP32 更穩定。
- 手機是否拍攝完成應由照片上傳成功確認，不應由 ESP32 用固定延遲猜測。

### 影響

- 手機輪詢 Django 取得 `capture_requested`。
- ESP32 輪詢 Django 取得 `motor_command`。
- ESP32 回報 `station_1_ready`、`station_2_ready`、`station_3_ready`、`capture_sequence_finished`。

## 2026-07-07：未分類資料期間鎖住自動觸發

### 決策

未分類 temp fruit 存在時，Django 回傳 `auto_trigger_enabled=0`，HC-SR04 自動觸發不得開始下一顆百香果流程。

### 原因

若上一顆照片尚未分類，硬體端再次觸發會干擾既有 active fruit 與 motor command。

### 影響

- `hcsr04_trigger` 在不允許時回 `200 ignored`。
- 重複 trigger 不建立 `fruit_002`，也不覆蓋既有 command。
- 使用者需先分類、刪除或 reset 目前 temp fruit，才可開始下一顆。

## 2026-07-07：硬體角度改為 0 / 90

### 決策

目前正式硬體角度基準：

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。

### 原因

實測後，`90` 度放行角度比先前角度更適合目前閘門機構。第 3 站放行後也需要額外等待，避免百香果尚未滾出時閘門太快歸位。

### 影響

- Django command payload 與 ESP32 fallback 常數需同步。
- 第 3 站 `release_gate_3` 後，ESP32 先等待 `finalGateReturnDelayMS = 300`，再讓三顆馬達歸位。

## 2026-07-08：站點停穩時間調整

### 決策

目前 `servo_settle_ms = 150`，`fruit_settle_ms = 300`。第 1 站另有 `firstStationSettleMS = 100` 測試值。

### 原因

第 2、3 站曾出現百香果尚未停穩就拍攝的模糊問題，因此將果實停穩時間從高速測試值提高到 `300 ms`。

### 影響

- 第 2、3 站照片清晰度優先於極限速度。
- 若仍模糊，可再提高到 `400` 或 `500 ms`；若速度太慢，可回測 `250 ms`。

## 2026-07-08：ESP32 預設不印完整 JSON response

### 決策

ESP32 Serial Monitor 預設只印 HTTP code、body length、ignored、start_sequence 與 command id 摘要。完整 response 需將 `verboseHttpResponseLog` 改為 `true` 才會輸出。

### 原因

完整 Django JSON response 太長，會拖慢 Serial Monitor 輸出，也會干擾現場判斷 timing。

### 影響

- 預設 log 更短，方便看關鍵 timing。
- 深度 debug 時仍可開啟完整 response。

## 2026-07-08：hcsr04_trigger response 可直接執行 start_sequence

### 決策

若 `hcsr04_trigger` POST response 內包含 `motor_command.command=start_sequence` 與 `command_id`，ESP32 直接組成 command 並執行，不再等下一次 `GET /api/esp32/command/`。

### 原因

Django 在接受 trigger 時已經建立 `start_sequence`，ESP32 沒必要再多等一次 command polling。

### 影響

- `hcsr04_trigger_post_success -> start_sequence_received` 可降到數十毫秒等級。
- 若解析失敗，仍保留 fast command polling fallback。
- `command_id` 規則不變：`start_sequence -> station_1_ready` 必須帶同一個 command id。

## 2026-07-08：hcsr04_trigger timeout 後優先 fast command polling

### 決策

`hcsr04_trigger` POST timeout 後，不立即連續重送 trigger。ESP32 會先進入 `waitingStartSequenceCommand=true`，以 fast command polling 嘗試取得 Django 可能已建立的 `start_sequence`。

### 原因

ESP32 timeout 不代表 Django 沒收到 POST。若立即 retry，可能多花一次 HTTPS timeout，反而拉長第一張照片延遲。

### 影響

- `hcsr04_trigger` pending retry 仍保留，但不阻塞優先找回 `start_sequence`。
- `waitingStartSequenceCommand=true` 時 command GET timeout 不進入長時間 idle backoff。
