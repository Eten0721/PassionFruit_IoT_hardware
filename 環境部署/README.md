# ESP32 快速燒錄準備

目前正式 firmware 位於：

```text
firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino
```

## 必要環境

- Arduino IDE `2.x`，或可使用 Arduino CLI 的等效環境。
- Espressif Systems 提供的 ESP32 board package。
- 板型：`ESP32 Dev Module`；Arduino CLI FQBN 為 `esp32:esp32:esp32`。
- Arduino library：`ESP32Servo 3.2.1`。

正式 firmware 的 Wi-Fi 功能由 ESP32 board package 提供，HC-SR04 也已直接在 firmware 內產生 Trigger pulse 並讀取 Echo，因此不需要另外安裝舊版 `WiFi`、`Servo` 或 `HCSR04Ultrasonic` library。

## 燒錄步驟

1. 安裝 ESP32 board package 與 `ESP32Servo 3.2.1`。
2. 在 repository 根目錄建立本機設定：

```powershell
Copy-Item firmware\Three_Gate_Data_Collection\secrets.example.h firmware\Three_Gate_Data_Collection\secrets.h
```

3. 編輯 `firmware/Three_Gate_Data_Collection/secrets.h`，填入：
   - `ssid`：ESP32 要連接的 Wi-Fi 名稱。
   - `password`：Wi-Fi 密碼。
   - `commandUrl`、`reportUrl`：只替換 Django 主機，保留 `secrets.example.h` 的 endpoint path。
4. 使用 Arduino IDE 開啟正式 `.ino`，選擇正確的 ESP32 開發板與序列埠。
5. 編譯並上傳 firmware；更換 Wi-Fi 或 Django 電腦 IP 後必須修改 `secrets.h` 並重新燒錄。

`secrets.h` 含有本機 Wi-Fi 密碼，已由 `.gitignore` 排除，不可加入 Git。

API 定義與 request timeout 見 [`docs/DATA_COLLECTION_SPEC.md`](../docs/DATA_COLLECTION_SPEC.md)。

## 舊資源說明

本機 `環境部署/Arduino_Library/` 可能仍留有舊版 Arduino library、Node.js ZIP、Node-RED flow、SQL、PDF 與編譯產物。這些內容屬於早期實驗資料，不是目前三站拍攝與 MG996R 正式 firmware 的必要依賴，也不會推送到 GitHub。
