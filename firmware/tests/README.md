# Firmware host harness

在 Repository 根目錄執行：

```powershell
./firmware/tests/run_host_tests.ps1
```

需要 PATH 中的 `g++`。腳本以 C++17 與 warnings-as-errors 編譯正式 Firmware 的六個 `.cpp`；Arduino、FreeRTOS、Wi-Fi、mDNS、HTTP 與伺服 adapter 由 `host/` 提供。編譯產物位於 ignored `build/`，複製來源時排除本機 `secrets.h` 與 `network.local.h`，只使用測試假設定。

測試從 `CaptureController::begin()`／`tick()`、可控制的時間與網路回應，檢查感測器讀取、actuator 輸出、命令及報告。HTTP 可以保持無回應，worker 也可暫停；assertion 失敗直接回傳非零 exit code。

先以正式診斷鎖執行 `discovery_test.cpp`，檢查動態 origin 往返、零／一／多控制端、手動備援與重啟優先探索、背景探索不遷移、版本拒絕、晚到資訊、絕對截止 rollover、探索等待不阻塞感測，以及 HTTP 2xx／惡意命令不觸發馬達。

既有控制測試共 14 個情境，涵蓋 command GET 延遲、單一 in-flight request、送料最大時間與精確邊界、HC-SR04 正常／無回音停止、計時 rollover、terminal report 重送與重複命令、兩種 trigger 的同 ID 重試、報告等待中晚到的果實、worker 建立失敗，以及三站與分類器既有 phase。另以感測器讀取耗時驗證 legacy 成功與 timeout 回應仍保留 command fast poll。所有 GPIO／servo 輸出均檢查只能由控制迴圈執行緒呼叫。

本 harness 不模擬 ESP32 排程、TLS 實際耗時或伺服位置；最壞 loop 間隔與實體停止仍依 [CAPTURE_SPEC.md](../../docs/CAPTURE_SPEC.md) 實機量測。既有控制 suite 只在 ignored `build/src/Config.h` 副本隔離診斷鎖，腳本核對絕對路徑並以 `try/finally` 還原；正式 Firmware 不因此啟用硬體。配網、lease 與 `local_network_v1` 仍由後續切片完成。
