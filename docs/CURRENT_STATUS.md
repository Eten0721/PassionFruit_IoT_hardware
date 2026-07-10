# 目前狀態

更新日期：2026-07-10

目前版本進度：`v1.1.6, 首站捷徑與採集流程加速重構`

## 已完成

- 資料採集流程已從「滾動中連拍 6 張」切換為「三段 SG90 閘門停止拍攝 3 張」。
- Django 三站中央狀態機已完成。
- Dashboard 手動拍攝入口已完成，且不繞過 ESP32 閘門流程。
- HC-SR04 自動觸發入口已完成，並與手動拍攝共用同一套三站流程。
- 手機 `/camera/` 已改為單站單張拍攝與上傳。
- ESP32 正式三閘門 firmware 已完成，位置為 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。
- ESP32 command polling、station report、pending retry、Wi-Fi reconnect 與 timeout 容錯已具備。
- `hcsr04_trigger` 可從 Django response 直接取得並執行 `start_sequence`，減少一次 command polling。
- 正式 firmware 已拆為薄入口 `.ino`、`Config`、`ProtocolTypes`、`DistanceSensor`、`GateController`、`DjangoApiClient` 與 `CaptureController` 模組，流程狀態不再散落於多個 `.ino` tab。
- HC-SR04 改為直接輸出 `10 us` Trigger pulse，Echo 無回波最多等待 `12000 us`。
- Wi-Fi 重連、伺服 phase 與等待時間改為 deadline 驅動；Wi-Fi 失敗不再在主迴圈同步等待數秒。
- 自動觸發可使用受守門的 `hcsr04_station_1_ready` 首站捷徑；本地安全條件不符、捷徑停用或收到可回退的協定拒絕時，會安全回退既有 `hcsr04_trigger -> start_sequence -> station_1_ready`。Gate 1 已攔住果實仍是實機部署前提，並非目前的硬體回授訊號。
- `trigger_id` 是首站捷徑與 timeout retry 的 server-side 冪等鍵；legacy `hcsr04_trigger` 的重複防護仍由 Django active fruit 與既有 motor command 狀態負責。
- Django 已提供禁止快取的 `/api/camera/state/`；手機頁使用 `250 ms` idle／`75 ms` active 的 single in-flight polling，並在 state request 加上 `AbortController` timeout。
- `capture_started` 已改為不阻塞拍照與上傳的 timing telemetry；上傳 `capture_meta` 會帶 client 端 request、影格、blob 與 upload timing。
- Dashboard 可顯示最近的 transition trace；舊版後端未傳回 trace 時會顯示相容提示。
- Dashboard 可持久化管理四項拍攝停穩設定；最後一版數值固定原子覆寫至 `runtime_config/capture_timing.json`，Django 以 revision 下發，ESP32 僅在 idle 套用並回報 `timing_config_applied`。
- Dashboard 三站縮圖固定為直式 `3:4`，可點擊開啟原始比例的網頁內彈窗預覽；未收到 WebRTC 影像軌時會收合預覽區，避免留白。
- 「跳過／刪除」會清除 active fruit、等待計時器、capture token、馬達命令與 fast-path 狀態，回到 `idle`；若 Windows 鎖住檔案，會隔離或持久標記待清理並跳過該 ID，避免阻擋下一顆。
- 未分類 temp fruit 存在時，Django 會透過 `auto_trigger_enabled=0` 鎖住自動觸發。
- Dashboard 已可顯示三站狀態、照片預覽、分類與刪除操作。
- WebRTC 即時預覽已可運作，但不是拍攝流程的必要條件。
- `metadata.csv` 採用三站欄位：`station_01_ok`、`station_02_ok`、`station_03_ok`。

## 目前硬體與 timing

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。
- HC-SR04 觸發距離：`6.0 cm`。
- HC-SR04 重新待命距離：`8.0 cm`。
- `servo_settle_ms = 200`。
- `fruit_settle_ms = 200`。
- 第 1 站停穩：`200 ms`。
- 第 3 站放行後歸位前額外等待：`200 ms`。
- 四項 Dashboard 推薦校正值皆為 `200 ms`；可在 idle 時以 `50 ms` 為步進調整至最多 `3000 ms`。最後一版調整會跨重啟保留，且舊版 dataset timing 檔會遷移後移除。
- ESP32 idle command polling：`5000 ms`。
- 等待 `start_sequence` command polling：`100 ms`。
- active sequence command polling：`120 ms`。
- `hcsr04_trigger` POST timeout：`1000 ms`。
- command GET timeout：`1500 ms`。
- station report POST timeout：`5000 ms`。
- HC-SR04 Echo timeout：`12000 us`；完整 `sensor_read_us` 的 firmware warning 門檻約為 `12100 us`。
- 手機 `/api/camera/state/` polling：single in-flight，idle `250 ms`、active `75 ms`、state timeout `1000 ms`。
- HTTPS connect／read 皆使用每種 request 的 deadline：trigger `1000 ms`、command `1500 ms`、report `5000 ms`。正常時重用同 origin HTTP/1.1 TLS 連線；Wi-Fi 斷線、timeout 或 `Connection: close` 時關閉 client 並重建。

## 目前觀察到的瓶頸

- 第一張照片的主觀延遲預期由首站捷徑、短 Echo timeout、TLS 連線重用與手機 active polling 降低。transition trace 的起點是 Django 收到首站事件，不是 HC-SR04 實體偵測；總延遲仍須以 `20` 次實機資料、firmware Serial timing 與高速錄影驗證中位數 ≤ `1.0 s`、`p95` ≤ `1.5 s`。
- HTTPS 在 ESP32 上偶爾會出現 `READ_TIMEOUT`；目前以 connect／read timeout、fast polling、同一 `trigger_id` retry 與 client 重建降低卡死與重複建立 fruit 的風險。
- 經裝置校正後，目前以全 `200 ms` profile 作為拍攝基準；若實機仍出現模糊，再以 `50 ms` 為單位逐次增加第 1 站或到站停穩時間。
- WebRTC 預覽有時可能受瀏覽器、iPhone 熱點或 ICE 狀態影響，但不應阻塞實際拍攝上傳。

## 待辦

1. 以高速錄影確認第 2、3 站 ready 回報前百香果已停止；若仍模糊，再評估 IR break-beam／存在感測器。
2. 空軌執行至少 `100` 次，記錄 Echo 等待上限與完整 `sensor_read_us` 是否都未跨過約 `12.1 ms` warning 門檻。
3. 健康網路完成連續 `20` 次自動採集；以 Django trace 觀察伺服器端鏈路，並以同次 firmware Serial timing 與高速錄影量測 HC-SR04 實體偵測到第 1 張原子保存的中位數與 `p95`。
4. 驗證 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站照片與重複 trigger 時，流程不會提早放行或建立重複 fruit。
5. 依實測微調 `triggerDistanceCM`、第 1 站機構位置與 `firstStationSettleMS`；穩定後再整合 AI 推論與分類器。

## 測試

本版已完成的自動化驗證：

- Django `fruit_app`：`35` 項測試通過。
- 相機與 dashboard 內嵌 JavaScript syntax check 通過。
- `git diff --check` 通過。
- 正式 firmware 已以 `esp32:esp32:esp32` 編譯通過。

Django 測試指令：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

實機驗收仍需檢查 transition trace、三站照片清晰度、未分類 temp 鎖定，以及 Echo 分壓後 ESP32 GPIO 正常目標 ≤ `3.3 V`、絕不可超過 `3.6 V`。

安全與品質稽核見 [SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)。目前僅適用可信任、隔離的實驗室區網；未修補的 TLS、API authentication、Django deployment 設定與資源上限問題不得忽略。
