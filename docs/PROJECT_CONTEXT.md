# 百香果辨識系統專案脈絡

## 專案目標

本專案建立百香果照片蒐集、資料集管理與 AI 分級辨識流程。每顆果實在三個固定站點各取得一張清晰照片；蒐集模式由人工分類，自動檢測模式由本地 YOLO 與兩階段 XGBoost 判級，無法判級時由人工接手，最後經實體分類器分流。

目標流程是上游單顆送料後，以三段閘門停止拍攝；舊版滾動連拍不再是開發方向。現行協定、硬體設定與驗收標準以 [CAPTURE_SPEC.md](CAPTURE_SPEC.md) 為唯一來源，實作進度以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 為準。

## 系統架構

```text
Dashboard
  -> 啟用自動運轉並要求單顆送料

HC-SR04
  -> 正常終止上游送料、確認果實抵達並觸發採集工作階段

Django
  -> 中央狀態機與互斥
  -> 手機拍攝請求
  -> ESP32 motor command
  -> 蒐集／檢測兩個資料分區與分類生命週期
  -> 本地 YOLO 檢測、XGBoost 判級與單顆結果保存

手機相機頁
  -> 輪詢 Django
  -> 依指定站點拍攝並上傳一張照片

ESP32 Firmware
  -> 輪詢命令與回報狀態
  -> 控制送料、三站閘門、感測器與分類器
```

### Django

Django 是 capture 與 sorter 的狀態來源。它負責工作階段、拍攝 token、照片原子保存、單一 motor command slot、command ID、Dataset 搬移及錯誤復原。

`runtime_state.py`、`capture_timing.py`、`api_payloads.py`、`capture_session.py`、`dataset_store.py` 與 `webrtc_signaling.py` 承擔可獨立責任；`detection.py` 負責四模型推論、單顆 `result.json`／檢測總表保存與人工接手寫回，`grading.py` 負責兩階段 XGBoost 特徵契約與最差視角判級，兩者只回結構化結果，不接觸硬體；`notification.py` 從共同結果產生加工果通知文字與合成圖，`notification_delivery.py` 以持久待送在背景經 Cloudinary／LINE 發送，失敗不影響本地流程；`dataset_store.py` 負責 Dataset 檔案生命週期與 recovery marker 的檔案讀寫及欄位驗證，`views.py` 負責 HTTP 整合、工作模式選擇與狀態機協調及復原決策。

### ESP32 Firmware

正式 Firmware 位於 `firmware/Three_Gate_Data_Collection/`。入口 `.ino` 只建立 `CaptureController` 並呼叫 `begin()`／`tick()`；感測、閘門、分類器、HTTPS 與流程控制分屬既有模組。

ESP32 是 HTTPS client，Django 不主動呼叫硬體。Wi-Fi、感測、伺服 phase、request 與 retry 必須有界，硬體流程不得用長時間同步等待阻塞主迴圈。

### 手機與 Dashboard

手機頁只負責即時預覽、相機 readiness、取得單站拍攝請求與上傳照片。Dashboard 負責開始／優雅暫停自動運轉、runtime profile、送料校正、狀態、照片檢查、分類與刪除；自動檢測時主區顯示三張 × 四模型的真實產物，右側顯示整顆等級與待人工處理的接手入口。WebRTC 預覽失敗不得阻塞照片上傳流程。

## Repository 邊界

### 硬體 Repository

`Eten0721/PassionFruit_IoT_hardware` 維護 Django、ESP32 Firmware、硬體筆記、資料採集契約、本地部署推論，以及 `Django_Server/models/` 內經驗收的四個 YOLO 權重與兩個 XGBoost 判級模型。它不追蹤原始照片、Dataset、訓練輸出、歷史 checkpoint 或 child Repository。

### 模型 Repository

`fcu-passionfruit-project/ps-quality-detection-system` 獨立維護模型訓練、研究推論 pipeline、Dataset manifest 與候選權重。只有模型開發需要在硬體 Repository 外建立獨立 checkout；操作方式見 [模型開發環境](模型開發環境.md)。

兩個 Repository 必須分別檢查、commit 與 push。Django 部署推論由本 Repository 自足維護，只讀取 `Django_Server/models/ROI.pt`、`Color.pt`、`Wrinkle.pt`、`Defect.pt`、`GradeStage1.json`、`GradeStage2.json`，並以 Git LFS 管理；部署與權重替換不需要外部 checkout 或資料。責任切分見 [ADR-0017](adr/0017-fixed-django-model-assets.md) 與 [ADR-0019](adr/0019-deployed-two-stage-xgboost-grading.md)。

### Dataset

照片快照實體存於獨立資料目錄，模型 Repository 透過 ignored junction 存取。Dataset schema 與資料生命週期見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md)，目前快照與備份狀態見 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## 主要資料流

1. 操作員由 Dashboard 啟用自動運轉，Django 在安全邊界建立單顆送料命令。
2. ESP32 驅動上游送料；HC-SR04 確認果實抵達後先在本機停止馬達，再建立採集工作階段。
3. 前兩站皆遵守「果實停穩、手機上傳、Django 保存成功、下一閘門放行」；第 3 張保存後，果實繼續停留在 Gate 3。
4. 蒐集模式由使用者檢查並分類三張照片，Django 先提交 Dataset 與 metadata；自動檢測模式由 Django 本地推論與判級，先保存 `result.json` 與檢測總表，無法判級時待人工選級或放棄本輪。
5. `classify_fruit` 先將下置式分類器轉到目標角度並完成 `500 ms` 開迴路就位等待，再放行 Gate 3；果實通過落料管後，三顆 Gate 與分類器才共同歸位並回報完成。
6. 複合分類動作完成且安全條件仍成立時，Django 才建立下一次送料。自動檢測的模型或人工等級同樣在結果保存後進入此分類邊界。
7. 未完成工作與實機驗證由 GitHub Issues 追蹤。

完整事件、API、command 與 timeout 見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md)。

## 不可破壞的原則

- Django 是中央狀態來源；手機與 ESP32 不自行推測流程完成。
- 當站照片原子保存成功前，不得放行下一閘門。
- 第 3 張照片保存後不得直接放行 Gate 3；ESP32 回報 `gate3_sorter_v1`，且下置式分類器完成目標角度的 `500 ms` 開迴路就位等待後才能放行。
- 正式硬體流程不提供通用 manual capture；重拍只重新執行同一顆果實的三站流程，不驅動送料。純拍攝使用獨立受模式限制的入口，全程不建立 motor command，契約見 [CAPTURE_SPEC.md](CAPTURE_SPEC.md)。
- 未分類資料、active capture 或 sorter 動作存在時，不得開始下一顆。
- 送料馬達正常由 HC-SR04 回授停止，感測異常或最大運轉時間到期時也必須由 ESP32 本機停止；網路 retry 不得重複實體送料。
- 優雅暫停只禁止後續送料，已承諾送出的果實仍完成既有流程。
- 首站加速只能縮短安全條件成立後的控制往返，不得縮短機構停穩或照片保存交握。
- HTTP timeout 是不確定結果；retry 必須維持冪等，不能假定前次失敗。
- 資料分類先於實體分類；硬體失敗不得回滾已提交的照片、metadata 或 counter。
- 分類器、Gate 3 放行與歸位共同構成一次 `classify_fruit` 實體動作；結果不確定時不得自動重放。
- AI 只能輸出結構化結果並進入既有分類邊界，不得直接控制 GPIO、角度或 PWM。
- 必要檢測或判級失敗時不補零硬判；人工接手的結果另記來源，不得改寫原模型輸出或偽裝成模型成功。
- 硬體供電、邏輯電壓與共地安全優先於持續執行。

## 延伸文件

- 現行規格：[CAPTURE_SPEC.md](CAPTURE_SPEC.md)
- 目前狀態：[CURRENT_STATUS.md](CURRENT_STATUS.md)
- 架構決策：[adr/README.md](adr/README.md)
- 硬體接線與驗收：[硬體接線與驗收摘要.md](../record_image/硬體接線與驗收摘要.md)
- 正式部署模型：[權重替換與驗收](../Django_Server/models/README.md)
