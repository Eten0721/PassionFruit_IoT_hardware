# Firmware host harness

在 Repository 根目錄執行：

```powershell
./firmware/tests/run_host_tests.ps1
```

需要 PATH 中的 `g++`。腳本以 C++17 與 warnings-as-errors 編譯正式 Firmware 的 `.cpp`；Arduino、FreeRTOS、Wi-Fi、mDNS、HTTP、WebServer、NVS 與伺服 adapter 由 `host/` 提供。編譯產物位於 ignored `build/`，複製來源時排除本機私密設定，只產生測試用 `network.local.h` 與 `setup.local.h`。目標 Wi-Fi 假設定由 NVS adapter 提供。

測試從 `CaptureController::begin()`／`tick()`、可控制的時間與網路回應，檢查感測器讀取、actuator 輸出、命令及報告。HTTP 可以保持無回應，worker 也可暫停；assertion 失敗直接回傳非零 exit code。

先以診斷模式執行 `discovery_test.cpp`，檢查動態 origin 往返、零／一／多控制端、手動備援與重啟優先探索、背景探索不遷移、版本拒絕、晚到資訊、絕對截止 rollover、探索等待不阻塞感測，以及 HTTP 2xx／惡意命令不觸發馬達。

另以正式控制模式執行 `network_safety_test.cpp`，覆蓋 lease 送出時間／rollover、Wi-Fi 中斷、舊／晚到回應、NVS 保存與校正停止值、各分類 phase 停止、慢心跳不阻塞 report，以及撤銷 ACK 後的新人工交握。

`wifi_setup_test.cpp` 檢查 BOOT 短按去抖與安全門檻、未存設定自動 AP、非阻塞掃描與直接輸入、停止掃描失敗、取得 IP 後保存 NVS、等待手動退出、掉線阻止退出與關 AP 保留 STA、重啟優先探索、連線／保存失敗重試及時間 rollover。

`network_http_test.cpp` 以管線交換實際 Django Client 回應，無控制器 source 修改。先執行上述編譯，再依 Repository 的 PF 指令執行 Django `fruit_app` 測試；`test_network_firmware.py` 串接 AP 配網、保存、關 AP、探索、校正、正式送料、首站保存、失聯／換 IP、清理、人工確認、雙端提交與新送料，缺少 binary 時明確 skip。

既有控制測試共 14 個情境，涵蓋 command GET 延遲、單一 in-flight request、送料最大時間與精確邊界、HC-SR04 正常／無回音停止、計時 rollover、terminal report 重送與重複命令、兩種 trigger 的同 ID 重試、報告等待中晚到的果實、worker 建立失敗，以及三站與分類器既有 phase。另以感測器讀取耗時驗證 legacy 成功與 timeout 回應仍保留 command fast poll。所有 GPIO／servo 輸出均檢查只能由控制迴圈執行緒呼叫。

本 harness 不模擬 ESP32 排程、TLS 實際耗時或伺服位置；最壞 loop 間隔、實體停止與配網驗收仍依 [CAPTURE_SPEC.md](../../docs/CAPTURE_SPEC.md) 實機量測。既有控制 suite 在 ignored `build/src/CaptureController.cpp` 副本隔離新控制交握、lease 與 heartbeat，保留歷史感測／動作回歸；不能將它當成失聯安全驗證。正式安全與跨端 suite 不隔離安全檢查；腳本以 `try/finally` 還原副本。
