# 決策紀錄

## 2026-07-15：模型 Repository 放入硬體工作區但維持獨立 Git

### 決策

模型 repository 本機移至 `D:\PassionFruit_IoT_hardware\external\ps-quality-detection-system\`，保留自己的 `.git`、GitHub 遠端與 Git LFS。父層硬體 repository 以 `.gitignore` 排除完整模型目錄，兩邊仍各自 commit 與 push，不採用 Git submodule，也不合併歷史。

原本只作為導引的舊 README 已移除，改由 [`external/README.md`](../external/README.md) 記錄 clone、版本管理、dataset 與 Django 整合邊界。

### 原因

- 讓 Django、firmware 與模型程式位於同一個本機工作區，降低跨工作區閱讀與整合的不便。
- 維持團隊模型 repository 的獨立權限、版本、Git LFS 與發布流程。
- 避免父層 Git 誤收模型權重、訓練輸出或 child repository 歷史。

### 影響

- 未來檢測層成為 Python package 後，可使用 `python -m pip install -e .\external\ps-quality-detection-system` 連接開發版本。
- Django 只透過 adapter 取得 ROI、顏色、皺褶、局部瑕疵、confidence 與模型版本；AI 仍不得直接控制 GPIO。

## 2026-07-14：模型訓練與硬體系統維持雙 Repository

### 決策

硬體與資料採集系統繼續由 `Eten0721/PassionFruit_IoT_hardware` 維護；YOLO 模型訓練、驗證與 Multi-stage Pipeline 改由團隊 repository `fcu-passionfruit-project/ps-quality-detection-system` 作為唯一來源。當時先將原本內嵌的獨立 Git workspace 移出硬體 repository；目前本機配置已由上方 `2026-07-15` 決策取代。

原始照片與 dataset 不進任何 Git repository，本機集中於 `D:\passion-fruit-datasets\` 或團隊共用儲存空間。模型 repository 只使用 Git LFS 管理通過驗收的正式權重，排除 `runs/`、`last.pt`、歷史 checkpoint 與圖片。

### 原因

- 硬體、Django 與 firmware 的版本週期相對穩定；模型訓練會由多位組員頻繁產生實驗、權重與準確率結果，不適合混在同一份 Git 歷史。
- 完整模型 workspace 原本已是獨立 Git repository，繼續內嵌會造成 nested repository、權限與 staging 混淆。
- Dataset 約數 GB，Git 不適合保存大量原始影像；模型版本必須透過 manifest、dataset ID、驗證結果與正式權重交接。

### 影響

- 目前不加入 Git submodule；開發期間兩個 repository 以同層 workspace 或明確路徑連接。
- 模型 repository 未來提供可匯入的 Python package 與結構化檢測結果；硬體 repository 只新增薄 Django adapter，不複製推論實作。
- AI 不得直接控制 GPIO。檢測結果仍須經決策層映射為 `high_medium`、`low`、`processing` 或 `discard`，再使用既有 `classify_fruit`、command ID 與 sorter 安全規則。
- 本決策將先前「所有模型權重不納入 Git」細化為：硬體 repository 不納入任何權重；模型 repository 僅以 Git LFS 納入已驗收的正式權重。

## 2026-07-14：正式 ESP32 部署只列入目前必要依賴

### 決策

正式三站與 MG996R firmware 的部署文件以根目錄 `README.md` 與 `Necessary_library/README.md` 為準。必要環境為 Espressif Systems 的 ESP32 board package、`esp32:esp32:esp32` 板型及 `ESP32Servo 3.2.1`；Wi-Fi 由 ESP32 core 提供，HC-SR04 由 firmware 直接控制。

### 原因

- 本機舊版 `WiFi`、`Servo` 與 `HCSR04Ultrasonic` library 不符合目前正式 firmware 的實際依賴。
- Node.js ZIP、Node-RED flow、SQL、PDF 與舊編譯產物屬於早期實驗資源，若一併上傳會增加 repository 體積並混淆部署流程。
- 更換 Wi-Fi 或 Django 電腦 IP 時，只需要更新 `secrets.h` 並重新燒錄 ESP32，不需要恢復舊 Node-RED／MySQL 架構。

### 影響

- Git 只追蹤 `Necessary_library/README.md`，其餘舊資源繼續保留在本機並由 `.gitignore` 排除。
- `secrets.h`、`.env`、dataset 與 runtime JSON 仍不得提交。
- Windows Django 快速部署維持 Python `3.10.20`、根目錄 `.env`、migration 與 `runsslserver 0.0.0.0:8000`。

## 2026-07-14：未來 AI 決策層沿用既有 classify_fruit 邊界

### 決策

`decision_layer/` 目前只建立整合說明，不啟用自動推論。未來 XGBoost、Random Forest 或其他模型必須將結果映射為 `high_medium`、`low`、`processing` 或 `discard`，再交由 Django 建立既有 `classify_fruit` 命令。

### 原因

- 照片搬移、metadata、counter、單一 motor command slot、command ID 與 sorter timeout 已形成完整的一致性邊界。
- 讓模型直接控制 GPIO、角度或 PWM，會繞過已驗證的資料分類與硬體互斥規則。
- 現階段仍以三站資料採集與人工分類的實機穩定性優先。

### 影響

- AI 決策層不可直接呼叫 ESP32 或控制 MG996R。
- 模型失敗、低信心或無法推論時，應回傳結構化結果並保留人工覆核，不得猜測分類。
- 模型權重、dataset 與訓練輸出不納入 Git；正式整合前另行定義模型版本與信心門檻。

## 2026-07-13：分類按鈕延遲以可調 idle command polling 控制

### 決策

將 ESP32 一般 idle command polling 從固定 `5000 ms` 改為 `capture_timing.json` 中的 `idle_command_poll_interval_ms`，預設 `250 ms`，可在 Dashboard 以 `50 ms` 步進調整於 `100～5000 ms`。等待 `start_sequence` 的 `100 ms` 與等待 `release_gate` 的 `50 ms` 維持不變。

### 原因

- Django 無法主動推送命令到 ESP32；人工分類命令的主要延遲是 idle polling，而非 MG996R 動作時序。
- 沿用既有 timing revision 與 ACK，可避免增加第二套 runtime 設定協定。
- 較短間隔能降低按鈕到作動延遲，但必須讓使用者自行權衡 HTTPS 請求頻率。

### 影響

- 舊版四欄 timing JSON 會保留原值、補入 `250 ms` 並提高 revision，確保 ESP32 重新套用。
- `classify_fruit` 仍不攜帶 GPIO、角度、PWM、`station_index` 或 timing 欄位。
- MG996R 的保持、歸位與 timeout，以及三個 SG90 的 phase 均不改變。

## 2026-07-12：人工資料分類成功後才驅動 MG996R

### 決策

三站拍攝、Gate 3 放行、三閘門歸位與 `capture_sequence_finished` 維持原流程。使用者之後在 Dashboard 完成中文資料分類，Django 才以同一個 motor command slot 下發 `classify_fruit`，由 GPIO `25` 的 MG996R 執行實體分類。

分類命令只傳固定 ASCII code，不傳 GPIO、角度或 PWM。MG996R 無位置回授，completed 僅代表控制時序完成。硬體失敗、離線或 timeout 均不回滾資料夾、metadata 或 counter。

### 原因

- 資料一致性優先，硬體分類不得成為照片分類 transaction 的前置條件。
- MG996R 不得改變已驗證的第三站安全交握。
- 單一命令槽必須互斥，避免 sorter 與下一顆拍攝互相覆蓋。

### 影響

- Sorter 使用獨立的 `idle／pending／running／completed／failed／timeout` 狀態。
- Pending／running 期間停用 HC-SR04、自動／手動拍攝、recapture、第二筆分類與 dataset reset。
- Command ID 原子持久化，dataset reset 不歸零。
- Firmware 以非阻塞 `ClassifierController` 控制 GPIO `25`，錯誤復原不呼叫 `GateController`。

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
- 第 3 站 `release_gate_3` 後，ESP32 先等待 `finalGateReturnDelayMS`，再讓三顆馬達歸位；當時初始值為 `200 ms`，目前實測推薦值已調整為 `300 ms`。

## 2026-07-10：站點停穩時間改由 Dashboard 管理

### 決策

第 1 站、伺服放行後、到下一站與最終歸位的初始推薦校正值皆為 `200 ms`。Django 將完整 timing profile 與 revision 持久化到固定單一的 `runtime_config/capture_timing.json`，每次僅原子覆寫最新版本；ESP32 僅在 idle、沒有流程或 pending report 且 Gate home 時套用，然後回報 `timing_config_applied`。這組初始值已於 2026-07-11 依實機校正結果更新。

### 原因

首站捷徑縮短網路交握後，果實可能在到站前就被手機拍攝。固定編譯時間不利於現場校正，故改由 Dashboard 以安全範圍和 `50 ms` 步進管理。

### 影響

- 每顆 fruit 開始時 snapshot 四項時間，過程中不會混用新版設定。
- Dashboard 僅在 idle 時允許更新；未收到 ESP32 ACK 時顯示等待套用，避免誤以為首站捷徑已使用新值。
- 若仍模糊，以 `50 ms` 為單位優先增加第 1 站或到下一站停穩時間。

## 2026-07-08：ESP32 預設不印完整 JSON response

### 決策

ESP32 Serial Monitor 預設只印 HTTP code、request 耗時、body length、ignored 與是否包含 `start_sequence`。完整 response 需將 `verboseHttpResponseLog` 改為 `true` 才會輸出；實際處理命令時另印 `Command #...`。

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

## 2026-07-10：自動模式採用受守門的首站捷徑

### 決策

自動模式預設啟用 `FirmwareConfig::kEnableAutoStation1FastPath`。當且僅當 firmware 為 idle、沒有進行中的 sequence、三個 Gate 的角度追蹤皆為 home，且已完成 `firstStationSettleMS`，ESP32 才能以 `trigger_id` 與 `station_index=1` 回報 `hcsr04_station_1_ready`，讓 Django 直接開放第 1 站拍攝。Gate 1 攔住果實是實機部署與機構驗證前提，不是此版本的硬體回授訊號。

### 原因

既有流程在果實已安全停在第 1 站後，仍需經過 `hcsr04_trigger -> start_sequence -> station_1_ready` 的額外 HTTPS 往返，造成第一張照片前可感知的等待。

### 影響

- 捷徑只縮短第 1 站前置握手；`station_N_ready -> Django 保存照片成功 -> release_gate_N` 的安全規則不變。
- 前置條件不符、功能被關閉或收到可回退的協定拒絕時，ESP32 必須走舊流程，不能假設捷徑成功。收到 `ignored` 時停止本次 trigger，等待感測器重新待命。
- 首站捷徑 timeout retry 使用同一個 `trigger_id`，避免 Django 已成功建立工作階段時產生重複 fruit。legacy `hcsr04_trigger` 的重複防護仍由 Django active fruit 與既有 motor command 狀態負責。

## 2026-07-10：感測、Wi-Fi 與 TLS 皆採有界等待

### 決策

HC-SR04 以直接 Trigger pulse 與 `12000 us` Echo timeout 讀值；Wi-Fi、伺服 phase 與 retry 使用 deadline 驅動。完整 `sensor_read_us` 的 firmware warning 門檻約為 `12100 us`。HTTPS 的 connect 與 read 均使用對應 request deadline，正常時重用同 origin HTTP/1.1 TLS 連線，連線失效時再關閉重建。

### 原因

首張延遲不能被無回波、同步 Wi-Fi 等待或每次 request 重做 TLS handshake 放大。

### 影響

- 感測、Wi-Fi 與伺服 phase 不再以長時間 `delay()` 卡住主迴圈；HTTPS request 仍是有 connect／read deadline 的同步操作。
- Wi-Fi 斷線、timeout 或 `Connection: close` 都必須使 client 回到可安全重建的狀態。
- 序列 log 記錄 phase 與 request 耗時，搭配 Django trace 找出延遲來源。

## 2026-07-10：手機拍攝 request 使用精簡 state 與非阻塞 telemetry

### 決策

手機只輪詢 `/api/camera/state/`，idle 為 `250 ms`、有 fruit／capture request 時為 `50 ms`、實際擷取或上傳期間為 `250 ms`，state request 使用 `AbortController` timeout。頁面進入背景時 polling 會停止，回到前景才重啟。`capture_started` 改為 best-effort telemetry，不得阻塞 canvas 擷取或照片上傳。

### 原因

完整 dashboard state 會增加手機端解析與傳輸負擔；等待 telemetry response 會直接拉長首張照片時間，卻不是放行閘門的依據。

### 影響

- Django 仍只以照片原子保存成功作為放行下一閘門的條件。
- `capture_meta` 保留既有欄位，另加入 request、影格、blob 與 upload 的 client timing，供 transition trace 對照。
- Dashboard 顯示最近 transition trace；舊版 state 未提供 trace 時不影響控制功能。

## 2026-07-11：高頻狀態路徑與等待命令輪詢最佳化

### 決策

Django 的 dataset 初始化、metadata schema 檢查與 timing 載入改為每個 dataset root 只執行一次；`/api/camera/state/` 不再進行檔案系統同步。分類、刪除與 reset 以 operation token 保護，慢速檔案操作移出全域狀態鎖。ESP32 report 回傳精簡協定 payload。

ESP32 只在 idle、等待 `start_sequence`、等待 `release_gate` 時輪詢 command。等待 release 的 interval 為 `50 ms`，伺服移動、果實停穩與 report pending 階段不輪詢。

### 原因

原本相機 active polling 每 `75 ms` 會反覆執行 dataset 維護，Windows 檔案重試也可能在全域鎖內累積約 `1.2 秒`。另外 active sequence 的 `120 ms` polling 會在硬體仍移動時取得不可能更新的命令。這些等待都不屬於機構安全停穩時間，可以在不改變三站交握的前提下降低。

### 影響

- 手機 active polling 改為 `50 ms`，但仍維持 single in-flight 與背景暫停規則。
- 前景檔案重試限制約 `100 ms`；失敗時沿用 upload retry、`409` 或 deferred cleanup。
- 當時四項機構 timing 預設仍為 `200 ms`，不得因本次軟體最佳化直接縮短；後續實機校正不受此限制。

## 2026-07-10：HC-SR04 Echo 必須降壓後接 ESP32

### 決策

HC-SR04 的 `5 V` Echo 訊號必須經過分壓或邏輯電平轉換，正常目標為 ESP32 GPIO 端 ≤ `3.3 V`、絕不可超過 `3.6 V` 後才可接到 GPIO `27`。

### 原因

ESP32 GPIO 並非 `5 V` 耐受；直接接 Echo 會造成不穩定讀值或硬體損傷風險。

### 影響

- 刷入新版 firmware 前需依硬體 note 檢查共地、分壓方向與量測電壓。
- 這是安全前置條件，不可用韌體 timeout 或軟體容錯取代。

## 2026-07-10：區分伺服器 trace 與實體首張延遲

### 決策

Django transition trace 的首站起點定義為伺服器收到 `hcsr04_station_1_ready`，只用於分析伺服器端鏈路。HC-SR04 實體偵測到照片保存的總延遲，必須以同次 firmware Serial timing 與高速錄影／外部同步量測驗收。

### 原因

firmware 目前只在 Serial 輸出 `hcsr04_trigger_detected` 的裝置端時間，未把實體偵測時間上送到 Django；不能把 server receipt 誤當成感測瞬間。

### 影響

- `20` 次首張延遲驗收保留為實機待辦，目標為中位數 ≤ `1.0 s`、`p95` ≤ `1.5 s`。
- Dashboard trace 仍可用來定位 Django 收到事件後的狀態轉移與照片原子保存時間。

## 2026-07-10：跳過資料後一律回到 idle

### 決策

`POST /api/discard/` 成功後以 `_clear_active_state(..., status='idle')` 作為唯一流程清理入口，再記錄 `fruit_discarded` trace。

### 原因

手動散落清理欄位會遺漏等待計時器或 capture token，造成 temp 已刪除但 Django 仍顯示舊流程狀態、阻擋下一顆 fruit。

### 影響

- 刪除成功後自動與手動觸發立即重新可用。
- 檔案無法安全刪除時維持結構化失敗回應，不可錯誤宣告為 idle。

## 2026-07-10：三站縮圖採直式比例並採原生彈窗預覽

### 決策

Dashboard 縮圖以可鍵盤操作的 button 顯示固定直式 `3:4` cover；點擊後使用原生 `<dialog>` 以 `object-fit: contain` 顯示原始比例照片。

### 原因

固定縮圖能讓三站比較一致，而彈窗仍保留完整照片內容以判斷模糊與構圖。

### 影響

- 使用者可按 `Esc`、關閉按鈕或點擊彈窗背景離開預覽。
- 刪除或分類前會先關閉彈窗並釋放縮圖檔案 handle，避免 Windows 檔案占用。

## 2026-07-10：刪除受阻時隔離資料並保持採集可用

### 決策

跳過／刪除先嘗試實體刪除。若 Windows 檔案占用導致刪除失敗，先搬移至 `_delete_pending`；若搬移也失敗，將原 temp 路徑記錄在 `discard_state.json`，跳過該 fruit ID 並定期重試清理。

### 原因

未分類 temp fruit 會鎖住自動觸發。不能因瀏覽器、檔案總管或其他程式暫時占用照片，就讓 ESP32 長期收到 `server_status=uploaded` 與 `auto_trigger_enabled=0`。

### 影響

- `/api/discard/` 會回傳 `deleted`、`quarantined` 或 `deferred_cleanup`。
- 待清理資料不會被 Django 恢復成 active fruit，也不會阻擋下一顆；搬移也失敗時會改用下一個 fruit ID。
- WebRTC 無影像軌時收合 `16:9` 預覽區，避免 Dashboard 留下空白。

## 2026-07-11：停穩設定使用固定單一 runtime 設定檔

### 決策

當時四項推薦校正值固定為 `200 ms`。最後一次由 Dashboard 套用的完整 timing profile 與 revision 持久化於 `Django_Server/runtime_config/capture_timing.json`，每次只以原子覆寫更新同一份檔案；舊版 `dataset/capture_timing.json` 首次升級時遷移後移除。推薦值後續依實機校正結果更新，持久化方式不變。

### 原因

校正值必須跨 Django 重啟保留，但 runtime 設定不應混入 dataset，也不應累積多份版本檔案。

### 影響

- `Config.h` 與 Django 推薦值提供首次刷入／設定檔遺失時的 fallback；目前數值依序為 `300 / 200 / 350 / 300 ms`。
- Dashboard 日後修改仍跨重啟保留最後一版數值，並維持 revision ACK 與每顆 fruit 的 timing snapshot。
- runtime 設定目錄不納入 Git；不影響照片、metadata 或分類資料。

## 2026-07-11：依實機結果更新停穩推薦值

### 決策

四項初始推薦值更新為：第 1 站停穩 `300 ms`、伺服穩定 `200 ms`、到站停穩 `350 ms`、最終歸位延遲 `300 ms`。Django、Dashboard fallback 與 ESP32 firmware fallback 使用相同數值；runtime revision `16` 已套用這組設定。

### 原因

實機測試顯示，第 1 站與後續到站需要較長的停穩時間，最終放行也需要保留足夠時間讓果實離開閘門範圍。伺服本身使用 `200 ms` 已可穩定完成動作，因此維持不變。

### 影響

- 新環境、首次刷入或 runtime 設定遺失時，會使用 `300 / 200 / 350 / 300 ms` 作為安全起點。
- Dashboard 仍可在 idle 時以原有範圍與步進調整，revision ACK 與每顆 fruit 的 timing snapshot 規則不變。
- 延遲只用於硬體動作與果實停穩，不取代 Django 確認照片保存成功的交握規則。
