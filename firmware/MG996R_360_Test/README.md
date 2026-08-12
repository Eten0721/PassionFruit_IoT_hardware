# MG996R 360° 獨立測試

此 sketch 只測試上游送料用的 360° MG996R，不連接 Django、HC-SR04 或三站流程。上電後只輸出 `1500 us` 停止脈波，馬達不會自動開始旋轉。

## 接線

```text
ESP32 GPIO 23 ── MG996R 訊號線
電池盒正極 ───── MG996R 正極
電池盒負極 ──┬─ MG996R 負極
              └─ ESP32 GND
```

- MG996R 不得由 ESP32 的 `3.3 V`、`5 V` 或 `VIN` 供電。
- 測試前卸除撥片或讓輸出軸保持無負載，確認旋轉範圍內沒有人手、線材或障礙物。
- 若馬達異音、抖動、線材發熱或 ESP32 重新啟動，立即關閉電池盒。

## 使用方式

1. 使用 `esp32:esp32:esp32` 與 `ESP32Servo 3.2.1` 編譯並燒錄 `MG996R_360_Test.ino`。
2. 開啟 `115200 baud` Serial Monitor，Line ending 可用 `Newline` 或 `Both NL & CR`。
3. 先送出 `status`，確認 `running=no` 與 `stop_us=1500`。
4. 送出 `run 1300 1000`，馬達運轉 `1000 ms` 後會自動回到停止脈波。
5. 要測另一方向時送出 `run 1700 1000`。實際方向依馬達與安裝位置而定。
6. 任意時刻送出 `stop` 可立即停止。

可用命令：

```text
status
stop
stop 1500
run 1300 1000
run 1700 1000
help
```

`run` 限制脈波為 `1000～2000 us`、`10 us` 間距，運轉時間為 `100～3000 ms`。程式不接受無時間上限的連續運轉命令。

若 `1500 us` 仍會緩慢爬行，可在 `1400～1600 us` 內以 `5 us` 為間距尋找停止值，例如：

```text
stop 1495
```

找到的停止值只存在本次開機記憶體中，不會寫入 Flash。完成獨立測試後，必須重新燒錄 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`，等待 Dashboard 顯示最新 revision 已由 ESP32 套用，再執行正式的「測試送料一次」。
