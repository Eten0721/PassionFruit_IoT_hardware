# 目前狀態

更新日期：2026-07-08

目前版本進度：`v1.1.5, 自動觸發交握延遲優化`

## 已完成

- 資料採集流程已從「滾動中連拍 6 張」切換為「三段 SG90 閘門停止拍攝 3 張」。
- Django 三站中央狀態機已完成。
- Dashboard 手動拍攝入口已完成，且不繞過 ESP32 閘門流程。
- HC-SR04 自動觸發入口已完成，並與手動拍攝共用同一套三站流程。
- 手機 `/camera/` 已改為單站單張拍攝與上傳。
- ESP32 正式三閘門 firmware 已完成，位置為 `firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino`。
- ESP32 command polling、station report、pending retry、Wi-Fi reconnect 與 timeout 容錯已具備。
- `hcsr04_trigger` 可從 Django response 直接取得並執行 `start_sequence`，減少一次 command polling。
- 未分類 temp fruit 存在時，Django 會透過 `auto_trigger_enabled=0` 鎖住自動觸發。
- Dashboard 已可顯示三站狀態、照片預覽、分類與刪除操作。
- WebRTC 即時預覽已可運作，但不是拍攝流程的必要條件。
- `metadata.csv` 採用三站欄位：`station_01_ok`、`station_02_ok`、`station_03_ok`。

## 目前硬體與 timing

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。
- HC-SR04 觸發距離：`6.0 cm`。
- HC-SR04 重新待命距離：`8.0 cm`。
- `servo_settle_ms = 150`。
- `fruit_settle_ms = 300`。
- 第 1 站停穩：`100 ms`。
- 第 3 站放行後歸位前額外等待：`300 ms`。
- ESP32 idle command polling：`5000 ms`。
- 等待 `start_sequence` command polling：`100 ms`。
- active sequence command polling：`120 ms`。
- `hcsr04_trigger` POST timeout：`1000 ms`。
- command GET timeout：`1500 ms`。
- station report POST timeout：`5000 ms`。
- 手機 `/api/state/` polling：single in-flight，約 `200 ms`。

## 目前觀察到的瓶頸

- 第一張照片體感延遲仍需持續觀察。已知 Django timing 顯示手機端從 command 到開始拍攝約 `100 ms` 等級，手機拍攝到 upload 約 `250` 到 `330 ms`，主要瓶頸更可能在 ESP32 HTTPS POST / command GET / station_1_ready 前段。
- HTTPS 在 ESP32 上偶爾會出現 `READ_TIMEOUT`，目前已用 fast polling 與 pending retry 降低卡死風險。
- 第 2、3 站照片模糊已透過 `fruit_settle_ms = 300` 改善，仍可依實測調整到 `400` 或 `500 ms`。
- WebRTC 預覽有時可能受瀏覽器、iPhone 熱點或 ICE 狀態影響，但不應阻塞實際拍攝上傳。

## 待辦

1. 持續以 Serial Monitor 與 Django timing log 量測第一張照片延遲。
2. 依實測微調 `triggerDistanceCM`、第 1 站機構位置與 `firstStationSettleMS`。
3. 強化 ESP32 timeout 後的狀態復原與 dashboard 錯誤提示。
4. 穩定資料採集後，再整合 AI 推論與分類器。
5. 未來若手機鏡頭與平台平行且距離固定，可納入大小特徵，並評估將 `上中等` 拆分為 `上等` 與 `中等`。

## 測試

Django 測試指令：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```
