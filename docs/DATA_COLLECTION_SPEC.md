# 三站資料採集與分類規格

## 目標與範圍

正式流程由 Django 中央狀態機、手機單站拍攝與 ESP32 三段 SG90 閘門組成。每顆百香果在三個固定站點各保存一張照片。本文是三站流程、API、command ID、GPIO、角度、timing 與分類契約的唯一來源。

上游送料機構與分類後出料閘門尚未納入現行規格，分別由 GitHub Issues [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 與 [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 規劃。

## 名詞

- Capture session：一顆果實從觸發至三站完成的工作階段。
- Station ready：ESP32 宣告果實已在指定站點停穩。
- Capture request：Django 通知手機拍攝指定站點。
- Upload complete：Django 驗證並原子保存指定照片。
- Motor command：`start_sequence`、`release_gate_N` 或 `classify_fruit`。
- Command slot：Capture 與 sorter 共用的單一命令槽。

## 硬體設定

正式 Firmware：`firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`

| 裝置 | GPIO | 現行設定 |
|---|---:|---|
| Gate 1 SG90 | `18` | Home `0°`，Release `90°` |
| Gate 2 SG90 | `19` | Home `0°`，Release `90°` |
| Gate 3 SG90 | `21` | Home `0°`，Release `90°` |
| HC-SR04 Trigger | `26` | `10 us` pulse |
| HC-SR04 Echo | `27` | `12000 us` timeout，輸入必須安全降壓 |
| MG996R sorter | `25` | Home `85°` |

MG996R 分類位置：

| 中文分類 | Command code | 角度 |
|---|---|---:|
| 上等 | `high` | `25°` |
| 中等 | `medium` | `55°` |
| 下等 | `low` | `115°` |
| 加工 | `processing` | `145°` |

Firmware 可接受舊輸入 alias `high_medium` 與 `discard`，分別套用上等與中等的新角度；Django 與新決策層不得再產生 alias。MG996R 分類位置保持 `1000 ms`、Home 穩定 `500 ms`、Firmware 動作 timeout `5000 ms`。

HC-SR04 觸發距離為 `6.0 cm`，重新待命距離為 `8.0 cm`，讀取間隔為 `50 ms`。Echo 分壓、伺服供電與機械驗收見 [`hardware_notes/硬體接線與驗收摘要.md`](../hardware_notes/硬體接線與驗收摘要.md)。

## Timing profile

| 設定 | 推薦值 | 允許範圍 |
|---|---:|---:|
| `first_station_settle_ms` | `300 ms` | `50～3000 ms` |
| `servo_settle_ms` | `200 ms` | `50～3000 ms` |
| `fruit_settle_ms` | `350 ms` | `50～3000 ms` |
| `final_gate_return_delay_ms` | `300 ms` | `0～3000 ms` |
| `idle_command_poll_interval_ms` | `250 ms` | `100～5000 ms` |

所有值必須為 `50 ms` 的倍數。`POST /api/capture_timing/` 只能在 Django idle、沒有 active fruit 與 motor command 時更新完整 profile。Django 原子保存至 `Django_Server/runtime_config/capture_timing.json` 並提高 revision；ESP32 只在 idle、沒有 sequence／pending report 且三閘門 Home 時套用，再回報 `timing_config_applied`。

每顆 fruit 開始時 snapshot 前四項機構 timing，流程中不得覆寫。舊四欄設定保留原值、補入 idle polling 預設值並提高 revision。

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
| Sorter pending／running deadline | 各 `30 s` |

伺服移動、果實停穩與 report pending 階段不輪詢 command。Wi-Fi、HTTPS、感測、伺服 phase 與 retry 必須使用 deadline；timeout 或 `Connection: close` 後關閉並安全重建 client。

## API

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/state/` | Dashboard 完整狀態 |
| `GET` | `/api/camera/state/` | 手機精簡 capture state |
| `POST` | `/api/capture_timing/` | 更新 timing profile |
| `POST` | `/api/capture_started/` | Best-effort 拍攝 telemetry |
| `POST` | `/api/upload_images/` | 上傳指定站點照片 |
| `GET` | `/api/esp32/command/?format=text` | ESP32 取得 motor command |
| `POST` | `/api/esp32/report/` | ESP32 回報 trigger、station、完成或失敗 |
| `POST` | `/api/discard/` | 跳過／刪除目前暫存果實 |

`/api/camera/state/` 禁止快取且只讀記憶體狀態與套用 session timeout，不得掃描 Dataset 或同步 metadata／counter。手機 polling 必須 single in-flight；頁面進入背景時停止，回到前景才重啟。每次 upload 必須帶 `fruit_id`、`capture_token` 與 `station_index`。

## 觸發與三站流程

### 自動首站捷徑

1. HC-SR04 進入觸發範圍，ESP32 產生 `trigger_id`。
2. 只有 Firmware idle、沒有 sequence、三閘門追蹤為 Home 且已等待首站停穩時，才可回報 `hcsr04_station_1_ready`。
3. Report 必須帶同一個 `trigger_id`、`station_index=1`、`gates_home=true` 與 `station_settled=true`。
4. Django 驗證鎖定條件後建立或找回 active fruit，開放第 1 站拍攝。
5. 可回退的協定拒絕才改走標準流程；HTTP timeout 只能重送原 `trigger_id`。收到 `ignored` 時停止本次 trigger 並等待重新待命。

Gate 1 已實際攔住果實是部署前提，並非額外位置感測。捷徑只縮短第 1 站前控制往返，不放寬照片保存交握。

### 標準與手動流程

手動拍攝只取代 HC-SR04 開始請求。Django 建立 `start_sequence`，ESP32 取得後回報 `station_1_ready`。Legacy `hcsr04_trigger` response 若已含 `start_sequence`，ESP32 可直接執行；POST timeout 時先 fast poll 既有 command，不立即假定失敗。

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
start_sequence -> station_1_ready 使用 start_sequence command_id
release_gate_1 -> station_2_ready 使用 release_gate_1 command_id
release_gate_2 -> station_3_ready 使用 release_gate_2 command_id
release_gate_3 -> capture_sequence_finished 使用 release_gate_3 command_id
classify_fruit -> sorter terminal report 使用 classify_fruit command_id
```

Django 建立 command 前，取記憶體與 `Django_Server/runtime_config/motor_command_sequence.json` 的較大值加一並原子保存，成功後才公開。檔案不存在時從 `1` 開始；損壞或寫入失敗時回傳 `motor_command_id_persist_failed` 並拒絕建立。Dataset reset 不重設序列。

以下狀況禁止新的自動或手動 capture：

- 已有 active fruit 或未分類 temp fruit。
- Capture 不在 idle。
- Sorter 為 pending／running。
- 單一 motor command slot 尚有命令。

首站捷徑以 `trigger_id` 做 server-side 冪等；legacy trigger 依 active fruit 與既有 command 防重。Firmware 以 command ID 去重實體動作，report retry 不得再次轉動伺服。Terminal sorter 收到 late duplicate report 時回 `200 ignored`，不得改寫既有結果。

## 分類與 Dataset

三站完成後，Django 先搬移照片、寫入 `metadata.csv`、推進 counter 並清除 active Dataset 狀態，再嘗試建立 `classify_fruit`。資料操作失敗時不得建立分類 command；資料已提交但 command 建立失敗時，不得反向搬移或回滾 metadata。

Sorter 使用 `idle／pending／running／completed／failed／timeout`，不覆蓋 capture status。Pending／running 期間拒絕下一次 capture、recapture、reset 與第二筆分類。MG996R 無位置回授，completed 只代表控制時序完成並成功回報。

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

跳過／刪除先嘗試實體刪除；Windows 檔案占用時移至 `_delete_pending`，仍失敗則持久標記待清理並跳過該 fruit ID。成功結果必須清除 capture token、等待計時器、fast-path 與 motor command，安全回到 idle。

## 驗收

### 自動化

- Django：`C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app`
- Firmware：以 `esp32:esp32:esp32` 與 `ESP32Servo 3.2.1` 編譯正式 sketch。
- Repository：`git diff --check`

### 實機

1. 空軌連續 `100` 次，確認 Echo 等待與完整感測時間未超過設定門檻。
2. 健康網路連續 `20` 次自動採集；實體偵測至首張保存的 median ≤ `1.0 s`、p95 ≤ `1.5 s`。
3. 高速錄影確認第 2、3 站 ready 前果實已停止，且三張照片清晰。
4. 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站、重複 trigger 與 duplicate command。
5. 驗證未分類鎖定、刪除復原、四個分類出口、Home 歸位與 report retry 不重複作動。
