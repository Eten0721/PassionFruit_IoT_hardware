# 目前狀態

更新日期：2026-10-14

本文件是可覆寫的目前快照。完成歷史由 Git、tag 與 release 保存；未完成工作的詳細規格與討論以 GitHub Issues 為準。

## 現行穩定功能

### 拍攝與硬體流程

- Django 中央狀態機協調上游單顆送料、HC-SR04 觸發、手機單站拍攝與 ESP32 三站閘門；每站照片原子保存成功後才放行下一閘門，首站捷徑、timeout retry、transition trace 與未分類資料鎖定可運作。
- Capture 與 sorter 共用單一 motor command slot，command ID 跨 Django 重啟遞增；ESP32／Django 重啟或 terminal report retry 不重送實體動作，結果不確定時須於 Dashboard 確認人工安全復原。
- 第 3 張保存後 Gate 3 保持 Home；蒐集模式人工分類先提交 Dataset 與 metadata，再以 `classify_fruit` 依序完成分類器就位、Gate 3 放行與共同歸位，硬體失敗不回滾資料。
- 送料由 HC-SR04 回授停止，另有 ESP32 本機最大運轉時間與 sensor unavailable 安全停止；正式自動運轉要求 `feeder_v1`、`gate3_sorter_v1`、送料校正、感測區、相機與三站流程皆就緒，分類完成後才送下一顆，並支援優雅暫停。
- 純拍攝完全隔離 ESP32：一次手動觸發自動拍三張，不建立 motor command。
- 正式 Firmware 依感測、閘門、分類器、HTTPS 與流程控制拆分模組。

### 裝置網路

- 每次 HTTPS 服務啟動須在 Dashboard 確認網路介面，產生本機相機 QR 並檢查 origin／subnet；同介面換 IP 會撤銷舊工作並更新位址，介面消失須人工重選。
- ESP32 以 `local_network_v1` 動態探索控制端，雙端依 lease／keepalive 與相機 readiness 停止後續動作；失聯後保存跨重啟安全鎖，經清理、人工確認、歸位與雙端提交才可重新開始。
- 安全閒置時按一次 BOOT 進入配網頁，掃描 SSID、保存 NVS，正式 Firmware 不再編譯時固定 Wi-Fi。
- iPhone Chrome 於新 DHCP 位址、另一台電腦與完全離線區網皆可開啟相機並保存三張照片（2026-10-11 操作員確認）。

### 資料分區

- 蒐集與檢測使用 `DATASET_ROOT/collection/`、`DATASET_ROOT/detection/` 兩個獨立分區，各自的暫存、編號、刪除與重置互不影響；模式跨重啟恢復，舊資料保留原位。只有蒐集分區有四級目錄與 `metadata.csv`。

### 自動檢測

- 三張照片保存後，本地依序執行 ROI、color、wrinkle、defect 四個 YOLO 模型，再以兩階段 XGBoost 逐張判級，整顆取三張最低級。
- 每顆保存原圖、處理圖與 `result.json`，`detection_summary.csv` 每顆一列並包含失敗輪次。
- 無法判級時標記待人工處理；純拍攝可由操作員人工選四級或放棄本輪，人工結果與原模型分類分開保存。
- Dashboard 主區以三張 × 四模型逐步顯示真實產物，右側顯示整顆結果、人工接手與手機預覽，操作列可收合。
- 硬體模式檢測後仍保持 Gate 3 人工復原，不自動分槽。

### 環境與模型資產

- Django 與模型開發統一使用 `PF` Conda 環境，基準為 Python `3.14.4` 與 Django `5.2.16` LTS。
- 四個 YOLO 權重與兩個 XGBoost 模型固定存於 `Django_Server/models/`，由 Git LFS 管理；部署不需外部 Repository。
- 目前 Dataset 快照包含 `327` 顆果實與 `981` 張照片，尚待建立正式 train／valid／test 切分與第二份備份。

各項協定與數值見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md)；完成歷史以 Git 與已關閉 Issues 為準。

## 已接受但尚未完成

- 硬體模式下的 AI／人工分類後安全分槽與連續檢測尚未接通，由 Issue [#33](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/33) 追蹤。
- 加工果 LINE 圖文通知、Cloudinary 附圖與通知開關尚未實作，由 Issue [#34](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/34)、[#35](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/35) 追蹤；整合驗收見 Issue [#36](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/36)。
- 資料分區的瀏覽器、手機與操作員觀察驗收（蒐集／檢測各一輪、只刪檢測、重置後重啟、處理中切換）尚未執行。
- 離線換網、換電腦與各階段斷線復原的實機驗收由 Issue [#27](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/27) 追蹤；BOOT 候選腳位、換網與斷電保存仍待操作員驗證。

原 Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 所涵蓋的單顆送料、HC-SR04 本機停止與 Django 自動運轉基礎整合已完成；分槽送料盤、XINHUI 馬達及單電源雙降壓分支屬 ADR-0016 的新實機驗收範圍。原 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的 SG90 分類器擋臂已取消，不再是開發目標。

Issue #9、#11 與 #12 的 Django、Dashboard 與 Firmware 軟體實作已完成；GPIO `23` 的 production／calibration 送料會在 HC-SR04 `<= 6.0 cm` 時停止，或依 `1000～20000 ms`、間距 `500 ms` 的安全上限在 ESP32 本機先停止，感測器 unavailable 也會立即本機停止。Dashboard 已開放具 runtime 安全門檻的正式自動運轉與優雅暫停；機構校正及連續運轉仍依待實機驗證項目執行。

[ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md) 已接受新硬體目標：三站閘門全部改為位置型 MG996R；上游使用 XINHUI `60KG` 連續旋轉伺服帶動分槽盤；分類器移到平台出口正下方且只保留一顆位置型 MG996R；全部馬達使用一顆 `12 V／20 A` 電源供應器與兩顆 LM25116，分別提供 `6.0 V` 與 `8.4 V`。平台目標為低點 `7 cm`、高點 `18 cm`、水平長度 `55 cm`、寬度 `21 cm`、軌道內寬 `10 cm` 與站距 `16 cm`。上述機構尚未完成製作與實機驗收。

Issue [#14](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/14) 的 Django、Dashboard 與 Firmware 軟體實作已完成：第 3 張保存後不再建立 `station_index=3` 的 `release_gate` 或等待獨立 `capture_sequence_finished`；分類器依四個目標角度轉向並等待 `500 ms`，Gate 3 放行、落果保持 `1000 ms`，再讓三顆 Gate 與分類器同時歸位並等待 `500 ms`。Django 會以 `gate3_sorter_v1` 阻擋舊 Firmware，並以原子 recovery marker 在 ESP32／Django 重啟或 terminal report retry 時避免重送實體動作；結果不確定時須由 Dashboard 明確確認人工安全復原，才可清除鎖定。純 C++ harness、Django 測試與正式 ESP32 編譯已通過；新機構的實體動作仍須依 Issue [#15](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/15) 驗收，不得把軟體驗證視為硬體已穩定運作。

## 已知問題

- ESP32 HTTPS 偶爾出現 read timeout；目前依冪等 retry 與 client 重建復原。
- WebRTC 預覽可能受瀏覽器、熱點或 ICE 狀態影響，但不應阻塞拍攝上傳。
- 分類器沒有位置回授，completed 只表示控制時序完成。
- 送料先後使用過連續旋轉 SG90 與 360° MG996R；MG996R 原型在 2026-08-12 曾出現 `feed_one` 已建立但馬達未實際轉動，最後回報 timeout。新目標改用 XINHUI `60KG` 與單電源雙降壓分支，尚未驗證是否排除未起轉、卡料與供電壓降。
- 2026-08-12 重開舊電池盒開關曾使送料 MG996R 暫時恢復。電壓下降、開關／接點電阻或電池盒供電能力都只是當時推論，沒有同步量測可確認根因；電池盒已退出目標方案。
- 同日拆裝時發生不明放電／電擊感與電腦短暫黑屏；舊 ESP32 的 CH340 仍可枚舉但 ROM bootloader 無回應，已停止使用。新板接回前必須完成斷電極性、裸線、平台金屬件、USB 回灌與共地檢查。
- 現有 metadata note 尚未正規化，不能直接作為完整模型特徵。
- 交付 XGBoost 的訓練 CSV、所用 YOLO 權重與前處理紀錄不在本機任一 Repository；以交付範例重算 48 顆、144 張的單張與整顆等級全數一致，但缺訓練資料證據，不宣稱部署輸入分布與訓練一致。
- 系統目前僅供可信任、隔離的實驗室區網使用；開發完成後重新評估安全稽核。

## 待實機驗證

- 空軌感測器連續測試與 Echo timeout。
- 健康網路連續自動採集，量測首張照片延遲分布。
- 以高速錄影確認第 2、3 站 ready 前果實已停止。
- 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站與重複 trigger。
- 驗證第 3 張保存後 Gate 3 保持關閉、未分類資料鎖定、刪除時不自動開 Gate 3、分類器斷線，以及重複 command 不重複實體動作。
- 驗證送料前只有有效距離大於 `8.0 cm` 才可啟動；`0 cm`／Echo timeout 必須拒絕或立即停止並通知操作者。
- 以 `10` 顆作為現階段一體式送料筒裝載量，校正 XINHUI 最慢可靠驅動脈波及由低往高的最大運轉時間，確認分槽盤不碰壁、停滯、漏送或雙送；超過 `10` 顆與正式安全填料線維持未驗證。
- 驗證共用 `12 V／20 A` 電源供應器與兩顆 LM25116 的輸入及輸出：記錄空載電壓、正常動作中最低電壓、峰值電流、線材／端子／模組溫升、馬達未起轉次數及 ESP32 reset；再以混合果形連續完成 `20` 顆 HC-SR04 終止送料、三站拍攝、Gate 3 等待分類與完整實體分流。
- 依 Issue [#16](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/16) 驗證新平台使用直接 Home／Release 控制時是否仍會夾果；只有實測仍失敗才開發非阻塞小角度遞增。另以高速錄影確認分類器等待 `500 ms` 後才開 Gate 3、Gate 3 開啟後保持目標角度至少 `1000 ms`，以及四顆馬達同時歸位。
- 模擬最大運轉逾時、送料中感測器無回音與逾時後果實才抵達，確認不補轉且自動送料保持暫停。
- 分別模擬 ESP32 與 Django 在送料 command／report 邊界重新啟動，確認不會自動重複送料。
- 建立 Dataset 第二份備份、正式切分、標註與模型驗收。

驗收方法與門檻見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md)；實體安全檢查見 [`record_image/硬體接線與驗收摘要.md`](../record_image/硬體接線與驗收摘要.md)。

## 下一個里程碑

依 [CAPTURE_SPEC.md](CAPTURE_SPEC.md) 與 [ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md) 完成 Issue [#15](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/15) 的完整實機驗收，以及 Issue [#16](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/16) 的三站直接控制觀察。
