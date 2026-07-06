## 照片採集系統規格

目前的短期開發目標是完成 Django-based 照片採集系統。

### 資料夾結構

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

  廢棄/
    fruit_003/

  加工/
    fruit_004/

  metadata.csv
  counter.json
```

### 資料夾用途

`dataset/` 是整個照片資料集根目錄。

`dataset/temp/` 是暫存區。每次拍攝後，照片先存入這裡，等待人工確認與分類。

`dataset/temp/fruit_XXX/` 代表尚未正式分類的一顆百香果樣本。每個樣本固定包含 6 張照片。

`dataset/上中等/` 存放人工判定為上中等級的百香果樣本。

`dataset/下等/` 存放人工判定為下等級的百香果樣本。

`dataset/加工/` 存放外觀不適合直接作為高品質商品，但仍可作為加工用途的百香果樣本。

`dataset/廢棄/` 存放不適合食用、販售或加工的百香果樣本。

`metadata.csv` 記錄每一筆樣本的 fruit_id、分類、拍攝時間、路徑與備註。

`counter.json` 記錄下一個要使用的 fruit_id，避免刪除、跳過、重拍後造成編號混亂。

### Metadata 欄位

```csv
fruit_id,label,capture_time,path,capture_count,station_01_ok,station_02_ok,station_03_ok,note
fruit_001,上中等,2026-xx-xx 14:32:10,上中等/fruit_001,3,true,true,true,null
```

`note` 為選填欄位，不應阻礙現場快速採集。

## 裝置分工
手機：
  開啟 Django 提供的 /camera/ 頁面
  顯示滿版鏡頭畫面
  連拍 6 張照片
  上傳照片給 Django

電腦 Django：
  提供 /camera/ 手機拍照頁
  提供 /dashboard/ 電腦 GUI 頁
  接收 ESP32 trigger
  管理 fruit_id、temp、分類資料夾、metadata.csv
  [未來負責 AI 推論與決策]

ESP32：
  讀取 HC-SR04
  偵測百香果進入拍攝區
  送 HTTP request 給 Django 觸發拍照
  [未來接收 Django 分類結果]
  控制馬達、伺服機、分類機構

## 資料採集流程(拍攝照片)
百香果滾入拍攝區
↓
HC-SR04 / 紅外線感測器偵測到百香果
↓
ESP32 向 Django 發送 trigger request
↓
Django 記錄「需要拍攝」
↓
手機瀏覽器上的 camera page 接收到拍攝命令
↓
手機連拍 6 張照片
↓
手機把 6 張照片 upload 給 Django
↓
Django 暫存到 temp/fruit_001/
↓
電腦 dashboard 預覽 6 張照片
↓
使用者按「上中等 / 下等 / 廢棄 / 加工」
↓
Django 把整個 fruit_001 資料夾移到對應分類目錄
↓
metadata.csv 記錄 fruit_id、分類、時間、路徑

## 資料夾設計
dataset/
  temp/
    fruit_001/
      img_01.jpg
      img_02.jpg
      img_03.jpg
      img_04.jpg
      img_05.jpg
      img_06.jpg

  上中等/
    fruit_001/
      img_01.jpg
      ...

  下等/
    fruit_002/

  廢棄/
    fruit_003/

  加工/
    fruit_004/

  metadata.csv
  counter.json

## 資料夾結構與用途

資料集目錄使用以下結構：
`dataset/` 是整個照片資料集的根目錄。

`dataset/temp/` 是暫存區。每次拍攝時，系統應先建立一個新的 `fruit_XXX/` 資料夾，並將該顆百香果的 6 張照片先存放在這裡。使用者尚未確認分類前，不要直接放入正式分類資料夾。

`dataset/temp/fruit_XXX/` 代表某一顆尚未完成分類的百香果樣本。每個 `fruit_XXX/` 資料夾內必須包含 6 張照片，檔名固定為 `img_01.jpg` 到 `img_06.jpg`。

`dataset/上中等/` 用來存放人工判定為上中等級的百香果樣本。

`dataset/下等/` 用來存放人工判定為下等級的百香果樣本。

`dataset/加工/` 用來存放外觀不適合直接作為高品質商品，但仍可作為加工用途的百香果樣本。

`dataset/廢棄/` 用來存放人工判定為不適合食用、販售或加工的百香果樣本。

`metadata.csv` 用來記錄每顆百香果的基本資料，包含 fruit_id、分類結果、拍攝時間、儲存路徑與備註。即使資料夾本身已經依照分類放置，仍然必須寫入 metadata，方便後續統計、模型訓練、資料清理與決策層分析。

`counter.json` 用來記錄下一個要使用的 fruit_id。系統不應該只依靠掃描資料夾來猜測下一個 ID，避免刪除、重拍或中斷後造成編號混亂。

## GUI必須要有的按鈕
1. 手動拍攝
2. 上中等
3. 下等
4. 廢棄
5. 加工
6. 跳過 / 刪除
7. 開啟資料夾

## GUI流程
1. 電腦顯示手機鏡頭即時畫面
2. 使用者可以輸入起始 ID，例如 120
3. 沒輸入就從 counter.json 自動遞增
4. 收到 ESP32 超音波trigger 或使用者按「手動拍攝」
5. 程式建立 temp/fruit_120/
6. 連拍六張，存成 img_01.jpg ~ img_06.jpg
7. GUI 顯示六張縮圖
8. 使用者確認照片是否清楚
9. 按「上中等 / 下等 / 廢棄 / 加工」
10. 程式把 temp/fruit_120/ 移到對應分類資料夾
11. metadata.csv 記錄 fruit_id、分類、時間、路徑
12. fruit_id 自動 +1

## metadata.csv紀錄格式範例
fruit_id,label,capture_time,path,note
fruit_001,上中等,2026-xx-xx 14:32:10,上中等/fruit_001,
fruit_002,加工,2026-xx-xx 14:33:05,加工/fruit_002,表皮皺
note欄位可以讓使用者自行輸入 或者空白

## 注意事項
手機瀏覽器若要開啟鏡頭，伺服器必須以HTTPS啟用!
'python manage.py runsslserver 0.0.0.0:8000'

# 開發流程
## 第一階段 : 純電腦與手機的溝通，暫不考慮MCU
手機畫面進電腦
Python 顯示預覽
按鍵或 GUI 按鈕連拍六張
建立 fruit_001 資料夾
顯示六張預覽
按分類按鈕移動資料夾

## 第二階段 : 考慮ESP32連接HC-SR04超音波感測器
ESP32 與電腦連上手機熱點 Wi-Fi
當觸發ESP32上的超音波訊號
傳 POST 給電腦
電腦收到後拍六張