# 拍攝、檢測與分類規格

## 目標與範圍

本文涵蓋使用硬體與純拍攝兩種拍攝方式，以及蒐集模式的人工分類、自動檢測模式的 YOLO 檢測、XGBoost 分級與結果保存。本文是拍攝、檢測、分類流程、API、command ID、GPIO、角度與 timing 的唯一規格來源；目標契約不代表已完成實作，進度見 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

使用硬體的蒐集模式由 Django 中央狀態機、ESP32 以 XINHUI `60KG` 連續旋轉伺服執行上游單顆送料、HC-SR04、手機單站拍攝、三段位置型 MG996R 閘門與一顆位置型 MG996R 下置式分類器組成。每顆百香果在三個固定站點各保存一張照片；第 3 張保存後果實停留在 Gate 3，直到人工分類完成、分類器就位並執行 Gate 3 放行。實體分類完成後才允許送入下一顆。

原始上游送料整合由 GitHub Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 記錄；本文件保存 ADR-0016 接受後的目標契約，實作與實機驗收進度以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 為準。2026-08-22 的平台、分槽盤、下置式分類器與供電決策見 [ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md)。原 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的額外 SG90 擋臂已取消。

## 名詞

- Capture session：一顆果實從觸發至三張照片保存完成的工作階段，包含硬體三站與純拍攝。
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

## 裝置網路連線目標

本節為 2026-10-10 討論已接受的目標，尚未完成實作與實機驗收；架構取捨見 [ADR-0018](adr/0018-local-network-onboarding.md)。以下要求不代表目前程式已具備此行為。

### 部署與日常啟動

- 新電腦首次部署要求可連上網際網路，並自動下載、安裝所需套件；安裝工具與套件封裝另行設計。
- 部署完成後，日常啟動、ESP32 控制、手機拍攝與更換 Wi-Fi 的設定都須能在沒有網際網路的區域網路完成。
- 電腦、ESP32 與相機手機使用網路自動分配的 IP；不要求修改 Windows 固定 IPv4，也不以更換網路或電腦為由重新燒錄 ESP32。現有裝置須先更新一次支援配網的韌體。
- 電腦先由 Windows 連上 Wi-Fi 或有線網路。Django 每次啟動在 Dashboard 頂部先展開可摺疊的網路選擇區，要求操作員確認使用的已連線網路介面，即使只有一個候選；選定後才開放設備操作、開始該網路的裝置探索並顯示相機 QR Code。
- 第一版以 Demo 同一網路只有一台 Django 為範圍，ESP32 每次啟動均優先自動探索控制端；找不到時，Django 顯示目前伺服器網址，ESP32 設定頁允許手動填入作為備援。曾手動填入網址不改變下次啟動的探索優先順序；服務探索採下述本期技術基線；多主機配對不在本期範圍，發現多個有效控制端時停止自動選擇並顯示衝突。

### ESP32 配網

- ESP32 設定熱點使用使用者指定、符合至少 `8` 字元要求的密碼，與目標 Wi-Fi 密碼分開；實際值僅存於不納入 Git 的私密設定，不寫入規格。密碼已於討論定案，韌體仍須驗證設定值並明確回報熱點啟動失敗。

- 採不接 USB 的設定熱點方式；無已存 Wi-Fi 設定時自動開啟，已有設定時由正常開機後按一下板上 BOOT 進入。BOOT 腳位須依實際板型核對；按鍵需通過去抖與安全閒置檢查，不在按住 BOOT 上電或重置時配網。
- 使用電腦或相機手機連入設定熱點，由 ESP32 本機提供獨立設定頁，選擇目標 Wi-Fi 並輸入密碼；不依賴 Django Dashboard 可連線。短暫斷線先重試既有設定，不自動清除設定。
- 成功加入目標 Wi-Fi 並取得 IP 後保存設定，設定頁啟用「退出連線」按鈕；按下後才關閉設定熱點，保留對目標 Wi-Fi 的連線，不要求再次按 BOOT，也不等待 Django 上線或網際網路可用。
- 密碼錯誤或連線逾時時保留設定熱點，允許修改、重試。設定端在熱點關閉後切回目標 Wi-Fi；不保證手機或電腦會自動切回。
- 只允許安全閒置時進入配網；送料、拍攝或分類途中按 BOOT 不切換網路，須等本輪完成或人工安全復原後再操作。Dashboard 底部在停穩設定與送料校正之後，以獨立的「ESP32 網路設定」摺疊區（`dashboard.html` 的 `network-setup`）顯示配網狀態、阻擋原因與按一下 BOOT 的操作指引；ESP32 離線時顯示無法確認，不宣稱可安全配網。

### 手機加入與斷線恢復

- 相機的操作目標為：Django 顯示依所選介面目前位址產生的相機 QR Code → iPhone 使用 Chrome 開啟 → 允許相機 → 傳送影像。維持 HTTPS 與既有相機頁；不把安裝手機信任憑證預設為必要步驟。
- 電腦所選介面的 IP 改變後，Django 更新相機 QR Code，ESP32 重新探索控制端；手機由操作員重新掃碼加入，不要求跨位址自動遷移。若已觸發失聯復原鎖，仍須處理本輪並人工重新開始。
- 接受首次使用新位址可能出現憑證警告並需額外確認，以實機結果為準；目前 Chrome 成功經驗不代表新 IP、新電腦或新手機均可用。憑證策略與 Safari 支援尚未定案。
- 短暫請求失敗沿用既有有界、冪等重試；一次 HTTPS timeout 不直接判定裝置失聯。只有連續失敗超過失聯門檻後，才鎖定人工復原並跳過未完成的本輪；各裝置採下述本期門檻，仍須依實機量測驗證，不能將測試通過視為實體停止已驗收。
- 確認失聯後採安全停止、自動重連、人工確認重新開始；送料馬達由 ESP32 在本機送出已校正的停止脈波，閘門與分類器維持最後已下達的目標角度，不再下達後續放行、分類或歸位命令。位置型伺服沒有回授，此行為不保證立即停在失聯當刻的實體位置；安全鎖須阻止重連後續跑原動作。
- 人工復原沿用 Django 既有介面；重連本身不得自動恢復實體流程。停止策略不能取代既有本機感測與最大運轉時間保護，確切偵測及停止時序仍須實機驗證。
- 拍攝中斷的當前果實採跳過：由操作員刪除本輪暫存資料並依既有安全程序移除實體果實，不自動續拍剩餘站點，也不重拍此顆。資料刪除不代表果實已移除，清理與人工復原完成後才可重新開始；已提交的資料沿用本文既有不回滾契約。
- WebRTC 僅為預覽，手機 readiness 與正式照片上傳沿用本文既有契約；預覽中斷不能直接等同手機拍攝服務失聯。優雅暫停與斷線安全停止也須分開處理。

### 本期技術基線

本節為待實作的工程方案；上述已接受操作需求優先，以下數值與相容性均尚未實機驗收。行為與協定只在本文維護，GitHub 規格 Issue 引用本文並追蹤交付。

#### Django 網路選擇與探索

- 維持 Windows、單一 Django 程序與 `runsslserver 0.0.0.0:8000`；從本機 Dashboard 完成選擇。優先以 `--noreload` 執行 Demo；若保留 autoreload，只有實際 serving process 可以持有探索／監測資源，reload 後必須重新確認。測試、migration、import 不啟動網路執行緒。
- 用標準函式庫以有界 subprocess 讀取 Windows PowerShell 網路介面 JSON，列出 Up、具有效 IPv4 的 Wi-Fi／Ethernet 介面名稱、介面 ID、位址與 prefix；排除 loopback、未連線與不可用位址。保留介面 ID 與所選位址，不猜測固定熱點或以連外 DNS 判定網路。讀取失敗回 `network_inventory_failed`，無候選回 `no_connected_network`，不能偷偷使用預設介面。
- `GET /api/network/` 回候選介面、選擇狀態、`server_run_id`、`network_revision`、`server_url`、`camera_url`、`qr_url`、探索狀態與配網可用原因；`POST /api/network/` 只接受已列出的介面 ID／IPv4，重讀並確認仍可用。未選定時，設備／拍攝操作回 `409 network_selection_required`，允許狀態讀取、網路設定、既有資料檢查與必要清理；UI disabled 不取代後端檢查。
- 選定後以 `python-zeroconf` 僅在所選 IPv4 註冊 `_pf-iot._tcp.local.`，SRV port 使用實際 HTTPS port，A record 為所選位址；TXT 含 `api_version=1`、`scheme=https`、`server_run_id`、`network_revision`、`info_path=/api/network/info/`。`GET /api/network/info/` 回同一份身分、選擇狀態與目前 URL，供 ESP32 確認目標；mDNS 結果只是探索線索，不能授權實體動作。設備請求須使用所選 origin 且來源在所選 IPv4 subnet；本機 Dashboard 可由 loopback 操作。這是網路範圍限制，不是裝置身分驗證。
- 新增 `zeroconf` 與支援 SVG 的 `qrcode` 兩項必要套件，實作時核對並固定 PF／Python 相容版本。`GET /api/network/qr.svg` 的 QR Code 在 Django 本機產生且禁止快取，內容為所選 IPv4／HTTPS port 的 `/camera/`，不使用 localhost、Dashboard request host、外部 QR API 或要求手機能解析 `.local`。
- Django 每秒監測所選介面。相同介面只換 IP 時先關閉舊探索、使舊工作與網址失效，再更新 revision、URL、QR 並重新註冊；運轉中套用失聯復原鎖。介面消失或操作員選擇另一介面時，回到待確認且不接受新動作；人工切換只允許安全閒置。解除舊註冊、socket、thread 與暫時資源須有 cleanup；資源建立失敗顯示具體 reason，不偽裝成功。
- 服務探索失敗不等於 HTTPS 不可用：保留手動網址備援並明示探索不可用。Windows 須允許 Django HTTPS TCP port 與 mDNS UDP `5353`；不自動關閉防火牆。要求裝置彼此可達的 `2.4 GHz` Wi-Fi，手動網址也無法繞過 AP client isolation；企業登入入口、僅 `5 GHz` 與阻擋裝置互通的訪客網路不在支援範圍。

#### ESP32 配網與控制端識別

- 沿用 Arduino core 的 `WiFi`、`WebServer`、`Preferences`、`ESPmDNS`，不為配網另引入 WiFiManager。目標網路使用 DHCP；固定的 ESP32 設定熱點網址只用於自身配網，不是 Django 位址。設定熱點名稱帶裝置識別尾碼，頁面資源全在 Firmware，使用 `http://192.168.4.1/` 並顯示切回目標 Wi-Fi 的指引，不依賴 captive portal 自動彈出。
- BOOT 以 active-low 與 `50 ms` 去抖判定，每次按下只提出一次配網請求；開機後須先放開按鍵；本期 `esp32:esp32:esp32` 的 GPIO `0` 為實作候選，須核對實際主板後才宣稱可用。只在無送料、Capture、Gate 3 待處理、sorter、命令或未清理果實的安全閒置開 AP；無已存設定且正常初始化完成時自動開 AP。已發生帶果實的安全鎖不能用 BOOT 繞過；從未進入實體流程、僅舊 Wi-Fi 不可達的閒置裝置仍可重新配網。
- 進入設定模式先停用自動 trigger／新命令，使用 AP+STA 非阻塞嘗試新 Wi-Fi。已連線時以 heartbeat 的 `motion=setup` 請求中央保留安全閒置；中央回 `setup_allowed=1` 後才開 AP，期間拒絕新硬體操作，配網完成後以一般狀態回報解除保留。離線只在本機安全閒置且未保留中央阻擋時允許 BOOT 配網。頁面提供非阻塞 SSID 掃描、SSID／密碼、可選手動 Django HTTPS origin 與連線結果；允許直接輸入 SSID。POST 驗證 SSID 長度與 Wi-Fi 密碼格式，密碼不用 query string、回應、log 或 QR；手動 origin 只接受有效 IPv4、HTTPS 與合法 port，不接受 userinfo、任意 API path 或外網位址。
- 候選設定先留 RAM，取得目標 Wi-Fi 的有效 IP 後才以一筆帶 schema version 的 Preferences 設定記錄保存；檢查寫入結果，失敗不覆蓋上一份可用設定、不關 AP。Wi-Fi 嘗試 `15000 ms` 逾時，成功且保存完成後持續顯示結果與保留 AP，直到設定頁以 `POST /exit` 手動退出；僅在保存完成且目標 Wi-Fi 仍連線、有有效 IP 時接受，否則回 `409 setup_exit_unavailable`。退出回應後只關 AP、保留 STA，等待退出期間不恢復硬體控制。已保存後掉線回 `wifi_connection_lost`，保留 AP 與 NVS 並允許重試。錯誤回 `wifi_connect_timeout`、`wifi_configuration_invalid`、`wifi_config_save_failed` 或 `setup_ap_failed`，允許修正再試；保存不以 Django／Internet 上線為條件。
- 設定熱點密碼由 ignored `setup.local.h` 提供，範本為 `setup.example.h`；缺少有效密碼回 `setup_ap_failed`，不開放無密碼 AP。不把實際值放進 Firmware example、文件、Issue 或 log；禁止會輸出 WebServer POST 內容的 Verbose debug build。目標 Wi-Fi 密碼保存在裝置 NVS，第一版不聲稱具備抵抗實體 Flash 讀取的加密保護。一般重試不清空 NVS；更換網路不需重新燒錄。
- 每次啟動、Wi-Fi 重新加入或控制端確認失聯後，先用 mDNS 找 `_pf-iot._tcp`，再查詢網路資訊確認版本／身分與 URL。首輪探索最多 `15000 ms`，期間不啟動動作；找不到才試已存手動 origin，仍持續有界背景探索。多個有效控制端回 `multiple_controllers_found` 而不任選；已選定健康控制端不因背景發現別台而遷移。
- 指向本次探索或手動驗證的 HTTPS origin 加上既有 command／report routes，移除編譯時固定 Wi-Fi 與完整 API URL 的依賴。本期沿用既有 ESP32 `setInsecure()`，不宣稱驗證 HTTPS 伺服器憑證或抵抗區網偽冒，僅適用可信任、隔離區網；正式裝置驗證另案設計。控制端、網路 revision 或 `boot_id` 改變使舊 lease 與舊動作失效；未完成流程須人工復原，不能將 report 或 command 重送到另一控制端後繼續。

探索診斷沿用以上身分契約。`GET /api/network/info/?format=text` 將版本、scheme、selected、身分與 URL 回為逐行 `key=value`；預設 JSON 仍保留。ESP32 核對 TXT 與 info 的身分及 origin，再用同一 origin 的既有 routes；診斷模式不領取實體命令：

- `GET /api/esp32/command/?diagnostic=1` 攜帶 `boot_id`、`server_run_id`、`network_revision`、`discovery_source=mdns|manual` 與 `discovery_reason`；回逐行文字、`diagnostic_id`、`command=none`、`control_allowed=0`，不領取 motor command 或刷新正式硬體 readiness。
- `POST /api/esp32/report/` 的 `event=network_probe` 回傳相同身分與 `diagnostic_id`；相符往返完成才顯示診斷連線，最後成功 report 超過 `6000 ms` 則顯示離線。診斷 HTTP 除既有階段 timeout 外，各有 `7000 ms` 總等待上限；首輪 info 驗證另受共同的 `15000 ms` 絕對截止限制，晚到結果無效。
- 多控制端時不選定 origin、不試手動備援；只向已驗證的前兩個候選各回報 `event=network_discovery_status`、身分與 `discovery_reason=multiple_controllers_found`，供 Dashboard 顯示衝突，且不標記已連線。後續探索確認衝突消失後才重新選擇。
- 正式 Firmware 的 Wi-Fi 與手動 origin 由設定頁保存至 NVS，不再讀取 `secrets.h`；每次加入 Wi-Fi 都先探索。ignored `network.local.h` 僅作診斷模式／host adapter 備援。配網 HTTP、NVS、首輪探索與 Django HTTP 都在既有單一網路 worker；診斷模式不宣告 `local_network_v1`，正式控制模式依下列安全交握接受命令。

#### 失聯門檻與安全交握

| 項目 | 本期基線 | 判定 |
|---|---:|---|
| Django／ESP32 keepalive | `1000 ms` | 動作中也持續，不以 command polling 是否閒置判定健康 |
| ESP32 控制 lease | `6000 ms` | 自最近有效請求的送出時間起算；逾期鎖定，不以單次 timeout 判定 |
| ESP32 Wi-Fi link 持續中斷 | `1000 ms` | 本機確認後立即安全停止，不等待重新探索或 Django |
| Django ESP32 失聯 | `6000 ms` | 自最後有效 keepalive／command／report 接收時間起算；取代本期控制路徑的舊 online window |
| 相機 readiness | `5000 ms` | 沿用目前契約，過期立即禁止開始新送料／拍攝 |
| 相機確認失聯 | `10000 ms` | 曾加入且正在使用的相機持續無 ready，鎖定人工復原；未加入不產生本輪失聯事件 |
| Django 監測／ESP32 停止反應 | `1000 ms`／`100 ms` | Django 最遲下一監測週期鎖定；ESP32 在本機門檻到期後至停止輸出之延遲目標，均待實測 |

- ESP32 在既有 command／report 攜帶 `network_capability=local_network_v1`，另以 `GET /api/esp32/heartbeat/` 在動作中取得狀態，heartbeat 不領取／建立 motor command。缺少 `local_network_v1` 時，Django 回 `network_capability_missing` 並禁止硬體運轉，不因既有 feeder／sorter capability 而放行。Django 控制回應含 `server_run_id`、`network_revision`、`boot_id`、`recovery_generation`、`control_allowed` 與 `control_lease_ms`；裝置同時回報本機安全鎖、當前動作及可否配網。Django 只對相符裝置／版本的有效回應續期；`control_allowed=false` 立即鎖定。優雅暫停允許目前果實完成，不以它關閉目前動作 lease；未選網路、確認失聯與復原鎖則關閉。
- HTTP `2xx`、mDNS 回應或 Wi-Fi 有 IP 均不能單獨續 lease，須驗證回應身分與控制欄位。請求送出時間、裝置身分與 generation 一併鎖存；較早請求的延遲回應、舊 revision、舊 generation 或鎖定前回應不得解除鎖或延長期限。Django 先判斷舊 heartbeat 是否已超門檻再更新，避免第一筆重連請求掩蓋中間失聯。只有本輪操作或自動運轉曾成立才產生操作中斷鎖；啟動等待連線與安全閒置可配網須獨立表示。
- 不沿用會阻塞控制 `tick()` 的同步 HTTPS 路徑：由單一有界網路 worker 處理 HTTPS／探索，以一筆 in-flight request 與結果回交；控制迴圈唯一持有 GPIO／servo／安全狀態，網路 worker 不驅動馬達。keepalive 優先於一般 retry，每次 HTTP connect／read 使用有界 timeout；網路 worker 卡住也不能阻止控制迴圈判斷 lease、HC-SR04 或最大送料時間。實作須量測最壞 loop 間隔，確認上述停止反應目標。
- Django 獨立監測即使 Dashboard 關閉仍執行，並在開始、trigger、command、report、照片保存及分類等既有入口共同以 `STATE_LOCK` 檢查安全狀態。純拍攝只依相機與所選網路判斷，不因 ESP32 缺席鎖定；WebRTC 預覽狀態不刷新／撤銷拍攝 readiness。
- 確認失聯時：關閉 auto run／auto trigger、取消未執行命令與後續 phase、撤銷 capture token，保持已保存暫存照片供人工刪除；ESP32 先寫校正停止脈波，再取消分類器／閘門 phase，禁止 timeout 的 HomeAll。中斷命令只能回報 aborted，不回 completed，不因重連重新執行；舊 report 可被冪等確認但不能建立新拍攝、放行或送料。
- 沿用既有原子 recovery marker 的寫入方式，保存網路失聯的 fruit／command／boot／控制端身分、reason 與 recovery generation；Django 重啟恢復鎖，ESP32 在確認失聯時以 NVS 保存 interlock 與當次校正停止脈波，重啟先恢復鎖及停止值，不能自行解除或自動歸位。保存失敗保持記憶體鎖與動作停用並回 `recovery_persist_failed`，不得宣稱復原完成；實體重新啟動的保持位置／初始化限制仍須驗收。
- 人工先刪除／隔離本輪暫存資料，再依斷電程序移除果實、確認手已離開，最後於既有主操作區明確確認復原。沿用 `recovery_confirmed=true`，新增同一 command slot 的 `recover_network` 安全歸位交握，附當前 recovery generation；ESP32 只接受人工授權後的新交握，保持送料停止，依既有停穩時序歸位並回 `network_recovery_completed`。Django 在歸位／提交階段回 `recovery_allowed=true`，僅授權復原且 `control_allowed=false`。收到相符完成 ACK 後，Django 原子保存提交階段並回 `recovery_commit=true`；ESP32 先保存本機提交狀態且維持鎖，再回 `network_recovery_committed`。Django 原子保存清鎖與 generation／完成 receipt，回 `recovery_committed=true`；ESP32 收到此回應且清鎖保存成功後才可送料，Django 也只在全部開始條件成立時建立新的 `feed_one`。重複 ACK 只重送結果、不重做歸位；失敗／再次失聯／舊 ACK 維持鎖，撤銷交握後可接受新的人工授權。純拍攝以既有拍攝開始入口的明確復原確認解除相機／網路鎖，不建立此 motor command。
- 無 active fruit 不代表沒有待移除實體果實；中斷分類時已提交資料維持不回滾，仍須人工移除。檔案刪除失敗沿用既有隔離／deferred cleanup，阻擋原因顯示清楚。鎖定期間拒絕重拍、分類與切換模式等可繞過處理的入口；清理、連線診斷與相符復原 ACK 仍可用。
- 所有門檻先於本輪下一個實體輸出檢查。分類正常時序短於失聯門檻，確認失聯前已完成的動作不能倒轉或宣稱被阻止；本期保證的是確認失聯後不再下達後續動作，須在各 phase 分別驗收。

#### 手機與離線驗收前提

- 沿用 HTTPS／Chrome／相機啟動按鈕與權限流程。先驗證既有憑證在新電腦／IP 的 `isSecureContext`、`getUserMedia` 及照片上傳；若只接受警告仍無法使用，回 `camera_secure_context_required` 並記錄阻礙，不能以換 Chrome 宣稱解決憑證信任，也不擅自加入手機安裝憑證流程。
- Dashboard 與相機兩端 WebRTC 使用一致的本機 ICE 設定，不依賴外部 STUN／TURN；靜態檔、QR、探索與 signaling 都由區域網路提供。區網 ICE／瀏覽器 host candidate 仍須實測，預覽失敗必須獨立顯示並允許正式照片採集。
- 畫面分開顯示網路已選擇、ESP32 已連線、相機可拍攝與 RTC 預覽，避免用單一「已連線」判斷可運轉。

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

伺服供電使用一顆 `AC 110 V → DC 12 V／20 A` 電源供應器，並接供應兩顆 LM25116 的輸入。分支 A 初始降至 `6.0 V`，供三站與分類器四顆 MG996R，且不得超過 `6.6 V`；分支 B 降至 `8.4 V`，只供 XINHUI 送料馬達。兩顆模組的輸出正極不得互接，電源 DC 負極、兩顆模組 GND 與 ESP32 GND 必須共地。完整配線、安全與負載估算見 [`record_image/硬體接線與驗收摘要.md`](../record_image/硬體接線與驗收摘要.md)。

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

目前由人工分類按鈕提供四級分類結果，未來 AI 只可取代結果來源並進入相同分類邊界。Django 確認 `gate3_sorter_v1` 後，仍先提交 Dataset，再建立 `classify_fruit`。只有相符的 `classification_sorter_completed` 已清除 command slot，且自動運轉、相機 readiness 與全部開始條件仍成立時，Django 才建立下一個 `feed_one`。Sorter failed／timeout、Capture timeout、相機 stale 或任何 blocking error 都不得建立下一次送料。現行程式在等待下一顆且相機恢復時仍可自動繼續，其他錯誤由操作員優雅暫停並排除；此相機自動恢復行為與上述「裝置網路連線目標」有差異，尚未完成遷移。

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

切斷送料供電分支 B 的 DC 輸出只用於送料機構緊急停止。若 Dashboard 仍可連線，操作員先要求優雅暫停，讓分支 B 保持斷電直到目前 `feed_one` 到達 `feeder_max_run_ms`，並確認 Dashboard 收到 `feeder_max_run_timeout`；清料且手離開送料筒後才可恢復分支 B 供電，接著必須執行一次測試送料再恢復正式運轉。

若緊急斷電後無法連線或看不到 terminal report，送料供電分支 B 必須保持斷電。操作員清料後重新啟動 ESP32，等待 Firmware 完成初始化、先寫入 `feeder_stop_us`，並確認 Dashboard 顯示 ESP32 已重新連線且正式運轉保持停用；手離開送料筒後才可恢復分支 B 供電，之後同樣必須執行一次測試送料。

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
dataset/collection/
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

### 資料分區

新流程的蒐集資料根目錄為 `DATASET_ROOT/collection/`，檢測為 `DATASET_ROOT/detection/`；各自維護 `temp/`、四級照片目錄、`metadata.csv`、`counter.json`、`reset_state.json`、`discard_state.json` 與 `_delete_pending/`。檢測原圖、處理圖及 `result.json` 保存於檢測分區的 `results/fruit_NNN/`。檢測專用 CSV 與分級結果契約由後續實作提供。

既有 `DATASET_ROOT` 根目錄及舊 `Django_Server/detection_results/` 保留原位，只作歷史資料，不自動搬遷、升級或納入新流程的恢復與清理。Dashboard 明示目前資料區及歷史位置；開啟、編號、刪除與重置只作用於目前模式。刪除檢測果實同時處理其暫存與結果，重置檢測包含 `results/`；Windows 占用沿用隔離、延後清理及忽略標記，未能隔離的舊產物不得被覆寫或展示為新結果。

目前模式與硬體選項原子保存於 runtime 的 `dataset_options.json`，重啟先恢復模式再讀取該分區；紀錄損壞時禁止資料變更、開始拍攝與接收照片，並顯示具體原因。硬體 command sequence、timing 與復原鎖仍共用，不隨分區重置。開始後固定本輪模式；有處理中工作或安全鎖時拒絕切換。Dashboard 的資料操作附 `dataset_mode`，缺少或與目前模式不符回 `409 stale_dataset_mode`；照片 URL 帶 `work_mode`，缺少或模式不符回 `404` 並要求快取重新驗證。

## 純拍攝與自動檢測

本節包含 2026-10-13 已確認的 XGBoost、資料分離與 UI 目標，尚未完成實作；目前程式仍為 YOLO 單輪檢測。既有硬體交握與安全邊界不因接入模型而略過，實作與實機驗證狀態見 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

### 模式與硬體隔離

- Dashboard 提供「蒐集模式／自動檢測模式」選單；蒐集模式保留人工級距分類，自動檢測模式由 YOLO 特徵與 XGBoost 產生四級結果，不將預測當成人工標籤。
- 硬體選項只有「使用硬體／純拍攝」，不另設送料開關。純拍攝完全隔離硬體，不要求 ESP32 連線、能力回報或送料校正，也不模擬超音波事件或建立任何 motor command。
- 純拍攝由「手動觸發本輪」啟動，要求手機相機 readiness 有效且沒有處理中的工作階段；僅預覽連線不代表相機已可拍攝。
- 純拍攝按一次即自動拍攝三張，不需逐張確認。Django 在每張驗證並保存成功後才要求下一張，保留拍攝 token、上傳驗證與重複請求防護；不等待實體 station ready。自動檢測的純拍攝用於隔離 ESP32 的模型檢測與分類測試，前兩張各在保存成功後額外等待 `1000 ms` 才要求下一張；此等待不套用到硬體停穩 timing。三張是三次拍攝，不保證果實已轉動或具有不同視角。
- 使用硬體時包含上游送料、HC-SR04 與三站閘門交握，沿用既有 readiness、能力回報與送料校正條件。純拍攝的略過條件不得套用到實體流程。
- 模式與硬體選項只能在沒有 active capture、待處理果實、推論或 motor command，且硬體沒有待復原動作時切換；切換選項不得清除既有鎖定。

### 純拍攝 API 契約

`POST /api/collection_options/` 接收 `work_mode=collection|detection` 與 `hardware_mode=hardware|photo_only`，四種組合皆可選；既有 capture、Dataset、motor、自動運轉、推論或復原鎖存在時回 `409` 與具體 reason，不清除鎖定。預設使用硬體；開始後固定本輪 `session_work_mode` 與 `session_hardware_mode`。純拍攝與使用硬體的自動檢測會在暫存 fruit 目錄原子保存 `.capture-session.json`，供 Django 重啟後恢復工作模式、硬體選項與拍攝時間；資料提交成功或完成安全復原後清理該內部標記，不改變 metadata CSV 欄位。

`POST /api/photo_capture/` 僅接受純拍攝；相機 heartbeat 過期回 `409 camera_not_ready`，有待處理資料回 `409 active_fruit_exists`，拒絕時不建立 fruit 資料夾。成功後重用手機 capture state 與 upload API，每張原子保存成功才更新 token 並要求下一張，不等待 station ready，也不建立任何 motor command。ESP32 事件回 `200 ignored`，直接 trigger 回 `409 hardware_mode_required`；ESP32 boot 改變不影響純拍攝上傳。

三張保存後才開放人工分類；純拍攝的 `/api/classify/` 與 `/api/discard/` 必須附目前 `fruit_id`、`capture_token`，過期請求回 `409 stale_capture`。分類維持既有照片、metadata 與 counter 契約，成功回 `hardware_skipped=true`，不建立分類器復原紀錄或實體命令。分類、刪除後回 idle，不自動開始下一輪。拍攝失敗沿用既有等待與同 token 重試；逾時保留已保存照片，操作員刪除本輪後重新開始。

### 檢測與判級

- 自動檢測在三張原圖全部保存後，由 Django 本地推論處理；部署不得依賴外部模型 Repository 或資料。ROI 處理後分別執行 color、wrinkle 與 local defect，再將各張特徵送入兩階段 XGBoost。
- 沿用本次交付模型的 13 個原始特徵與 3 個衍生特徵，包括 confidence；不得套用舊研究 baseline 的 7 特徵限制。特徵名稱、順序、單位與前處理必須與訓練一致，交付模型存檔不等於分類結果 JSON。
- 每張先判斷加工／非加工，再將非加工分為其餘三級；整顆取三張預測最低級距，`0／1／2／3` 對應加工／下等／中等／上等，再轉為既有 canonical code。沿用 [ADR-0012](adr/0012-defect-ratio-ai-contract.md)：未偵測瑕疵為 `0`，必要 ROI／推論失敗為 `null` 並待覆核，不補零硬判級距。
- 部分失敗仍保留已取得原圖、成功產物與具體原因；無法產生的裁切或標示圖不建立，保存失敗不得顯示存檔成功。硬體尚未取得有效分類或資料尚未提交時，不得放行 Gate 3 或開始下一顆。
- 無法取得完整模型判級時，標記「待人工處理」並允許操作員檢查照片後人工選級，再經相同的資料提交與安全分類邊界完成分槽；保留原推論失敗原因與模型輸出，另記錄最終分類來源為人工，不將人工結果偽裝成模型成功。人工接手仍屬檢測紀錄，不搬入人工蒐集 Dataset；純拍攝的人工接手也不得建立硬體命令。
- 使用硬體的目標為「三站拍攝 → YOLO → XGBoost → 保存 → `classify_fruit` → 下一顆」。資料提交後才執行實體分類，硬體失敗不回滾結果；只有相符的 sorter 完成回報與全部安全條件成立，才允許下一次送料。純拍攝維持單輪，完成後由操作員再按開始，不建立實體命令。
- Django／ESP32 重啟、重複上傳或 terminal report 不得重播實體動作；資料刪除不能代表實體果實已移除，仍須遵守既有人工安全復原契約。

### 檢測資料與 LINE 通知

- 蒐集與檢測資料完全分離，包含暫存、最終照片、結果、counter 與清理／重置範圍；新資料使用獨立生命週期，既有資料保留原位，不自動搬遷。人工蒐集的目錄與 metadata schema 沿用上節。
- 每顆檢測果實有獨立目錄，保存原圖、成功的 ROI 框選／遮罩／灰階 CLAHE／局部瑕疵標示圖及 `result.json`。JSON 是 Dashboard 與 LINE 共用的單顆詳細結果，須分開表達推論狀態與最終分類，保留三張特徵、各張判級、整顆等級、分類來源、時間、模型版本、具體失敗原因及圖片對應；不把模型檔當成結果檔傳送。
- 檢測 CSV 每顆一列，保留唯一識別、時間、三張等級、最終等級、成功／失敗原因、模型版本及詳細 JSON 路徑，失敗輪次也留紀錄；完整特徵由 JSON 保存。原圖與所有執行產物均不納入 Git。
- LINE 對接交付程式已使用的 Messaging API，目標收件對象為模擬農夫群組的畢專群組；綁定與實際收件對象尚未驗證。只對最終等級為加工的果實通知，正文使用農夫可理解的等級、時間、果實識別與實際成功偵測的瑕疵，並附圖片；不放 grade code、模型 confidence 或程式欄位。疑似病害須保留「疑似」，不得把觀察項目宣稱為 XGBoost 判為加工的因果解釋；無可用瑕疵結果時如實表達，不捏造原因。
- 通知附圖將三個視角的瑕疵標示圖合成一張，標示照片編號與中文瑕疵名稱；某張檢測失敗時使用已取得原圖並標明「未完成檢測」，不得產生虛構標示。採用 Cloudinary 免費方案提供通知合成圖的 HTTPS 網址，只上傳通知所需合成圖，完整原圖與檢測資料留在本機；雲端圖片保留至畢專展示結束，再由操作員手動清理，不新增自動刪除。清理後舊通知附圖可能無法重新載入，本機資料不受影響。
- 人工判為加工同樣列入通知，文字明示「人工判為加工等級」，只列已有的有效瑕疵觀察；沒有可用檢測結果時如實說明。推論失敗而尚無人工判級的紀錄不發加工通知。
- 每顆加工果最終分類結果保存成功即排送一則通知，不等待分槽完成，也不宣稱已完成實體分槽。通知失敗不阻塞本地檢測與分槽，恢復網路後補送並標明原檢測時間；同一結果須去重，分槽或通知狀態更新不得觸發重送。
- 純拍攝預設不發送 LINE；操作員明確啟用後才允許測試通知，訊息開頭加「測試」。Dashboard 的操作摺疊區提供 LINE 通知啟用／關閉控制與狀態，憑證留在後端，不顯示在頁面。
- 主動關閉通知時保留並暫停尚未送出的訊息，重新開啟後續送；關閉期間新產生的結果不加入待送，之後開啟也不回溯排送。已交付 LINE 的請求不因關閉開關而撤回。

### UI 展示與操作摘要

2026-10-13 使用者已接受目前 UI 規劃，配置以下列條目為準，仍待實作；取代 [Issue #18](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/18#ui-display-contract) 的舊版配置，歷史 Issue 不作為另一份同步維護的規格。

- 「確認本次使用的網路」下方新增操作摺疊列，收納目前流程、模式選項、啟動條件、開始／重新拍攝／跳過或刪除，以及人工分類。收合時仍在該列保留開始／重新拍攝／跳過等主要操作；按鈕可用性沿用狀態與安全限制，運轉時保留優雅暫停。
- 移除獨立「三張原圖預覽」區塊；自動檢測每張原圖保存後先顯示於對應 ROI 格，待該張 ROI 產物可用才替換，未完成時明示等待／處理中，不把原圖冒充 ROI 結果。
- 蒐集模式沿用左側同一展示區，只顯示三張原圖，隱藏未執行的模型格與自動分類結果；人工級距按鈕留在操作摺疊區。自動檢測無法判級時，該區開放人工接手，且右側的待人工處理提示在收合操作區後仍須可見。
- 「四模型檢測結果」移至原手機預覽所在的左側主區，以三張照片分組、各列 ROI／color／wrinkle／defect，共十二格；成功產物逐步更新，有跳過、缺失或失敗時不得顯示整輪完整成功。
- 手機即時畫面移至右側原流程操作位置；整顆分類結果也放在檢測區右手邊，以醒目級距文字區分等待、有效結果與待覆核，不只靠顏色表示。
- 詳細設定、校正、診斷與資料管理保留在底部摺疊區。阻擋原因與安全復原提示直接可見；檢測展示重用真實前處理產物，既有橢圓 ROI 遮罩演算法不因版面調整而更改。

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
13. 同一個 `20` 顆測試確認送料 XINHUI `60KG`、三站與分類器共四顆 MG996R 均無抖動／異音，ESP32 無 reset，共用電源供應器、兩顆 LM25116、配電端子與線材無異常溫升；記錄共用 `12 V` 輸入與兩條伺服電源軌的空載電壓、動作中最低電壓、峰值電流與送料馬達未起轉次數。任一項失敗時，校正或修正後重新累計連續 `20` 顆。
14. 模擬 ESP32 與 Django 分別在 `feed_one`、三站拍攝、Gate 3 等待分類及 `classify_fruit` terminal report 前後重新啟動，確認不會自動重播送料、閘門或分類器動作，且 Dashboard 顯示可操作的復原提示。
