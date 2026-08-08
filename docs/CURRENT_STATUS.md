# 目前狀態

更新日期：2026-08-09

本文件是可覆寫的目前快照。完成歷史由 Git、tag 與 release 保存；未完成工作的詳細規格與討論以 GitHub Issues 為準。

## 現行穩定功能

- Django 中央狀態機可協調 HC-SR04 自動觸發、手機單站拍攝與 ESP32 三站閘門。
- 每顆果實保存三張站點照片，照片保存成功後才放行下一閘門。
- 首站捷徑、timeout retry、transition trace、照片原子保存與未分類資料鎖定可運作。
- Capture 與 sorter 共用單一 motor command slot，command ID 可跨 Django 重啟保持遞增。
- 人工分類先提交 Dataset 與 metadata，再驅動位置型 MG996R 分類器；硬體失敗不回滾資料。
- Dashboard 可管理拍攝 timing、檢查三張照片、分類與刪除；手機頁使用 single in-flight polling。
- 上游送料測試使用 HC-SR04 回授停止，並具備 `1000～20000 ms`、間距 `500 ms` 的本機 max timeout、sensor unavailable 安全停止與同 revision 校正確認；推薦上限為 `5000 ms`。
- 正式 Firmware 已按感測、閘門、分類器、HTTPS 與流程控制拆分模組。
- Django 與模型開發統一使用 `PF` Conda 環境，基準為 Python `3.14.4` 與 Django `5.2.16` LTS。
- 硬體與模型採獨立 Repository，照片快照存於獨立資料目錄。
- 目前 Dataset 快照包含 `327` 顆果實與 `981` 張照片，尚待建立正式 train／valid／test 切分與第二份備份。

現行協定與數值見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)。

## 正在開發

- [#1 整合上游送料機構](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1)
- [#2 分類器新增 SG90 擋臂](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2)
- [#12 擴充送料最大運轉時間為秒級可調範圍](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/12)

Issue #9 與 #12 的 Django、Dashboard 與 Firmware 軟體實作已完成；GPIO `23` 測試送料會在 HC-SR04 `<= 6.0 cm` 時停止，或依 `1000～20000 ms`、間距 `500 ms` 的安全上限在 ESP32 本機先停止，感測器 unavailable 也會立即本機停止。Dashboard 維持既有優雅暫停，正式自動運轉仍停用。

上游送料馬達已決定由 360° SG90 改為 360° MG996R，機構改採頂部開放的一體式送料筒與四片徑向撥片，果實由寬 `9 cm` 的側面出口直接送到拍攝平台起點。最終供電配置使用兩組獨立的 `4 × AA` 電池盒：一組供應送料與分類器兩顆 MG996R，另一組供應三顆拍攝平台 SG90 與分類器擋臂 SG90；兩組正極隔離，負極與 ESP32 共地。送料筒、馬達、分類器擋臂與雙電池盒尚待安裝、校正及實機驗收。

## 已知問題

- ESP32 HTTPS 偶爾出現 read timeout；目前依冪等 retry 與 client 重建復原。
- WebRTC 預覽可能受瀏覽器、熱點或 ICE 狀態影響，但不應阻塞拍攝上傳。
- 分類器沒有位置回授，completed 只表示控制時序完成。
- 原型的連續旋轉送料 SG90 受外力後會偏離原角度；已決定改用 360° MG996R，HC-SR04 終止策略與新馬達尚待整合驗證。
- 現有 metadata note 尚未正規化，不能直接作為完整模型特徵。
- 安全稽核仍限制系統只能部署於可信任、隔離的實驗室區網；詳見 [SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)。

## 待實機驗證

- 空軌感測器連續測試與 Echo timeout。
- 健康網路連續自動採集，量測首張照片延遲分布。
- 以高速錄影確認第 2、3 站 ready 前果實已停止。
- 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站與重複 trigger。
- 驗證未分類資料、刪除復原、分類器斷線與重複 command。
- 驗證送料前只有有效距離大於 `8.0 cm` 才可啟動；`0 cm`／Echo timeout 必須拒絕或立即停止並通知操作者。
- 以 `10` 顆作為現階段一體式送料筒裝載量，校正最慢可靠驅動脈波及由低往高的最大運轉時間，確認撥片不碰壁或停滯；超過 `10` 顆與正式安全填料線維持未驗證。
- 安裝送料 MG996R 與分類器擋臂 SG90 後，以混合果形連續完成 `20` 顆 HC-SR04 終止送料與完整分類；確認無漏送／雙送／卡料，兩組電池盒的空載與動作中電壓合規，六顆伺服無抖動／異音，且無 ESP32 reset、配電端子或線材異常溫升。
- 模擬最大運轉逾時、送料中感測器無回音與逾時後果實才抵達，確認不補轉且自動送料保持暫停。
- 分別模擬 ESP32 與 Django 在送料 command／report 邊界重新啟動，確認不會自動重複送料。
- 建立 Dataset 第二份備份、正式切分、標註與模型驗收。

驗收方法與門檻見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)；實體安全檢查見 [`hardware_notes/硬體接線與驗收摘要.md`](../hardware_notes/硬體接線與驗收摘要.md)。

## 下一個里程碑

依 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md) 與 [ADR-0015](adr/0015-integrated-radial-paddle-feeder.md) 完成 HC-SR04 終止送料、重啟防重與完整分類實機驗收。Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 通過後，再處理 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的分類後出料閘門。
