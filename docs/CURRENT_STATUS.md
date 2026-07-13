# 目前狀態

更新日期：2026-07-13

目前版本進度：`v1.2.3, 補充決策層與 ESP32 快速部署資源`

## 已完成

- `Necessary_library/README.md` 已整理目前正式 ESP32 firmware 的快速燒錄需求；舊版 WiFi／Servo／HCSR04 函式庫、Node.js 安裝包、Node-RED flow、SQL 與編譯產物維持本機忽略，不列為正式依賴。
- `decision_layer/README.md` 已建立未來 AI／XGBoost 決策層的整合邊界；目前仍由人工按鈕產生既有 `classify_fruit` 命令，尚未實作自動推論。
- 根目錄 `README.md` 已精簡為同學電腦可快速復現的 Windows 部署指南，包含 Python `3.10.20`、Conda／`venv`、iPhone 熱點固定 IP `172.20.10.3`、Windows 防火牆與同學家 Wi-Fi 備案。
- Django 已透過 `python-dotenv` 自動載入 repository 根目錄的 `.env`；本機 Django 密鑰、Wi-Fi 密碼與 firmware `secrets.h` 仍由 `.gitignore` 排除，不會納入版本控制。
- 資料採集流程已從「滾動中連拍 6 張」切換為「三段 SG90 閘門停止拍攝 3 張」。
- Django 三站中央狀態機已完成。
- Dashboard 手動拍攝入口已完成，且不繞過 ESP32 閘門流程。
- HC-SR04 自動觸發入口已完成，並與手動拍攝共用同一套三站流程。
- 手機 `/camera/` 已改為單站單張拍攝與上傳。
- ESP32 正式三閘門 firmware 已完成，位置為 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。
- ESP32 command polling、station report、pending retry、Wi-Fi reconnect 與 timeout 容錯已具備。
- `hcsr04_trigger` 可從 Django response 直接取得並執行 `start_sequence`，減少一次 command polling。
- 正式 firmware 已拆為薄入口 `.ino`、`Config`、`ProtocolTypes`、`DistanceSensor`、`GateController`、`ClassifierController`、`DjangoApiClient` 與 `CaptureController` 模組，流程狀態不再散落於多個 `.ino` tab。
- HC-SR04 改為直接輸出 `10 us` Trigger pulse，Echo 無回波最多等待 `12000 us`。
- Wi-Fi 重連、伺服 phase 與等待時間改為 deadline 驅動；Wi-Fi 失敗不再在主迴圈同步等待數秒。
- 自動觸發可使用受守門的 `hcsr04_station_1_ready` 首站捷徑；本地安全條件不符、捷徑停用或收到可回退的協定拒絕時，會安全回退既有 `hcsr04_trigger -> start_sequence -> station_1_ready`。Gate 1 已攔住果實仍是實機部署前提，並非目前的硬體回授訊號。
- `trigger_id` 是首站捷徑與 timeout retry 的 server-side 冪等鍵；legacy `hcsr04_trigger` 的重複防護仍由 Django active fruit 與既有 motor command 狀態負責。
- Django 已提供禁止快取的 `/api/camera/state/`；手機頁使用 `250 ms` idle／`50 ms` active 的 single in-flight polling，並在 state request 加上 `AbortController` timeout。高頻路徑已不再重複掃描 dataset、讀取 counter 或驗證 metadata schema。
- `capture_started` 已改為不阻塞拍照與上傳的 timing telemetry；上傳 `capture_meta` 會帶 client 端 request、影格、blob 與 upload timing。
- Dashboard 可顯示最近的 transition trace；舊版後端未傳回 trace 時會顯示相容提示。
- Dashboard 可持久化管理四項拍攝停穩設定與 ESP32 閒置命令輪詢間隔；最後一版數值固定原子覆寫至 `runtime_config/capture_timing.json`，Django 以 revision 下發，ESP32 僅在 idle 套用並回報 `timing_config_applied`。
- Dashboard 三站縮圖固定為直式 `3:4`，可點擊開啟原始比例的網頁內彈窗預覽；未收到 WebRTC 影像軌時會收合預覽區，避免留白。
- 「跳過／刪除」會清除 active fruit、等待計時器、capture token、馬達命令與 fast-path 狀態，回到 `idle`；若 Windows 鎖住檔案，會隔離或持久標記待清理並跳過該 ID，避免阻擋下一顆。
- 未分類 temp fruit 存在時，Django 會透過 `auto_trigger_enabled=0` 鎖住自動觸發。
- Dashboard 已可顯示三站狀態、照片預覽、分類與刪除操作。
- WebRTC 即時預覽已可運作，但不是拍攝流程的必要條件。
- `metadata.csv` 採用三站欄位：`station_01_ok`、`station_02_ok`、`station_03_ok`。
- 人工資料分類成功後，Django 會在既有單一 motor command slot 建立 `classify_fruit`；ESP32 使用 GPIO `25` 的 MG996R 完成實體分類並回報結果。
- Sorter 狀態與拍攝主狀態分離；pending／running 期間會鎖住下一次自動與手動拍攝，失敗或逾時不回滾照片資料。
- Motor command ID 已原子持久化於 `runtime_config/motor_command_sequence.json`，Django 重啟與 dataset reset 不會重用最後一筆 ID。

## 目前硬體與 timing

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。
- MG996R：GPIO `25`，Home `85°`，上中等 `25°`、下等 `55°`、加工 `115°`、廢棄 `145°`；保持 `1000 ms`、歸位穩定 `500 ms`、timeout `5000 ms`。
- HC-SR04 觸發距離：`6.0 cm`。
- HC-SR04 重新待命距離：`8.0 cm`。
- `servo_settle_ms = 200`。
- `fruit_settle_ms = 350`。
- 第 1 站停穩：`300 ms`。
- 第 3 站放行後歸位前額外等待：`300 ms`。
- 五項 Dashboard 推薦值依序為 `300 / 200 / 350 / 300 / 250 ms`；前四項最多 `3000 ms`，idle command polling 可在 `100～5000 ms` 間以 `50 ms` 為步進調整。最後一版調整會跨重啟保留，舊版四欄設定會自動補入 `250 ms` 並提高 revision。
- ESP32 idle command polling：預設 `250 ms`，使用已套用的 `idle_command_poll_interval_ms`。
- 等待 `start_sequence` command polling：`100 ms`。
- 等待 `release_gate` command polling：`50 ms`；伺服移動、果實停穩與 report pending 階段不輪詢 command。
- `hcsr04_trigger` POST timeout：`1000 ms`。
- command GET timeout：`1500 ms`。
- station report POST timeout：`5000 ms`。
- HC-SR04 Echo timeout：`12000 us`；完整 `sensor_read_us` 的 firmware warning 門檻約為 `12100 us`。
- 手機 `/api/camera/state/` polling：single in-flight，idle `250 ms`、active `50 ms`、state timeout `1000 ms`。
- Django runtime state、dataset cache、timing persistence 與 ESP32 response shaping 已分離；分類、刪除與 reset 使用 operation token，檔案搬移／刪除在全域狀態鎖外執行。前景檔案重試上限約為 `100 ms`。
- ESP32 report 使用 firmware 相容的精簡 response，不再回傳 dashboard 圖片、labels、完整 trace 或 timing telemetry。
- 相機與 dashboard 的 CSS／JavaScript 已移至 Django static assets；dashboard 只在圖片 manifest 改變時重建縮圖，圖片 API 使用串流與 `ETag`／`Last-Modified`。
- HTTPS connect／read 皆使用每種 request 的 deadline：trigger `1000 ms`、command `1500 ms`、report `5000 ms`。正常時重用同 origin HTTP/1.1 TLS 連線；Wi-Fi 斷線、timeout 或 `Connection: close` 時關閉 client 並重建。

## 目前觀察到的瓶頸

- 第一張照片的主觀延遲預期由首站捷徑、短 Echo timeout、TLS 連線重用與手機 active polling 降低。transition trace 的起點是 Django 收到首站事件，不是 HC-SR04 實體偵測；總延遲仍須以 `20` 次實機資料、firmware Serial timing 與高速錄影驗證中位數 ≤ `1.0 s`、`p95` ≤ `1.5 s`。
- HTTPS 在 ESP32 上偶爾會出現 `READ_TIMEOUT`；目前以 connect／read timeout、fast polling、同一 `trigger_id` retry 與 client 重建降低卡死與重複建立 fruit 的風險。
- 經裝置校正後，目前四項機構 timing 採 `300 / 200 / 350 / 300 ms`；若實機仍出現模糊，再以 `50 ms` 為單位逐次增加第 1 站或到站停穩時間。
- WebRTC 預覽有時可能受瀏覽器、iPhone 熱點或 ICE 狀態影響，但不應阻塞實際拍攝上傳。

## 待辦

1. 以高速錄影確認第 2、3 站 ready 回報前百香果已停止；若仍模糊，再評估 IR break-beam／存在感測器。
2. 空軌執行至少 `100` 次，記錄 Echo 等待上限與完整 `sensor_read_us` 是否都未跨過約 `12.1 ms` warning 門檻。
3. 健康網路完成連續 `20` 次自動採集；以 Django trace 觀察伺服器端鏈路，並以同次 firmware Serial timing 與高速錄影量測 HC-SR04 實體偵測到第 1 張原子保存的中位數與 `p95`。
4. 驗證 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站照片與重複 trigger 時，流程不會提早放行或建立重複 fruit。
5. 依實測微調 `triggerDistanceCM`、第 1 站機構位置與 `firstStationSettleMS`；維持現有人工按鈕與 MG996R 流程，穩定後再接入 AI 自動決策。

## 測試

本版已完成的自動化驗證：

- Django `fruit_app`：`59` 項測試通過。
- 相機與 dashboard static JavaScript syntax check 通過。
- `git diff --check` 通過。
- 正式 firmware 已以 `esp32:esp32:esp32` 編譯通過；整合第四顆 Servo 後 Flash 約 `82%`、RAM 約 `15%`。

Django 測試指令：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

實機驗收仍需檢查 transition trace、三站照片清晰度、未分類 temp 鎖定，以及 Echo 分壓後 ESP32 GPIO 正常目標 ≤ `3.3 V`、絕不可超過 `3.6 V`。

安全與品質稽核見 [SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)。目前僅適用可信任、隔離的實驗室區網；未修補的 TLS、API authentication、Django deployment 設定與資源上限問題不得忽略。
