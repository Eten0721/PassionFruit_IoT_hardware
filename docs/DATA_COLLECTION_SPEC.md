## 照片採集系統規格

目前的短期開發目標是完成 Django-based 照片採集系統。新的主要架構是「Django 中央狀態機協調手機與 ESP32，完成三段 SG90 閘門停止拍攝 3 張」，不再以「滾動中連拍 6 張」作為資料採集主流程。

此系統不能只實作純 Django 手動拍照。因為實體軌道上存在三段 SG90 閘門，如果沒有 ESP32 控制攔截與放行，百香果會停在閘門前，無法完成三站拍攝。

Django 是流程狀態中心。手機相機頁透過輪詢取得拍攝請求；ESP32 也以 HTTPS client 身分輪詢 Django 取得馬達命令。Django 不主動呼叫 ESP32，避免要求 ESP32 架設 HTTPS API server。

## 專有名詞

- `拍攝工作階段`：一顆百香果從開始到三張照片完成的完整流程。
- `拍攝站點`：第 1、2、3 張照片對應的位置。
- `拍攝請求`：Django 狀態中通知手機拍攝某一站照片的命令。
- `照片上傳完成`：Django 接收到該站照片並保存成功。
- `馬達命令`：ESP32 輪詢 Django 取得的放行或結束命令，例如 `release_gate_1`。
- `站點就緒`：ESP32 回報百香果已在某站可拍攝，例如 `station_1_ready`。

## 資料採集原則

- 每次只處理一顆百香果。
- HC-SR04 與 dashboard 手動拍攝按鈕都只是「開始一顆百香果流程」的觸發來源。
- 自動模式由 HC-SR04 觸發 ESP32，ESP32 回報 Django 開始拍攝工作階段。
- 手動模式由使用者在 dashboard 按下「手動拍攝」，Django 建立開始請求，ESP32 輪詢 Django 後啟動同一套三站流程。
- 三個 SG90 閘門依序攔截百香果，讓百香果在三個拍攝站點短暫停留。
- 每個拍攝站點只拍 1 張照片，預設每顆百香果共 3 張。
- 三個 SG90 閘門以 `45` 度作為攔截預設角度，以 `0` 度作為放行角度。
- 手機拍攝完成不靠延遲判斷，而靠 Django 收到該站照片並保存成功。
- `servo_settle_ms` 是 SG90 轉動後的機械穩定時間，建議先設為可調參數，初始可用 `100` 到 `300` ms。
- `fruit_settle_ms` 是百香果到達下一站後的停穩時間，建議先設為可調參數，初始可用 `100` 到 `300` ms；若實測仍模糊可提高。
- 照片先暫存在 `dataset/temp/fruit_XXX/`，人工確認後才移到正式分類資料夾。
- 每次成功分類後都必須寫入 `metadata.csv`。

## 資料夾結構

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
    fruit_002/

  加工/
    fruit_003/

  廢棄/
    fruit_004/

  metadata.csv
  counter.json
```

## 資料夾用途

`dataset/` 是整個照片資料集根目錄。

`dataset/temp/` 是暫存區。每次拍攝後，照片先存入這裡，等待人工確認與分類。

`dataset/temp/fruit_XXX/` 代表尚未正式分類的一顆百香果樣本。每個樣本預設包含 3 張照片，分別對應第一、第二、第三拍攝站點。

`dataset/上中等/` 存放人工判定為上中等級的百香果樣本。

`dataset/下等/` 存放人工判定為下等級的百香果樣本。

`dataset/加工/` 存放外觀不適合直接作為高品質商品，但仍可作為加工用途的百香果樣本。

`dataset/廢棄/` 存放不適合食用、販售或加工的百香果樣本。

`metadata.csv` 記錄每一筆樣本的 fruit_id、分類、拍攝時間、路徑、三個站點是否拍攝成功與備註。

`counter.json` 記錄下一個要使用的 fruit_id，避免刪除、跳過、重拍後造成編號混亂。

## Metadata 欄位

```csv
fruit_id,label,capture_time,path,capture_count,station_01_ok,station_02_ok,station_03_ok,note
fruit_001,上中等,2026-07-06 14:32:10,上中等/fruit_001,3,true,true,true,null
```

欄位說明：

- `fruit_id`：樣本編號，例如 `fruit_001`。
- `label`：人工分類結果，限定為 `上中等`、`下等`、`加工`、`廢棄`。
- `capture_time`：完成拍攝或分類時的時間。
- `path`：正式分類後的資料夾路徑。
- `capture_count`：成功保存的照片數，正常情況為 `3`。
- `station_01_ok`、`station_02_ok`、`station_03_ok`：三個站點是否成功拍攝。
- `note`：選填欄位，不應阻礙現場快速採集。

## 裝置分工

手機：

- 開啟 Django 提供的 `/camera/` 頁面。
- 顯示滿版鏡頭畫面。
- 接收 Django 拍攝命令。
- 每個站點拍攝 1 張照片並上傳給 Django。

電腦 Django：

- 提供 `/camera/` 手機拍照頁。
- 提供 `/dashboard/` 電腦 GUI 頁。
- 作為拍攝工作階段的中央狀態機。
- 提供手機輪詢狀態，讓手機得知目前是否有某站 `capture_requested`。
- 提供 ESP32 輪詢狀態，讓 ESP32 取得目前 `motor_command`。
- 接收 ESP32 回報的 `station_1_ready`、`station_2_ready`、`station_3_ready` 與 `capture_sequence_finished`。
- 手動拍攝時建立開始請求，等待 ESP32 輪詢後啟動三站流程，而不是直接繞過閘門拍照。
- 管理 fruit_id、`temp/`、分類資料夾、`metadata.csv`。
- 在 dashboard 顯示三張照片預覽與分類控制。
- 未來負責 AI 推論與決策。

ESP32：

- 讀取 HC-SR04。
- 偵測百香果進入拍攝區。
- 控制三段 SG90 閘門依序攔截與放行百香果。
- 自動模式下，HC-SR04 觸發後回報 Django 開始拍攝工作階段。
- 手動模式下，輪詢 Django 取得開始請求後啟動同一套三站流程。
- 輪詢 Django 取得馬達命令，例如 `release_gate_1`。
- 回報 Django 站點就緒與流程完成狀態。
- 未來接收 Django 分類結果並控制分類機構。

## 觸發來源

自動模式：

- 百香果滾入拍攝軌道。
- HC-SR04 偵測到百香果。
- ESP32 回報 Django 開始拍攝工作階段。
- ESP32 進入同一套三段 SG90 閘門拍攝流程。

手動模式：

- 使用者將百香果放入或準備進入拍攝軌道。
- 使用者在 dashboard 按下「手動拍攝」。
- Django 建立開始請求。
- ESP32 輪詢 Django 後取得開始請求。
- ESP32 進入同一套三段 SG90 閘門拍攝流程。

兩種模式只差在開始訊號來源，後續三站拍攝、閘門角度、照片命名與資料儲存流程都必須一致。

## 三站握手資料採集流程

1. Django 建立 `dataset/temp/fruit_XXX/`，或確認目前樣本資料夾已建立。
2. ESP32 確認第 1 閘門以 `45` 度攔截，並回報 Django `station_1_ready`。
3. Django 設定第 1 站拍攝請求。
4. 手機輪詢 Django 得知需要拍攝第 1 站。
5. 手機拍攝並上傳 `img_01.jpg`，上傳資料包含 `fruit_id`、`capture_token`、`station_index`。
6. Django 保存 `img_01.jpg` 成功後，設定馬達命令 `release_gate_1`。
7. ESP32 輪詢 Django 取得 `release_gate_1`，將第 1 閘門轉到 `0` 度放行。
8. ESP32 等待 `servo_settle_ms` 與 `fruit_settle_ms`，確認百香果到達第 2 閘門並回報 `station_2_ready`。
9. Django 設定第 2 站拍攝請求。
10. 手機輪詢 Django 後拍攝並上傳 `img_02.jpg`。
11. Django 保存 `img_02.jpg` 成功後，設定馬達命令 `release_gate_2`。
12. ESP32 輪詢 Django 取得 `release_gate_2`，將第 2 閘門轉到 `0` 度放行。
13. ESP32 等待 `servo_settle_ms` 與 `fruit_settle_ms`，確認百香果到達第 3 閘門並回報 `station_3_ready`。
14. Django 設定第 3 站拍攝請求。
15. 手機輪詢 Django 後拍攝並上傳 `img_03.jpg`。
16. Django 保存 `img_03.jpg` 成功後，設定馬達命令 `release_gate_3`。
17. ESP32 輪詢 Django 取得 `release_gate_3`，將第 3 閘門轉到 `0` 度放行，讓百香果離開拍攝區。
18. ESP32 回報 Django `capture_sequence_finished`。
19. 電腦 dashboard 顯示三張照片預覽。
20. 使用者確認照片後按下分類按鈕。
21. Django 將整個 `fruit_XXX/` 移到對應分類資料夾。
22. Django 寫入 `metadata.csv`。

## 未來實作介面方向

文件階段先不固定完整 API payload，但最小行為介面應包含：

- 手機輪詢 Django：取得目前是否有某站 `capture_requested`。
- 手機上傳單張照片：包含 `fruit_id`、`capture_token`、`station_index`。
- ESP32 輪詢 Django：取得目前 `motor_command`，例如 `release_gate_1`。
- ESP32 回報 Django：回報 `station_1_ready`、`station_2_ready`、`station_3_ready`、`capture_sequence_finished`。

## GUI 必須要有的控制

1. 手動拍攝。
2. 上中等。
3. 下等。
4. 加工。
5. 廢棄。
6. 跳過 / 刪除。
7. 開啟資料夾。
8. 重新拍攝目前樣本。

## 異常處理原則

- 若 ESP32 不可用，dashboard 手動拍攝不得直接啟動純手機拍照流程，應顯示硬體未連線或無法開始三站流程。
- 若任一 SG90 閘門未回應或未能放行，流程應停止在安全狀態，不應繼續觸發下一站拍攝。
- 若手機未完成當站拍攝或照片上傳失敗，Django 不應設定下一個 `release_gate_X` 馬達命令，ESP32 也不應繼續放行到下一站。
- 若某一站拍攝失敗，樣本應保留在 `dataset/temp/fruit_XXX/`，不可直接寫入正式分類資料夾。
- dashboard 應能讓使用者重拍目前樣本或刪除失敗樣本。
- 若只有部分站點成功，`station_XX_ok` 應反映實際狀態，`note` 可記錄失敗原因。
- ESP32 閘門、手機相機、照片上傳或 Django 狀態機異常時，安全性優先，不應繼續放行到下一站造成資料錯位。

## 注意事項

手機瀏覽器若要開啟鏡頭，伺服器必須以 HTTPS 啟用。

```powershell
C:\Users\qoqo\anaconda3\envs\pf_iot_env\python.exe manage.py runsslserver 0.0.0.0:8000
```

## 開發流程

1. 先定義並實作 Django 三站中央狀態機。
2. 將手機 `/camera/` 改為每站輪詢、拍攝 1 張並上傳。
3. 將 ESP32 改為輪詢 Django 取得馬達命令，並回報站點就緒與流程完成。
4. 確認 ESP32 可以依序控制三段 SG90 閘門：`45` 度攔截、`0` 度放行。
5. 確認 `dataset/temp/fruit_XXX/`、分類資料夾、`metadata.csv`、`counter.json` 都能正確運作。
6. 接上 HC-SR04 自動觸發流程與 dashboard 手動開始請求。
7. 硬體三站流程穩定後，再整合 AI 推論與分類器控制。
