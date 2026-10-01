# 送料、三站資料採集與分類規格

## 目標與範圍

正式流程由 Django 中央狀態機、ESP32 以 XINHUI `60KG` 連續旋轉伺服執行上游單顆送料、HC-SR04、手機單站拍攝、三段位置型 MG996R 閘門與一顆位置型 MG996R 下置式分類器組成。每顆百香果在三個固定站點各保存一張照片；第 3 張保存後果實停留在 Gate 3，直到人工分類完成、分類器就位並執行 Gate 3 放行。實體分類完成後才允許送入下一顆。本文是送料、三站流程、API、command ID、GPIO、角度、timing 與分類契約的唯一來源。

原始上游送料整合由 GitHub Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 記錄；本文件保存 ADR-0016 接受後的目標契約，實作與實機驗收進度以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 為準。2026-08-22 的平台、分槽盤、下置式分類器與雙電源決策見 [ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md)。原 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的額外 SG90 擋臂已取消。

## 名詞

- Capture session：一顆果實從觸發至三站完成的工作階段。
- Station ready：ESP32 宣告果實已在指定站點停穩。
- Capture request：Django 通知手機拍攝指定站點。
- Upload complete：Django 驗證並原子保存指定照片。
- 自動運轉：操作員啟用後，Django 只在安全邊界建立下一次送料命令的運轉模式；Django 或 ESP32 重新啟動後不自動恢復。
- 一體式送料筒：同一個圓筒同時提供百香果儲存、底部撥料與側面出料空間；目標內徑為 `20～22 cm`、內部高度為 `30 cm`。
- 分槽送料盤：直立於送料筒底部中央的 XINHUI `60KG` 連續旋轉伺服帶動扭蛋機式分槽盤，將分隔後的果實送向側面出口；槽數、槽寬與盤片間隙由實機驗證決定。
- 出口擋板：位於側面出口上方、限制多顆果實同時靠近出口的固定件；下緣距底板 `9 cm`、向筒內延伸 `9 cm`，側面出口寬度為 `9 cm`。
- 送料循環：XINHUI 連續旋轉伺服從開始驅動至停止的一次 `feed_one` 實體動作；成功契約是實體恰好送出一顆，不以固定角度判定。HC-SR04 只確認有果實抵達，不能計數。
- 感測區清空：HC-SR04 取得一次有效且大於 `8.0 cm` 的距離；`0 cm`／Echo timeout 不是清空。
- 果實抵達確認：送料期間 HC-SR04 取得一次有效且小於等於 `6.0 cm` 的距離；同一筆讀值正常終止送料並啟動首站流程。
- 送料安全逾時：`feeder_max_run_ms` 先於果實抵達確認到期；ESP32 立即停止馬達，Django 暫停自動送料且不得自動補轉。
- 進料未確認：送料因安全逾時、重新啟動或不確定結果停止，但 HC-SR04 尚未確認果實抵達；不得推測為料斗已空。
- 優雅暫停：立即禁止建立後續送料命令，已承諾送出的目前果實仍完成拍攝與實體分類。
- Gate 3 等待分類：第 3 張照片已保存，但 Gate 3 保持 Home、果實仍留在第 3 站，等待 `classify_fruit`。
- Motor command：`feed_one`、`start_sequence`、`release_gate` 或 `classify_fruit`；`release_gate` 以 `station_index` 區分第 1、2 站。
- Command slot：送料、Capture 與 sorter 共用的單一命令槽。

## 硬體設定

正式 Firmware：`firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`

| 裝置 | GPIO | 目標設定 |
|---|---:|---|
| Gate 1 position-control MG996R | `18` | Home `0°`，Release `90°` |
| Gate 2 position-control MG996R | `19` | Home `0°`，Release `90°` |
| Gate 3 position-control MG996R | `21` | Home `0°`，分類器就位後才 Release `90°` |
| Upstream feeder XINHUI `60KG` continuous rotation | `23` | HC-SR04 回授停止，最大運轉時間安全保護 |
| HC-SR04 Trigger | `26` | `10 us` pulse |
| HC-SR04 Echo | `27` | `12000 us` timeout，輸入必須安全降壓 |
| Position-control MG996R sorter | `25` | Home `85°` |

三站閘門先沿用直接寫入 `0°／90°` 的控制方式，實際角度須以無干涉且能穩定推動果實的實機結果為準。新平台若仍發生夾果或過度推擠，再依 Issue [#16](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/16) 評估非阻塞小角度遞增；該觀察不阻擋 Gate 3／分類器複合流程。若後續實作慢速控制，必須使用 deadline 推進，不能以長時間同步迴圈阻塞 HTTPS、感測與安全停止。

送料方向由馬達安裝位置及 `feeder_drive_us` 位於校正停止值的哪一側共同決定，Firmware 不固定順時針對應的脈波側。正式流程不提供反轉清料、位置 Home 或自動補轉；更換馬達位置或修改驅動脈波後必須重新測試送料。

XINHUI `60KG` 送料馬達直立於筒底中央並帶動分槽送料盤。送料筒頂部保持開放；馬達底座旁保留線材孔，線材直接離開送料筒，筒內不得留下鬆弛線圈或接頭。分槽盤下緣不得摩擦底板，外緣不得碰觸筒壁；槽數、槽寬、盤片厚度、垂直間隙、筒壁間隙與舵盤固定方式均須以最大果實尺寸及連續送料結果驗證。

側面出口直接對齊拍攝平台起點，不另設導料斜道；出口底部與拍攝平台起點的高低差為 `8.5～9 cm`。現階段受可取得果實數量限制，只以 `10` 顆驗證裝載能力；超過 `10` 顆與正式安全填料線均維持未驗證，不使用未量測的「滿載」作為驗收條件。

下置式分類器位於平台出口正下方，平台末端使用名目直徑 `10 cm` 的落料管，圓形舵盤上的ㄇ型鐵帶輕微坡度並導向四個籃子。位置型 MG996R 分類位置：

| 中文分類 | Command code | 角度 |
|---|---|---:|
| 上等 | `high` | `55°` |
| 中等 | `medium` | `70°` |
| 下等 | `low` | `100°` |
| 加工 | `processing` | `115°` |

Firmware 可接受舊輸入 alias `high_medium` 與 `discard`，分別套用上等與中等的新角度；Django 與新決策層不得再產生 alias。分類器收到目標角度後先以開迴路方式等待 `500 ms`，再將 Gate 3 直接放行至 `90°`；從 Gate 3 到達 Release 後計時，分類器在目標角度保持 `1000 ms`。落果等待完成後，三顆 Gate 同時回 Home `0°`，分類器同時回 Home `85°`，共同歸位等待 `500 ms` 後才可回報完成。MG996R 沒有位置回授，這些等待只代表控制時序完成，不證明實際位置。複合動作 timeout 必須涵蓋分類器轉向、Gate 3 放行、落果保持與四顆馬達歸位，最終值由實作與實機量測決定。

伺服供電使用兩組獨立 `AC 110 V → DC 12 V／20 A` 電源。電源 A 經 LM25116 初始降至 `6.0 V`，供三站與分類器四顆 MG996R，且不得超過 `6.6 V`；電源 B 經另一顆 LM25116 降至 `8.4 V`，只供 XINHUI 送料馬達。兩路正極不得互接，兩路 DC 負極與 ESP32 GND 必須共地。完整配線、安全與負載估算見 [`record_image/硬體接線與驗收摘要.md`](../record_image/硬體接線與驗收摘要.md)。

HC-SR04 觸發距離為 `6.0 cm`，重新待命距離為 `8.0 cm`，讀取間隔為 `50 ms`。一次有效觸發即可立即停止送料；只有有效距離大於 `8.0 cm` 才可重新待命。`0 cm`／Echo timeout 代表感測器異常或線材問題，送料前必須拒絕命令，送料中必須立即停止。Echo 分壓、伺服供電與機械驗收見 [`record_image/硬體接線與驗收摘要.md`](../record_image/硬體接線與驗收摘要.md)。

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
| `feeder_max_run_ms` | `5000 ms` | `1000～20000 ms` | `500 ms` |

`feeder_drive_us` 不得等於 `feeder_stop_us`，但不限制兩者差值或驅動脈波位於停止值的哪一側。`feeder_max_run_ms` 是唯一運轉上限；Dashboard 與 Firmware 不得另設隱藏上限或靜默截斷。Dashboard 必須在欄位旁固定顯示「警告 : 若設定過長將導致連續送料之情形發生」。

`POST /api/capture_timing/` 只能在自動運轉停止、Django idle、沒有 active fruit、sorter 動作與 motor command 時更新完整 profile。此 endpoint 只更新數值，不接受 client 直接設定 `feeder_calibrated`。Django 原子保存至 `Django_Server/runtime_config/capture_timing.json` 並提高 revision；ESP32 只在 idle、沒有 sequence／pending report、三閘門 Home 且送料馬達停止時套用，再回報 `timing_config_applied`。

每顆 fruit 開始時 snapshot 前四項拍攝機構 timing；每次建立 `feed_one` 時 snapshot 三項送料設定，流程中不得覆寫。

`feeder_calibrated` 預設為 false，由 Django 管理並與 profile 一起原子保存。單次確認流程固定為：套用設定、等待 ESP32 ACK 相同 revision、執行一次「測試送料一次」、由 HC-SR04 正常停止，最後由操作員目視確認恰好送出一顆並勾選 checkbox。Checkbox 立即呼叫 `POST /api/feeder/calibration/confirm/` 保存目前 revision，不得要求第二次測試或再次套用設定；保存失敗時介面必須恢復未勾選並顯示具體錯誤。重新整理後仍以伺服器保存值為準。

完整機構驗收仍先在空送料筒確認停止脈波沒有爬行，再放入 `10` 顆，連續執行 `10` 次測試且不補回已送出的果實，覆蓋剩餘量由 `10` 顆降至 `1` 顆的情境。驅動脈波由 `1300 us` 起測，校正為能可靠帶動分槽盤且不造成雙送的最慢值；最大運轉時間由操作員依實測結果自行指定，系統只驗證它位於 `1000～20000 ms` 且為 `500 ms` 的倍數，不套用自動計算公式。每次都必須由 HC-SR04 正常停止並目視確認恰好送出一顆；任一次漏送、雙送、卡料、分槽盤停滯或安全逾時都要在調整後將同一批果實放回，從 `10` 顆重新累計。測試模式不得建立 Capture session。超過 `10` 顆的裝載量必須重新校正與驗收。

完成十顆送料後，必須在空送料筒至少執行一次完整逾時測試。操作員不得提前中止該次測試；ESP32 必須在指定的 `feeder_max_run_ms` 到期時先於本機停止，回報 `feeder_max_run_timeout`，Dashboard 顯示進料未確認，且不得建立 Capture session 或自動補轉。其他空筒情境仍等待 HC-SR04 或最大運轉時間停止；Dashboard「優雅暫停」只禁止後續送料，不中止目前 `feed_one`。

修改 `feeder_stop_us`、`feeder_drive_us` 或 `feeder_max_run_ms`，以及主動開始另一輪測試，都會取消既有確認；只修改拍攝 timing 時保留確認，但仍須等待新 revision ACK。測試失敗或逾時不得確認。確認 endpoint 只接受相同 revision 的成功測試、最新 ACK 且系統 idle；確認本身不得提高 revision 或建立第二個 motor command。舊設定檔升級時保留停止與驅動脈波，移除 `feeder_run_ms` 與 `fruit_arrival_warning_ms`，將 `feeder_max_run_ms` 設為 `5000 ms`、取消確認並提高 revision。設定檔遺失、損壞或驗證失敗時載入推薦值、取消確認並在 Dashboard 明確警示，不得靜默恢復正式送料。ESP32 尚未 ACK 最新 revision 時不得測試送料或開始正式運轉；ACK、校正及其餘開始條件皆成立後，Dashboard 才開放「開始執行」。

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
| `POST` | `/api/capture_timing/` | 更新 runtime profile 數值 |
| `POST` | `/api/collection_options/` | 安全閒置時選擇工作與硬體模式 |
| `POST` | `/api/photo_capture/` | 僅純拍攝可用的單輪三張入口 |
| `POST` | `/api/auto_run/` | 啟用正式自動運轉或要求優雅暫停 |
| `POST` | `/api/feeder/test/` | 在安全 idle 狀態測試一次 HC-SR04 回授送料 |
| `POST` | `/api/feeder/calibration/confirm/` | 保存目前 revision 的人工送料確認 |
| `POST` | `/api/capture_started/` | Best-effort 拍攝 telemetry |
| `POST` | `/api/upload_images/` | 上傳指定站點照片 |
| `GET` | `/api/esp32/command/?format=text` | ESP32 取得 motor command |
| `POST` | `/api/esp32/report/` | ESP32 回報 trigger、station、完成或失敗 |
| `POST` | `/api/discard/` | 跳過／刪除目前暫存果實 |

`/api/camera/state/` 禁止快取且只讀記憶體狀態與套用 session timeout，不得掃描 Dataset 或同步 metadata／counter。手機 polling 必須 single in-flight；頁面進入背景時停止，回到前景才重啟。只有相機 stream 存在、video track 為 live、已有可擷取影格與有效尺寸時，手機才在 polling query 回報 `camera_ready=1`；Django 只在記憶體保存最後 ready 時間。每次 upload 必須帶 `fruit_id`、`capture_token` 與 `station_index`。

Dashboard 的即時預覽外框在未連線時保持隱藏，左欄不得被右欄內容高度拉伸；連線後 video 使用 `width: auto`、`max-width: 100%`、`height: auto` 依來源原始比例縮放，並以 `min(52vh, 520px)` 限制預覽高度，不得固定預留 `16:9` 高度或放大到超過單頁可預覽範圍。

ESP32 command polling 必須附帶本次開機唯一的 `boot_id`、送料能力 `capability=feeder_v1`、複合分類能力 `sorter_capability=gate3_sorter_v1`、`feeder_state=idle|awaiting_fruit`、`feeder_sensor_state=clear|blocked|unavailable` 與 `last_feed_command_id`。`clear` 只代表最近一次有效距離大於 `8.0 cm`；`0 cm`／Echo timeout 必須回報 `unavailable`，其餘為 blocked。Django 未收到 `feeder_v1`、ESP32 離線、感測器不是 `clear`、最新 revision 尚未 ACK，或 boot／feeder state 尚未完成復原時，不得建立測試 `feed_one`。Django 未收到 `gate3_sorter_v1` 時，禁止正式自動運轉，並在 Dataset 提交前拒絕人工或未來 AI 的分類請求。

## 自動運轉與單顆送料

### 開始條件

正式 production `feed_one` 已開放；Dashboard 與 `POST /api/auto_run/` 只在下列 runtime 安全條件全部成立時允許開始。

操作員按下 Dashboard「開始執行」時，Django 必須在同一個 `STATE_LOCK` critical section 內確認：

- ESP32 在線且回報 `feeder_v1` 與 `gate3_sorter_v1`。
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

「測試送料一次」重用完整停止邏輯，command context 為 calibration。開始測試會先撤銷舊確認。HC-SR04 成功觸發後 ESP32 回到 `idle`，Django 保持自動運轉關閉，只開放人工確認 checkbox，不建立 Capture session；勾選成功保存後即可直接使用「開始執行」，不再建立另一個測試 command。測試逾時只顯示停止原因與實際時間，不建立正式流程的 blocking error；感測器異常仍必須顯示可操作的錯誤。

### 下一顆果實

目前由人工分類按鈕提供四級分類結果，未來 AI 只可取代結果來源並進入相同分類邊界。Django 確認 `gate3_sorter_v1` 後，仍先提交 Dataset，再建立 `classify_fruit`。只有相符的 `classification_sorter_completed` 已清除 command slot，且自動運轉、相機 readiness 與全部開始條件仍成立時，Django 才建立下一個 `feed_one`。Sorter failed／timeout、Capture timeout、相機 stale 或任何 blocking error 都不得建立下一次送料；相機恢復時可自動繼續，其他錯誤由操作員優雅暫停並排除。

### 安全逾時、感測異常與延遲抵達

正式送料發生 `feeder_max_run_timeout` 時，Django 清除 command slot、關閉自動運轉並顯示「進料未確認」；ESP32 維持 HC-SR04 待命，但不得自動補轉。逾時不取消 `feeder_calibrated`，因為原因也可能是料斗已空或果實卡住；操作者修改任一送料參數時才依校正規則取消。

逾時與有效觸發落在同一次控制迴圈時，以逾時優先。若果實之後才觸發 HC-SR04，仍接手既有三站拍攝與分類，但自動送料保持關閉，完成後由操作員檢查送料區域並重新按「開始執行」。

送料前的 `0 cm`／Echo timeout 直接拒絕命令；送料中的相同錯誤立即停止馬達並關閉自動運轉。Dashboard 必須指示操作者檢查 HC-SR04 線材、供電、Echo 分壓與感測方向，不得將無回音解讀為感測區清空。

### 優雅暫停

暫停是 Django 控制旗標，不是 motor command，不占用、取消或覆寫單一 command slot，API 必須立即回應。與 `feed_one` 建立使用同一把 `STATE_LOCK`：

- 暫停旗標先寫入時，不建立下一次送料。
- `feed_one` 先建立時，視為已承諾；目前果實完成拍攝與實體分類後停止。
- 安全逾時後仍保持感測器待命；若實際沒有果實，不建立 Capture session，也不自動補轉。

暫停可發生於送料後等待、三站拍攝、等待人工分類、實體分類或分類後尚未建立下一次送料。Dashboard 主操作區只使用同一按鈕：停止時顯示「開始執行」、運轉時顯示「優雅暫停」、正在完成目前果實時顯示 disabled「正在完成目前果實」。本軟體暫停不是實體緊急斷電。

重拍與刪除成功時都關閉自動運轉。重拍由操作員將同一顆果實放回 Gate 1，再走既有 `start_sequence`，不驅動送料機構；完成後由操作員重新按「開始執行」。正式硬體流程不提供通用 manual capture；純拍攝僅使用下述受模式限制的獨立入口。

### 重新啟動與重複動作

ESP32 每次開機產生新的 `boot_id`。Django 若在未完成 `feed_one` 期間看到 boot 改變，必須關閉自動運轉、不向新 boot 重送該命令，保留 HC-SR04 待命並顯示操作員檢查提示；原則是寧可少送一次，也不能自動重複送料。

Django 重新啟動後，依 ESP32 polling 的 `feeder_state` 與 `last_feed_command_id` 重建「進料未確認」。看到 `awaiting_fruit` 時保持自動運轉關閉、允許 HC-SR04 接手，但不建立新 `feed_one`。重複或舊的 feeder terminal report 回 `200 ignored`，不得推進流程或造成 ESP32 永久 retry。

切斷送料專用電源 B 的 DC 輸出只用於送料機構緊急停止。若 Dashboard 仍可連線，操作員先要求優雅暫停，讓電源 B 保持關閉直到目前 `feed_one` 到達 `feeder_max_run_ms`，並確認 Dashboard 收到 `feeder_max_run_timeout`；清料且手離開送料筒後才可重新開啟電源 B，接著必須執行一次測試送料再恢復正式運轉。

若緊急斷電後無法連線或看不到 terminal report，送料專用電源 B 必須保持關閉。操作員清料後重新啟動 ESP32，等待 Firmware 完成初始化、先寫入 `feeder_stop_us`，並確認 Dashboard 顯示 ESP32 已重新連線且正式運轉保持停用；手離開送料筒後才可重新開啟電源 B，之後同樣必須執行一次測試送料。

### Dashboard 錯誤提示

Blocking error 必須以可存取的 `role="alert"` 區塊顯示發生位置、具體 reason、目前 fruit／command 與操作指引。Reason 至少區分 `feeder_max_run_timeout`、`feeder_sensor_not_clear`、`feeder_sensor_unavailable`、`camera_upload_timeout`、`classifier_timeout`、`feeder_capability_missing`、`feeder_calibration_required` 與 `esp32_restarted_during_feed`。送料錯誤的 alert 與送料結果都必須顯示 `feeder_elapsed_ms`、`feeder_max_run_ms` 與停止原因。標準操作文字為「暫停 → 排除／重新拍攝／刪除 → 開始執行」；存在 active fruit 時，「開始執行」保持停用。

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
3. 第 1、2 站原子保存成功後，Django 分別建立 `station_index=1`、`station_index=2` 的 `release_gate`。
4. ESP32 直接放行目前 Gate，等待伺服與果實停穩後回報下一站 ready；Gate 1 與 Gate 2 放行後維持 Release。
5. 第 3 站保存後，Django 將三張照片標記為可檢查與分類，不建立 `station_index=3` 的 `release_gate`；Gate 3 保持 Home，果實留在第 3 站。
6. 使用者分類時，Django 先確認 ESP32 已回報 `gate3_sorter_v1`；能力缺失時保留暫存照片與果實並拒絕分類，能力存在時才提交 Dataset 與 metadata，再建立 `classify_fruit`。
7. ESP32 將分類器轉至目標角度並等待 `500 ms`，再放行 Gate 3；Gate 3 到達 Release 後保持分類角度 `1000 ms`。
8. 落果等待完成後，三顆 Gate 同時回 Home `0°`，分類器同時回 Home `85°`；共同歸位等待 `500 ms` 後回報 `classification_sorter_completed`。
9. 失敗或結果不確定時保持自動運轉關閉，且不得自動重放實體動作。

任何固定延遲都不能取代 Django 確認照片保存成功。

## Command ID、互斥與冪等

```text
feed_one -> feed_cycle_completed | feeder_max_run_timeout | feeder_sensor_not_clear | feeder_sensor_unavailable 使用 feed_one command_id
start_sequence -> station_1_ready 使用 start_sequence command_id
release_gate + station_index=1 -> station_2_ready 使用該 release_gate command_id
release_gate + station_index=2 -> station_3_ready 使用該 release_gate command_id
classify_fruit -> Gate 3 與 sorter 複合 terminal report 使用 classify_fruit command_id
```

目標流程不再建立 `station_index=3` 的 `release_gate`，也不再以獨立 `capture_sequence_finished` 作為第 3 站實體放行完成事件；第 3 張照片保存本身建立可分類狀態，完整實體交握由相同 `classify_fruit` terminal report 結束。

Django 建立 command 前，取記憶體與 `Django_Server/runtime_config/motor_command_sequence.json` 的較大值加一並原子保存，成功後才公開。檔案不存在時從 `1` 開始；損壞或寫入失敗時回傳 `motor_command_id_persist_failed` 並拒絕建立。Dataset reset 不重設序列。每個 command 只能有一個 terminal report；相同 ID 的 retry 必須冪等。

以下狀況禁止新的 `feed_one`、自動 capture 或重拍：

- 已有 active fruit 或未分類 temp fruit。
- Capture 不在 idle。
- Sorter 為 pending／running。
- 單一 motor command slot 尚有命令。
- 相機 readiness stale、送料設定未 ACK／未確認，或 ESP32 未回報 `feeder_v1`／`gate3_sorter_v1`。

首站捷徑以 `trigger_id` 做 server-side 冪等；legacy trigger 依 active fruit 與既有 command 防重。Firmware 以 command ID 去重實體動作，report retry 不得再次轉動伺服。Terminal sorter 收到 late duplicate report 時回 `200 ignored`，不得改寫既有結果。

## 分類與 Dataset

第 3 張照片保存後，果實仍由 Gate 3 攔住。Django 必須先確認 ESP32 在線且回報 `gate3_sorter_v1`；已知能力不相容時，在 Dataset 提交前拒絕分類並保留暫存照片。能力確認後，Django 在人工分類時先搬移照片、寫入 `metadata.csv`、推進 counter 並清除 active Dataset 狀態，再嘗試建立 `classify_fruit`。資料操作失敗時不得建立分類 command；資料已提交後才發生的 command 建立或硬體失敗，不得反向搬移或回滾 metadata，Gate 3 也不得自動開啟。

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

## 純拍攝與 YOLO 檢測模式設計

本節記錄純拍攝與自動檢測的現行契約；既有硬體交握仍以上述規格為準，實機驗證狀態見 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

### 模式與硬體隔離

- Dashboard 提供「蒐集模式／自動檢測模式」選單；蒐集模式保留人工級距分類，自動檢測模式隱藏級距按鈕，僅輸出 YOLO 外觀檢測結果。
- 硬體選項只有「使用硬體／純拍攝」，不另設送料開關。純拍攝完全隔離硬體，不要求 ESP32 連線、能力回報或送料校正，也不模擬超音波事件或建立任何 motor command。
- 純拍攝由「手動觸發本輪」啟動，要求手機相機 readiness 有效且沒有處理中的工作階段；僅預覽連線不代表相機已可拍攝。
- 純拍攝按一次即自動拍攝三張，不需逐張確認。Django 在每張驗證並保存成功後才要求下一張，保留拍攝 token、上傳驗證與重複請求防護；不等待實體 station ready。三張是三次拍攝，不保證果實已轉動或具有不同視角。
- 使用硬體時包含上游送料、HC-SR04 與三站閘門交握，沿用既有 readiness、能力回報與送料校正條件。純拍攝的略過條件不得套用到實體流程。
- 模式與硬體選項只能在沒有 active capture、待處理果實、推論或 motor command，且硬體沒有待復原動作時切換；切換選項不得清除既有鎖定。

### 純拍攝 API 契約

`POST /api/collection_options/` 接收 `work_mode=collection|detection` 與 `hardware_mode=hardware|photo_only`，四種組合皆可選；既有 capture、Dataset、motor、自動運轉、推論或復原鎖存在時回 `409` 與具體 reason，不清除鎖定。預設使用硬體；開始後固定本輪 `session_work_mode` 與 `session_hardware_mode`。純拍攝與使用硬體的自動檢測會在暫存 fruit 目錄原子保存 `.capture-session.json`，供 Django 重啟後恢復工作模式、硬體選項與拍攝時間；資料提交成功或完成安全復原後清理該內部標記，不改變 metadata CSV 欄位。

`POST /api/photo_capture/` 僅接受純拍攝；相機 heartbeat 過期回 `409 camera_not_ready`，有待處理資料回 `409 active_fruit_exists`，拒絕時不建立 fruit 資料夾。成功後重用手機 capture state 與 upload API，每張原子保存成功才更新 token 並要求下一張，不等待 station ready，也不建立任何 motor command。ESP32 事件回 `200 ignored`，直接 trigger 回 `409 hardware_mode_required`；ESP32 boot 改變不影響純拍攝上傳。

三張保存後才開放人工分類；純拍攝的 `/api/classify/` 與 `/api/discard/` 必須附目前 `fruit_id`、`capture_token`，過期請求回 `409 stale_capture`。分類維持既有照片、metadata 與 counter 契約，成功回 `hardware_skipped=true`，不建立分類器復原紀錄或實體命令。分類、刪除後回 idle，不自動開始下一輪。拍攝失敗沿用既有等待與同 token 重試；逾時保留已保存照片，操作員刪除本輪後重新開始。

### 檢測、保存與結束

- 自動檢測模式在三張原圖全部保存後，將三張交給模型 Repository 的既有推論 pipeline；ROI 處理後分別執行 color、wrinkle 與 local defect。此階段不產生品質級距，也不將 YOLO 結果當成人工標籤。
- 每顆果實獨立資料夾，保存原始上傳照片、成功產生的原圖 ROI 框選圖、遮罩 ROI、灰階加 CLAHE 圖、局部瑕疵標示圖與一份 `result.json`；以照片檔名維持結果對應，不要求站點、時間或模型版本欄位。檢測產物與人工級距 Dataset 分開保存，均不納入 Git。
- Dashboard 在自動檢測模式以三張照片分組，每組依序顯示 ROI 框選、color、wrinkle、defect 四格影像與判定，共十二格；四模型有跳過、缺失或失敗時，整輪不得顯示完整成功。蒐集模式保留原照片預覽。
- 部分檢測失敗仍保留三張原圖、成功產物與每張的具體失敗原因，顯示「檢測未完整完成」後結束，不自動重拍；無法產生的裁切或標示圖不建立。保存失敗不得顯示存檔成功。
- 本階段自動檢測採單輪操作，成功或失敗後均不自動啟動下一輪或送料。純拍攝存檔完成後結束該工作階段；使用硬體時，第 3 張保存後直接執行同一套檢測與保存流程，不建立 `classify_fruit` 或 Gate 3 放行命令。
- 使用硬體的檢測完成或部分失敗後，active fruit、Gate 3 待處理狀態及人工安全復原提示都必須保留。處理暫存原圖只進入既有復原鎖，不能代表實體果實已移除；操作員依斷電程序移除果實並明確確認後，系統才可清除鎖定、切換純拍攝或建立下一次 `feed_one`。Django／ESP32 重啟與重複 terminal report 不得重跑推論或重播實體動作。

### UI 展示與操作摘要

完整影像對應、好壞文字、失敗占位、響應式排版與驗收契約以 [Issue #18 的 UI 展示契約](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/18#ui-display-contract) 為本期唯一詳細來源；[Issue #17](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/17) 建立簡潔操作配置，[Issue #19](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/19) 重用相同檢測展示。

主要區域只保留模式選擇、啟動／暫停、必要狀態與結果；詳細設定、校正、診斷及低頻資料管理置於頁面底部，預設摺疊。阻擋操作的原因與安全復原提示仍直接可見。展示重用模型實際前處理產物；本期「圓形遮罩」沿用依 ROI 長寬產生的既有橢圓遮罩，不另改推論演算法。

## 驗收

### 自動化

- Django：`C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s manage.py test fruit_app`
- Firmware：以 `esp32:esp32:esp32` 與 `ESP32Servo 3.2.1` 編譯正式 sketch。
- Repository：`git diff --check`

### 實機

1. 空軌連續 `100` 次，確認有效距離大於 `8.0 cm` 才重新待命；斷開 HC-SR04 或造成 Echo timeout 時必須回報異常，不得解讀為清空。
2. 健康網路連續 `20` 次自動採集；實體偵測至首張保存的 median ≤ `1.0 s`、p95 ≤ `1.5 s`。
3. 高速錄影確認第 2、3 站 ready 前果實已停止，且三張照片清晰。
4. 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站、重複 trigger 與 duplicate command。
5. 驗證第 3 張保存後 Gate 3 仍保持關閉、Gate 1／2 維持 Release、未分類鎖定，以及缺少 `gate3_sorter_v1` 時在 Dataset 提交前拒絕分類。
6. 驗證四個分類角度 `55°／70°／100°／115°`、分類器就位等待 `500 ms`、Gate 3 放行後落果保持 `1000 ms`、四顆馬達同時歸位與共同等待 `500 ms`，以及 report retry 不重複轉動分類器或 Gate 3；刪除未分類果實不得自動放行。
7. 以 `10` 顆作為現階段一體式送料筒裝載量，由 `1300 us` 起校正能可靠起轉且不造成雙送的最慢驅動脈波，並由 `5000 ms` 起調整最大運轉時間；最終時間由操作員依實測自行指定。測試送料成功後，只有人工確認恰好一顆才可完成校正。
8. 十顆測試果實應盡量涵蓋不同大小、形狀與蒂頭方向，並記錄各顆最大橫向直徑；樣本外形過於相近時只完成基本功能驗證，混合果形仍由後續正式實機驗收補足。
9. 每次果實都必須落在拍攝平台起點範圍內，不得彈出軌道、越過 Gate 1、勾住出口或產生可見破皮與凹傷。
10. 驗收記錄必須包含分槽盤材料、槽數、槽寬、固定方式、直徑、厚度、垂直間隙、筒壁最小間隙、舵盤與螺絲規格，以及連續運轉後是否鬆動、變形或刮傷果實。
11. 以混合尺寸、形狀與蒂頭方向的果實連續完成 `20` 顆送料與完整分類；每個 `feed_one` 都由一次有效 HC-SR04 讀值正常停止且恰好一顆，無漏送／雙送、無須人工重對送料桿。
12. 模擬最大運轉逾時、送料中 Echo timeout，以及逾時後果實才抵達；確認馬達先停止、不自動補轉，延遲果實仍完成三站流程且自動送料保持關閉。
13. 同一個 `20` 顆測試確認送料 XINHUI `60KG`、三站與分類器共四顆 MG996R 均無抖動／異音，ESP32 無 reset，兩組電源、LM25116、配電端子與線材無異常溫升；記錄兩條伺服電源軌的空載電壓、動作中最低電壓、峰值電流與送料馬達未起轉次數。任一項失敗時，校正或修正後重新累計連續 `20` 顆。
14. 模擬 ESP32 與 Django 分別在 `feed_one`、三站拍攝、Gate 3 等待分類及 `classify_fruit` terminal report 前後重新啟動，確認不會自動重播送料、閘門或分類器動作，且 Dashboard 顯示可操作的復原提示。
