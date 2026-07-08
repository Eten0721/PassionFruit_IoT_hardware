# 百香果辨識系統專案脈絡

## 1. 專案目標

本專案目標是建立百香果照片蒐集、資料集管理與後續 AI 分級辨識流程。現階段重點是先穩定「資料採集」，讓每顆百香果能在固定軌道上停留於三個拍攝站點，各拍攝 1 張照片，形成可分類與可訓練的資料。

舊版「滾動中連拍 6 張」已停止作為主要方向。新版架構以 Django 中央狀態機協調手機相機與 ESP32 三段 SG90 閘門，完成三站停止拍攝。

## 2. 目前系統架構

```text
HC-SR04 / dashboard
  -> 觸發一顆百香果拍攝工作階段

Django Server
  -> 中央狀態機
  -> 手機拍攝請求
  -> ESP32 馬達命令
  -> dataset/temp 與分類資料夾管理
  -> metadata.csv

手機相機頁 /camera/
  -> 輪詢 Django
  -> 單站拍攝 1 張
  -> 上傳 img_01.jpg / img_02.jpg / img_03.jpg

ESP32 firmware
  -> HTTPS client
  -> HC-SR04 自動觸發
  -> 輪詢 Django motor command
  -> 控制 3 顆 SG90 閘門
  -> 回報 station ready / finished
```

正式 firmware 位於：

```text
firmware/Three_Gate_Data_Collection/Three_Gate_Data_Collection.ino
```

## 3. 現行拍攝流程

手動拍攝與自動拍攝只差在開始訊號來源：

- 自動模式：HC-SR04 偵測百香果進入軌道，ESP32 回報 `hcsr04_trigger`。
- 手動模式：使用者在 dashboard 按下「手動拍攝」，Django 建立同一套開始請求。

後續流程一致：

1. Django 建立拍攝工作階段與 `start_sequence` motor command。
2. ESP32 取得 `start_sequence`，確認第 1 站可拍攝後回報 `station_1_ready`。
3. Django 設定第 1 站拍攝請求。
4. 手機輪詢 `/api/state/`，拍攝並上傳 `img_01.jpg`。
5. Django 保存成功後建立 `release_gate_1`。
6. ESP32 放行第 1 閘門，百香果到達第 2 站後回報 `station_2_ready`。
7. 第 2、3 站重複相同握手。
8. Django 收到 `img_03.jpg` 後建立 `release_gate_3`。
9. ESP32 放行第 3 閘門，等待百香果滾出後三顆馬達歸位，回報 `capture_sequence_finished`。
10. 使用者在 dashboard 確認照片並分類。

手機是否完成拍攝一律以 Django 收到並保存照片為準，不使用固定延遲猜測手機狀態。

## 4. 硬體基準

目前正式三閘門流程使用 3 顆 SG90：

- Gate 1：GPIO `18`
- Gate 2：GPIO `19`
- Gate 3：GPIO `21`
- HC-SR04 Trig：GPIO `26`
- HC-SR04 Echo：GPIO `27`

角度基準：

- `HOME_ANGLE = 0`：攔截／歸位。
- `RELEASE_ANGLE = 90`：放行。

目前 timing 基準：

- HC-SR04 觸發距離：`6.0 cm`
- HC-SR04 重新待命距離：`8.0 cm`
- `servo_settle_ms = 150`
- `fruit_settle_ms = 300`
- 第 1 站停穩測試值：`100 ms`
- 第 3 站放行後歸位前額外等待：`300 ms`

這些數值是目前實測版本，後續可依照片模糊、百香果滾動速度與機構摩擦狀況微調。

## 5. Django 頁面與 API 角色

主要頁面：

- `/camera/`：手機相機頁，負責即時影像、輪詢拍攝請求、單張拍攝與上傳。
- `/dashboard/`：電腦控制頁，負責手動觸發、流程狀態、三張照片預覽、分類與刪除。

主要 API 角色：

- `GET /api/state/`：手機與 dashboard 讀取目前拍攝狀態。
- `POST /api/capture_started/`：手機回報開始拍攝 timing。
- `POST /api/upload_images/`：手機上傳單站照片。
- `GET /api/esp32/command/?format=text`：ESP32 輪詢 Django motor command。
- `POST /api/esp32/report/`：ESP32 回報 `hcsr04_trigger`、`station_1_ready`、`station_2_ready`、`station_3_ready`、`capture_sequence_finished`。

ESP32 是 HTTPS client，Django 不主動呼叫 ESP32。

## 6. 自動觸發延遲優化

目前已完成 `hcsr04_trigger -> start_sequence` 的延遲優化：

- ESP32 預設不印完整 Django JSON response，只印 HTTP code、body length、ignored、start_sequence 與 command id 摘要。
- 若 `hcsr04_trigger` response 內含 `motor_command.command=start_sequence`，ESP32 直接執行，不多等一次 command polling。
- 若 `hcsr04_trigger` POST timeout，ESP32 會先進入 fast command polling 嘗試取得 Django 已建立的 `start_sequence`，不立即回到 idle polling。
- 重複 `hcsr04_trigger` 必須冪等，不建立新的 fruit，也不覆蓋既有 motor command。

## 7. 資料集結構

```text
dataset/
  temp/
    fruit_001/
      img_01.jpg
      img_02.jpg
      img_03.jpg

  上中等/
    fruit_001/
      img_01.jpg
      img_02.jpg
      img_03.jpg

  下等/
  加工/
  廢棄/

  metadata.csv
  counter.json
```

目前分類級距：

```text
上中等
下等
加工
廢棄
```

未來若手機鏡頭與平台保持平行且拍攝距離固定，可將 ROI 面積納入「大小」特徵，再評估把 `上中等` 拆成：

```text
上等
中等
下等
加工
廢棄
```

## 8. AI 整合方向

AI 推論與後段分類器目前暫緩，等待三站資料採集流程穩定後再整合。預期方向仍包含：

- 百香果 ROI 偵測或裁切。
- 表面瑕疵與外觀特徵判斷。
- 大小特徵，前提是拍攝平面與距離固定。
- 後段分類器，例如 XGBoost / Random Forest。
