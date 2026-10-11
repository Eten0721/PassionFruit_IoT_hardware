# Firmware host harness

在 Repository 根目錄執行：

```powershell
./firmware/tests/run_host_tests.ps1
```

需要 PATH 中的 `g++`。腳本以 C++17 與 warnings-as-errors 編譯正式 Firmware 的六個 `.cpp`；Arduino、FreeRTOS、Wi-Fi、HTTP 與伺服 adapter 由 `host/` 提供。編譯產物位於 ignored `build/`，複製來源時排除本機 `secrets.h`，只使用測試假設定。

測試從 `CaptureController::begin()`／`tick()`、可控制的時間與網路回應，檢查感測器讀取、actuator 輸出、命令及報告。HTTP 可以保持無回應，worker 也可暫停；assertion 失敗直接回傳非零 exit code。

共 14 個情境，涵蓋 command GET 延遲、單一 in-flight request、送料最大時間與精確邊界、HC-SR04 正常／無回音停止、計時 rollover、terminal report 重送與重複命令、兩種 trigger 的同 ID 重試、報告等待中晚到的果實、worker 建立失敗，以及三站與分類器既有 phase。另以感測器讀取耗時驗證 legacy 成功與 timeout 回應仍保留 command fast poll。所有 GPIO／servo 輸出均檢查只能由控制迴圈執行緒呼叫。

本 harness 不模擬 ESP32 排程、TLS 實際耗時或伺服位置；最壞 loop 間隔與實體停止仍依 [CAPTURE_SPEC.md](../../docs/CAPTURE_SPEC.md) 實機量測。它也不啟用 Issue #20 的配網、探索、lease 或新 capability。
