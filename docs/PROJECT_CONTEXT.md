# 百香果辨識系統專案脈絡

## 專案目標

本專案建立百香果照片蒐集、資料集管理與後續 AI 分級辨識流程。現階段以穩定取得每顆果實三個固定站點的清晰照片為主，再由人工分類與實體分類器完成資料與果實分流。

目標流程是上游單顆送料後，以三段閘門停止拍攝；舊版滾動連拍不再是開發方向。現行協定、硬體設定與驗收標準以 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md) 為唯一來源，實作進度以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 為準。

## 系統架構

```text
Dashboard
  -> 啟用自動運轉並要求單顆送料

HC-SR04
  -> 確認果實抵達並觸發採集工作階段

Django
  -> 中央狀態機與互斥
  -> 手機拍攝請求
  -> ESP32 motor command
  -> Dataset、metadata 與分類生命週期

手機相機頁
  -> 輪詢 Django
  -> 依指定站點拍攝並上傳一張照片

ESP32 Firmware
  -> 輪詢命令與回報狀態
  -> 控制送料、三站閘門、感測器與分類器
```

### Django

Django 是 capture 與 sorter 的狀態來源。它負責工作階段、拍攝 token、照片原子保存、單一 motor command slot、command ID、Dataset 搬移及錯誤復原。

`runtime_state.py`、`capture_timing.py`、`api_payloads.py`、`capture_session.py`、`dataset_store.py` 與 `webrtc_signaling.py` 承擔可獨立責任；`views.py` 仍負責 HTTP 整合、Dataset 生命週期與部分狀態機 helper，後續應延續現有邊界逐步縮小。

### ESP32 Firmware

正式 Firmware 位於 `firmware/Three_Gate_Data_Collection/`。入口 `.ino` 只建立 `CaptureController` 並呼叫 `begin()`／`tick()`；感測、閘門、分類器、HTTPS 與流程控制分屬既有模組。

ESP32 是 HTTPS client，Django 不主動呼叫硬體。Wi-Fi、感測、伺服 phase、request 與 retry 必須有界，硬體流程不得用長時間同步等待阻塞主迴圈。

### 手機與 Dashboard

手機頁只負責即時預覽、相機 readiness、取得單站拍攝請求與上傳照片。Dashboard 負責開始／優雅暫停自動運轉、runtime profile、送料校正、狀態、照片檢查、分類與刪除。WebRTC 預覽失敗不得阻塞照片上傳流程。

## Repository 邊界

### 硬體 Repository

`Eten0721/PassionFruit_IoT_hardware` 維護 Django、ESP32 Firmware、硬體筆記、資料採集契約與未來 AI adapter。它不追蹤原始照片、Dataset、模型權重、訓練輸出或 child Repository。

### 模型 Repository

`fcu-passionfruit-project/ps-quality-detection-system` 獨立維護模型訓練、推論 pipeline、Dataset manifest 與正式權重。本機 checkout 放在 `external/ps-quality-detection-system/` 並保留自己的 `.git`；操作方式見 [`external/README.md`](../external/README.md)。

兩個 Repository 必須分別檢查、commit 與 push。模型只有通過驗收的正式權重可使用 Git LFS；原始照片、`runs/` 與歷史 checkpoint 不進 Git。

### Dataset

照片快照實體存於獨立資料目錄，模型 Repository 透過 ignored junction 存取。Dataset schema 與資料生命週期見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)，目前快照與備份狀態見 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## 主要資料流

1. 操作員由 Dashboard 啟用自動運轉，Django 在安全邊界建立單顆送料命令。
2. ESP32 完成有界送料並停止，HC-SR04 確認果實抵達後建立採集工作階段。
3. 每站皆遵守「果實停穩、手機上傳、Django 保存成功、下一閘門放行」。
4. 三站完成後，使用者檢查並分類照片。
5. Django 先提交 Dataset 與 metadata，再嘗試下發實體分類命令。
6. 分類器完成且安全條件仍成立時，Django 才建立下一次送料。
7. 未完成工作與實機驗證由 GitHub Issues 追蹤。

完整事件、API、command 與 timeout 見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)。

## 不可破壞的原則

- Django 是中央狀態來源；手機與 ESP32 不自行推測流程完成。
- 當站照片原子保存成功前，不得放行下一閘門。
- 正式流程不提供 manual capture；重拍只重新執行同一顆果實的三站流程，不驅動送料。
- 未分類資料、active capture 或 sorter 動作存在時，不得開始下一顆。
- 送料馬達必須在 ESP32 本機有界停止；網路 retry 不得重複實體送料。
- 優雅暫停只禁止後續送料，已承諾送出的果實仍完成既有流程。
- 首站加速只能縮短安全條件成立後的控制往返，不得縮短機構停穩或照片保存交握。
- HTTP timeout 是不確定結果；retry 必須維持冪等，不能假定前次失敗。
- 資料分類先於實體分類；硬體失敗不得回滾已提交的照片、metadata 或 counter。
- AI 只能輸出結構化結果並進入既有分類邊界，不得直接控制 GPIO、角度或 PWM。
- 硬體供電、邏輯電壓與共地安全優先於持續執行。

## 延伸文件

- 現行規格：[DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)
- 目前狀態：[CURRENT_STATUS.md](CURRENT_STATUS.md)
- 架構決策：[adr/README.md](adr/README.md)
- 機構紀錄：[HARDWARE_DEVELOPMENT_REPORT.md](HARDWARE_DEVELOPMENT_REPORT.md)
- AI 決策層：[`decision_layer/README.md`](../decision_layer/README.md)
- 安全稽核：[SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)
