# 目前狀態

更新日期：2026-10-27

本文件是可覆寫的目前快照。完成歷史由 Git、tag 與 release 保存；未完成工作的詳細規格與討論以 GitHub Issues 為準。

## 實機驗收

2026-10-27 於研究室完成 [ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md) 新平台的完整實機驗收，Issue [#15](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/15)、[#16](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/16) 已關閉；驗收項目見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md#實機)。

- ESP32、XINHUI 送料馬達、三站與分類器 MG996R 皆正常；送料無卡轉、未起轉或壓降。
- 分類器角度實測調整為 Home `160°`，上等／中等／下等／加工 `140°／150°／170°／180°`；其餘時序與送料參數同為現場實測值，見 `Django_Server/hardware_params.toml`。
- 送料、三站拍攝、Gate 3 等待分類、分槽、逾時與斷線復原、LINE 通知、奶蛋博士系統狀態查詢、資料分區操作及乾淨電腦啟動器均已實測。

## 現行功能

### 拍攝與硬體流程

- Django 中央狀態機協調上游單顆送料、HC-SR04 觸發、手機單站拍攝與 ESP32 三站閘門；每站照片原子保存成功後才放行下一閘門。
- Capture 與 sorter 共用單一 motor command slot，command ID 跨 Django 重啟遞增；ESP32／Django 重啟或 terminal report retry 不重送實體動作，結果不確定時須於 Dashboard 確認人工安全復原。
- 第 3 張保存後 Gate 3 保持 Home；分類先提交資料，再以 `classify_fruit` 依序完成分類器就位、Gate 3 放行與共同歸位，硬體失敗不回滾資料。
- 送料由 HC-SR04 回授停止，另有 ESP32 本機逾時與感測失效保護；分類完成後才送下一顆，並支援優雅暫停。送料逾時可按「結束等待」結束本輪，雙端歸位後開放模式與網路設定；果實若仍經過感測器，照常觸發三站拍攝。契約見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md#安全逾時感測異常與延遲抵達)。
- 純拍攝完全隔離 ESP32：一次手動觸發自動拍三張，不建立 motor command。

### 裝置網路

- 每次 HTTPS 服務啟動須在 Dashboard 確認網路介面，產生本機相機 QR；同介面換 IP 會撤銷舊工作並更新位址，介面消失須人工重選。
- ESP32 以 `local_network_v1` 動態探索控制端，雙端依 lease／keepalive 與相機 readiness 停止後續動作；失聯後保存跨重啟安全鎖，經人工確認與雙端歸位提交才可重新開始。
- 安全閒置時按 BOOT 進入配網頁，Wi-Fi 保存於 NVS。

### 資料分區

- 蒐集與檢測使用 `DATASET_ROOT/collection/`、`DATASET_ROOT/detection/` 兩個獨立分區，暫存、編號、刪除與重置互不影響；只有蒐集分區有四級目錄與 `metadata.csv`。

### 自動檢測

- 三張照片保存後，本地依序執行 ROI、color、wrinkle、defect 四個 YOLO 模型，再以兩階段 XGBoost 逐張判級，整顆取三張最低級；無法判級時由操作員人工判級或放棄本輪。
- 每顆保存原圖、處理圖與 `result.json`，`detection_summary.csv` 每顆一列；硬體模式保存後經 `classify_fruit` 分槽。
- 加工果經 Cloudinary／LINE 發送圖文通知；檢測統計、三層趨勢提醒（預設關閉）與檢測報告 ZIP 匯出可用。契約見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md#檢測資料與-line-通知)。
- 奶蛋博士唯讀 Gemini 助手可查詢統計與系統狀態。契約見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md#奶蛋博士)。

### 環境與模型資產

- 統一使用 `PF` Conda 環境，基準為 Python `3.14.4` 與 Django `5.2.16` LTS。
- 四個 YOLO 權重與兩個 XGBoost 模型固定存於 `Django_Server/models/`，由 Git LFS 管理。
- Windows 雙擊啟動器可在乾淨電腦自動安裝 Miniforge 與建立 `PF`。決策見 [ADR-0021](adr/0021-double-click-launcher-and-release-zip.md)。

## 已知問題

- Windows 使用者名稱含中文時，啟動器安裝會卡住；目前須改用英文名稱的使用者帳號。
- ESP32 HTTPS 偶爾出現 read timeout；目前依冪等 retry 與 client 重建復原。
- WebRTC 預覽可能受瀏覽器、熱點或 ICE 狀態影響，但不阻塞拍攝上傳。
- 分類器沒有位置回授，completed 只表示控制時序完成。
- 送料筒只以 `10` 顆驗證裝載量，超過 `10` 顆未驗證。
- 交付 XGBoost 的訓練 CSV、YOLO 權重與前處理紀錄不在本機；以交付範例重算 48 顆、144 張全數一致，但不宣稱部署輸入分布與訓練一致。
- 現有 metadata note 尚未正規化，不能直接作為模型特徵。
- 趨勢提醒門檻尚待以實際檢測資料校正。
- 系統僅供可信任、隔離的實驗室區網使用。

## 下一步

Dataset 目前有 `327` 顆、`981` 張照片，尚待正式 train／valid／test 切分、第二份備份與模型驗收；細節以 GitHub Issues 為準。
