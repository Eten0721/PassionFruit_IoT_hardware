# ESP32 燒錄指南

[完整部署與操作說明](環境部署說明文件.md)

本頁只負責首次燒錄與 Firmware 更新。日常配網、固定設定網址及手機操作統一見 [部署說明：ESP32 Wi-Fi 設定](環境部署說明文件.md#4-esp32-首次燒錄與-wi-fi-設定)。

## 1. 安裝 Arduino 環境

1. 安裝 Arduino IDE `2.x`。
2. 在「檔案 → 偏好設定」的「額外的開發板管理員網址」加入以下穩定版來源：

   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. 在開發板管理員搜尋 `esp32`，安裝 Espressif Systems 提供的套件。
4. 在程式庫管理員安裝 `ESP32Servo 3.2.1`。

開發板安裝方式見 [Espressif 官方說明](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html)。正式板型為 `ESP32 Dev Module`，Arduino CLI FQBN 是 `esp32:esp32:esp32`；其他 ESP32 板型須另行核對。

Wi-Fi、WebServer、Preferences 與 mDNS 由 ESP32 開發板套件提供；HC-SR04 直接由 Firmware 讀取，不必另外安裝舊版 `WiFi`、`Servo` 或 `HCSR04Ultrasonic` 程式庫。

## 2. 設定 ESP32 熱點密碼

在硬體 Repository 根目錄執行；已有設定時保留原檔：

```powershell
if (-not (Test-Path firmware/Three_Gate_Data_Collection/setup.local.h)) {
    Copy-Item firmware/Three_Gate_Data_Collection/setup.example.h firmware/Three_Gate_Data_Collection/setup.local.h
}
notepad firmware/Three_Gate_Data_Collection/setup.local.h
```

將 `setupApPassword` 的空字串改為自己的 `8～63` 字元可列印 ASCII 密碼。這是手機連入 ESP32 設定熱點時使用的密碼；目標 Wi-Fi 的 SSID、密碼與 Django 備援網址在燒錄後由設定頁填入。

`setup.local.h` 已被 Git 忽略，不加入 Git；缺少有效密碼時韌體會回報 `setup_ap_failed`。正常使用不需要 `secrets.h` 或 `network.local.h`。完整設定與安全限制見 [配網規格](CAPTURE_SPEC.md#esp32-配網與控制端識別)。

## 3. 編譯與上傳

1. 先只連接 ESP32 與可傳資料的 USB 線；接回其他硬體前須依 [上電前檢查](../record_image/硬體接線與驗收摘要.md#上電前檢查) 完成安全確認。
2. 在 Arduino IDE 開啟 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。整個資料夾內的 `.h`、`.cpp` 都是正式程式的一部分，不只複製 `.ino`。
3. 選擇 `ESP32 Dev Module` 與目前實際出現的 COM 埠；COM 編號依電腦與裝置而異，不固定為 `COM7`。
4. 「Core Debug Level」使用非 `Verbose` 設定；Firmware 會拒絕可能輸出設定頁 POST 密碼的 `Verbose` build。
5. 先按「驗證」確認編譯成功，再按「上傳」，等寫入與校驗完成。
6. 如出現 `Failed to connect` 且 COM 埠保持存在，依 [官方 BOOT／EN 操作](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html#manual-bootloader) 進入下載模式後再上傳。
7. 燒錄後讓 BOOT 保持放開，正常重新啟動；序列埠監視器使用 `115200 baud`，再依完整部署說明進行 Wi-Fi 設定。

BOOT 在下載模式與日常配網有不同用途：進入設定頁須正常開機後在安全閒置時按一下 BOOT，詳見 [開啟設定熱點](環境部署說明文件.md#42-開啟設定熱點)。

## 4. 判斷燒錄結果與 USB 問題

上傳紀錄最後顯示寫入 `100%`、主程式的 `Hash of data verified`，再出現 `Hard resetting via RTS pin...`，代表映像已寫入並完成校驗；最後一行是要求板子重新啟動。這不等於韌體已成功啟動或硬體已通過驗收。

| 現象 | 判斷與操作 |
|---|---|
| 編譯失敗 | 依第一個編譯錯誤核對板型、程式庫與完整程式資料夾；尚未開始寫入。 |
| `Failed to connect` | 尚未建立燒錄連線；核對目前 COM 埠與 BOOT／EN 下載模式。 |
| 寫入或校驗中途失敗 | 保留第一個錯誤；先確認 USB 連線穩定，再依錯誤排查。 |
| 校驗完成後，監視器顯示 `Serial port not found` | 寫入已完成，但原 COM 埠當時不在；重新確認埠號與 USB 連線。 |
| 電腦反覆發出 USB 退出音效、COM 埠消失又出現 | 先只留 USB，換可傳資料的線，再換直接連電腦的插孔，每次只改一項；不以重複燒錄代替 USB 排查。 |

執行階段供電不足也可能出現在燒錄成功之後；實際原因須用開機紀錄或換線、換板對照確認，不能只憑音效判定。排查依 [Espressif 官方文件](https://docs.espressif.com/projects/esptool/en/latest/esp32/troubleshooting.html)。

## 舊資源

本機 `環境部署/` 已被 Git 忽略，其中的 `Arduino_Library/` 可能仍留有早期程式庫、Node-RED、SQL、PDF 或編譯產物；這些不是目前正式 Firmware 的必要依賴。配網與協定以 [CAPTURE_SPEC.md](CAPTURE_SPEC.md) 為準。
