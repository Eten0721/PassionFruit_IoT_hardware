# 百香果辨識系統專案脈絡

## 1. 專案目標

本專案目標是建立百香果照片蒐集、資料集管理與後續 AI 分級辨識流程。現階段重點是先穩定「資料採集」，讓每顆百香果能在固定軌道上停留於三個拍攝站點，各拍攝 1 張照片，形成可分類與可訓練的資料。

舊版「滾動中連拍 6 張」已停止作為主要方向。新版架構以 Django 中央狀態機協調手機相機與 ESP32 三段 SG90 閘門，完成三站停止拍攝。

## 2. 目前系統架構

```text
HC-SR04 / dashboard
  -> 觸發一顆百香果拍攝工作階段

Django Server
  -> 中央狀態機
  -> 手機拍攝請求
  -> ESP32 馬達命令
  -> dataset/temp 與分類資料夾管理
  -> metadata.csv

手機相機頁 /camera/
  -> 輪詢 Django
  -> 單站拍攝 1 張
  -> 上傳 img_01.jpg / img_02.jpg / img_03.jpg

ESP32 firmware
  -> HTTPS client
  -> HC-SR04 自動觸發
  -> 輪詢 Django motor command
  -> 控制 3 顆 SG90 閘門
  -> 人工分類後控制 GPIO25 的 MG996R 分類器
  -> 回報 station ready / finished
```

正式 firmware 位於：

```text
firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino
```

入口 sketch 只負責初始化與主迴圈；HC-SR04 讀值、閘門 phase、Django HTTPS client 與採集控制分拆為 `Config.h`、`ProtocolTypes.h`、`DistanceSensor.*`、`GateController.*`、`DjangoApiClient.*` 與 `CaptureController.*`，避免把流程狀態散落在多個 `.ino` tab。

Django 的單程序 runtime state／operation token 位於 `runtime_state.py`，timing 驗證與原子保存位於 `capture_timing.py`，ESP32 response shaping 位於 `api_payloads.py`；`capture_session.py`、`dataset_store.py` 與 `webrtc_signaling.py` 分別負責狀態轉移、dataset primitive／熱路徑 cache 與 signaling。`views.py` 仍保留 HTTP 整合、資料集生命週期與部分狀態機 helper，後續應延續相同責任邊界逐步縮小，不應誤稱為完全薄化。

## 3. 現行拍攝流程

手動拍攝與自動拍攝在 Django 建立拍攝請求後共用同一套三站狀態機；自動模式可在安全前提成立時壓縮第 1 站前的控制往返：

- 自動模式：HC-SR04 偵測百香果進入軌道，ESP32 優先回報受守門的 `hcsr04_station_1_ready`，不符條件時回退 `hcsr04_trigger`。
- 手動模式：使用者在 dashboard 按下「手動拍攝」，Django 建立同一套開始請求。

後續流程一致：

1. 自動觸發且符合首站捷徑的條件時，firmware 處於 idle、沒有進行中的 sequence，且三個 Gate 都追蹤為 `HOME_ANGLE`；在等待 `firstStationSettleMS` 後，以冪等 `trigger_id` 與 `station_index=1` 回報 `hcsr04_station_1_ready`。此流程依賴部署時已確認 Gate 1 的機構會攔住果實，並非由額外硬體回授證明。
2. Django 建立或找回對應拍攝工作階段，直接開放第 1 站拍攝請求；這條路徑省去 `hcsr04_trigger -> start_sequence -> station_1_ready` 的額外往返。
3. 若捷徑被停用、本地前置條件不符，或收到可回退的協定拒絕，ESP32 回退為 Django 建立 `start_sequence`、ESP32 回報 `station_1_ready` 的既有流程。HTTP timeout 屬於不確定結果，只能重送同一個 `trigger_id`；收到 `ignored` 時則停止本次 trigger 並等待感測器重新待命。
4. 手機輪詢精簡的 `/api/camera/state/`，拍攝並上傳 `img_01.jpg`。
5. Django 原子保存成功後才建立 `release_gate_1`。
6. ESP32 放行第 1 閘門，百香果到達第 2 站並停穩後回報 `station_2_ready`。
7. 第 2、3 站重複「站點就緒 -> 手機單張保存成功 -> 放行下一閘門」的握手。
8. Django 收到並保存 `img_03.jpg` 後建立 `release_gate_3`。
9. ESP32 放行第 3 閘門，等待百香果滾出後三顆馬達歸位，回報 `capture_sequence_finished`。
10. 使用者在 dashboard 確認照片並分類。

資料分類成功後，Django 才在同一個 motor command slot 建立 `classify_fruit`。MG996R 依固定 ASCII code 前往分類角度、保持後回到 `85°`，再回報完成或具體失敗原因。此後段流程不參與第 3 站拍攝、Gate 3 放行、三閘門歸位或 `capture_sequence_finished`。

手機是否完成拍攝一律以 Django 收到並保存照片為準，不使用固定延遲猜測手機狀態。

## 4. 硬體基準

目前正式三閘門流程使用 3 顆 SG90：

- Gate 1：GPIO `18`
- Gate 2：GPIO `19`
- Gate 3：GPIO `21`
- HC-SR04 Trig：GPIO `26`
- HC-SR04 Echo：GPIO `27`
- MG996R 分類器：GPIO `25`，Home `85°`；必須使用獨立外部電源並與 ESP32 共地。

HC-SR04 的 Echo 是 `5 V` 邏輯輸出，接到 ESP32 GPIO 前必須經過分壓或邏輯電平轉換；正常目標為 ≤ `3.3 V`，絕不可超過 `3.6 V`。接線與量測方式見 `hardware_notes/HC-SR04_ESP32_3V3_安全檢查.md`。

角度基準：

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。

目前 timing 基準：

- HC-SR04 觸發距離：`6.0 cm`
- HC-SR04 重新待命距離：`8.0 cm`
- `servo_settle_ms = 200`
- `fruit_settle_ms = 350`
- 第 1 站停穩校正值：`300 ms`
- 第 3 站放行後歸位前額外等待：`300 ms`
- Echo 等待上限：`12000 us`；完整 `sensor_read_us` 的 firmware warning 門檻約為 `12100 us`。

四項設定由 Dashboard 管理，目前實測推薦值依序為 `300 / 200 / 350 / 300 ms`，分別對應第 1 站停穩、伺服穩定、到站停穩與最終歸位延遲。Django 會將最後套用的完整設定與 revision 原子覆寫到固定的 `runtime_config/capture_timing.json`，不放入 dataset、也不保留歷史版本；ESP32 只在 idle 時套用並回報 `timing_config_applied`。每顆 fruit 開始後使用自己的 timing snapshot，避免流程中混用設定。舊版 `dataset/capture_timing.json` 會在首次升級時遷移後移除。

## 5. Django 頁面與 API 角色

主要頁面：

- `/camera/`：手機相機頁，負責即時影像、輪詢拍攝請求、單張拍攝與上傳。
- `/dashboard/`：電腦控制頁，負責手動觸發、流程狀態、可調整的拍攝停穩設定、三張直式 `3:4` 照片預覽／彈窗、分類與刪除。

主要 API 角色：

- `GET /api/state/`：dashboard 讀取完整拍攝、資料集、控制與 transition trace 狀態。
- `GET /api/camera/state/`：手機讀取精簡且禁止快取的 state，主要包含 `revision`、fruit、token、站點與 `capture_requested`，並保留 `status`、`active_fruit_id`、`pending_capture` 與 nested `capture` 相容欄位。此高頻路徑只讀記憶體狀態與套用 timeout，不執行 dataset 掃描或 metadata／counter I/O。
- `POST /api/capture_timing/`：在 idle 時以完整四項 `*_ms` 設定更新下一顆 fruit 的硬體停穩參數。
- `POST /api/capture_started/`：手機 best-effort 回報開始拍攝 timing；回報失敗不可阻塞相片擷取或上傳。
- `POST /api/upload_images/`：手機上傳單站照片。
- `GET /api/esp32/command/?format=text`：ESP32 輪詢 Django motor command。
- `POST /api/esp32/report/`：ESP32 回報 `timing_config_applied`、`hcsr04_station_1_ready`、`hcsr04_trigger`、`station_1_ready`、`station_2_ready`、`station_3_ready`、`capture_sequence_finished`。
- `GET /api/esp32/command/` 亦可在人工資料分類成功後下發不含角度與 GPIO 的 `classify_fruit`；同一 report endpoint 接收 `classification_sorter_completed`／`classification_sorter_failed`。

ESP32 是 HTTPS client，Django 不主動呼叫 ESP32。

## 6. 自動觸發延遲優化

首張照片以受守門的首站捷徑為優先，並保留既有 `hcsr04_trigger -> start_sequence` 優化作為安全回退：

- `FirmwareConfig::kEnableAutoStation1FastPath` 預設開啟時，只有 firmware 為 idle、沒有進行中的 sequence、Gate 角度追蹤皆為 home 且首站停穩後，才可送出 `hcsr04_station_1_ready`。Gate 1 已攔住果實是部署與機構驗證前提，不是此版本的硬體回授訊號。
- `trigger_id` 是首站捷徑的 server-side 冪等鍵，用於 timeout 後重送與回覆去重；legacy `hcsr04_trigger` 的重複防護仍由 Django active fruit 與 motor command 狀態負責。
- ESP32 預設不印完整 Django JSON response，只印 HTTP code、request 耗時、body length、ignored 與是否包含 `start_sequence`；實際處理命令時另印 `Command #...`。
- 若 `hcsr04_trigger` response 內含 `motor_command.command=start_sequence`，ESP32 直接執行，不多等一次 command polling。
- 若 `hcsr04_trigger` POST timeout，ESP32 會先進入 fast command polling 嘗試取得 Django 已建立的 `start_sequence`，不立即回到 idle polling。
- ESP32 等待 `release_gate` 時以 `50 ms` 輪詢；伺服移動、果實停穩與 report pending 階段停止 command polling，避免無效 HTTPS GET。
- ESP32 report response 只保留 firmware 協定欄位；dashboard 專用圖片、labels、trace 與完整 timing 不再透過硬體回報端點傳送。
- 重複 `hcsr04_trigger` 必須冪等，不建立新的 fruit，也不覆蓋既有 motor command。
- HC-SR04 單次 Echo 等待上限為 `12 ms`；Wi-Fi 重連與伺服 phase 以 deadline 驅動，不得在主迴圈使用多秒同步等待。
- HTTPS 的 connect 與 read 都使用該 request 的 deadline，正常連線採同 origin HTTP/1.1 keep-alive；Wi-Fi 斷線、timeout 或伺服器要求關閉連線時，必須關閉 client 後安全重建。

## 7. 首張延遲驗收

transition trace 的起點是 Django 收到 `hcsr04_station_1_ready`，可量測伺服器端鏈路至 `img_01.jpg` 原子保存。HC-SR04 實體偵測至保存的總延遲仍須以同次 firmware Serial timing 與高速錄影／外部同步量測驗收：健康網路下連續 `20` 次自動採集的中位數目標為不超過 `1.0 s`，`p95` 目標為不超過 `1.5 s`。另以空軌連續 `100` 次確認 Echo 等待上限為 `12 ms`，完整 `sensor_read_us` 未跨過約 `12.1 ms` warning 門檻，並以高速錄影確認第 2、3 站回報 ready 前已停止。

## 8. 資料集結構

```text
dataset/
  temp/
    fruit_001/
      img_01.jpg
      img_02.jpg
      img_03.jpg

  上中等/
    fruit_001/
      img_01.jpg
      img_02.jpg
      img_03.jpg

  下等/
  加工/
  廢棄/

  metadata.csv
  counter.json
```

目前分類級距：

```text
上中等
下等
加工
廢棄
```

未來若手機鏡頭與平台保持平行且拍攝距離固定，可將 ROI 面積納入「大小」特徵，再評估把 `上中等` 拆成：

```text
上等
中等
下等
加工
廢棄
```

## 9. AI 整合方向

AI 推論與後段分類器目前暫緩，等待三站資料採集流程穩定後再整合。預期方向仍包含：

- 百香果 ROI 偵測或裁切。
- 表面瑕疵與外觀特徵判斷。
- 大小特徵，前提是拍攝平面與距離固定。
- 後段分類器，例如 XGBoost / Random Forest。
