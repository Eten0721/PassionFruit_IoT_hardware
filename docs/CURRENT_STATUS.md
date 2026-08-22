# 目前狀態

更新日期：2026-08-22

本文件是可覆寫的目前快照。完成歷史由 Git、tag 與 release 保存；未完成工作的詳細規格與討論以 GitHub Issues 為準。

## 現行穩定功能

- Django 中央狀態機可協調 HC-SR04 自動觸發、手機單站拍攝與 ESP32 三站閘門。
- 每顆果實保存三張站點照片，照片保存成功後才放行下一閘門。
- 首站捷徑、timeout retry、transition trace、照片原子保存與未分類資料鎖定可運作。
- Capture 與 sorter 共用單一 motor command slot，command ID 可跨 Django 重啟保持遞增。
- 人工分類先提交 Dataset 與 metadata，再驅動位置型 MG996R 分類器；硬體失敗不回滾資料。
- Dashboard 可管理拍攝 timing、檢查三張照片、分類與刪除；送料測試成功後勾選即保存人工確認，不需第二次測試或再次套用。手機頁使用 single in-flight polling，Dashboard 預覽依影片原始比例縮放。
- 上游正式送料與測試送料共用 HC-SR04 回授停止，並具備 `1000～20000 ms`、間距 `500 ms` 的本機 max timeout、sensor unavailable 安全停止與同 revision 校正確認；推薦上限為 `5000 ms`。
- 正式自動運轉會在 ESP32、送料校正、感測區、相機與三站流程皆就緒時送入單顆果實；同一筆 HC-SR04 trigger 啟動首站，分類器完成後才允許下一顆。
- 正式 Firmware 已按感測、閘門、分類器、HTTPS 與流程控制拆分模組。
- Django 與模型開發統一使用 `PF` Conda 環境，基準為 Python `3.14.4` 與 Django `5.2.16` LTS。
- 硬體與模型採獨立 Repository，照片快照存於獨立資料目錄。
- 目前 Dataset 快照包含 `327` 顆果實與 `981` 張照片，尚待建立正式 train／valid／test 切分與第二份備份。

現行協定與數值見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)。

## 已接受但尚未完成

原 Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 所涵蓋的單顆送料、HC-SR04 本機停止與 Django 自動運轉基礎整合已完成；分槽送料盤、XINHUI 馬達及雙電源屬 ADR-0016 的新實機驗收範圍。原 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的 SG90 分類器擋臂已取消，不再是開發目標。

Issue #9、#11 與 #12 的 Django、Dashboard 與 Firmware 軟體實作已完成；GPIO `23` 的 production／calibration 送料會在 HC-SR04 `<= 6.0 cm` 時停止，或依 `1000～20000 ms`、間距 `500 ms` 的安全上限在 ESP32 本機先停止，感測器 unavailable 也會立即本機停止。Dashboard 已開放具 runtime 安全門檻的正式自動運轉與優雅暫停；機構校正及連續運轉仍依待實機驗證項目執行。

[ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md) 已接受新硬體目標：三站閘門全部改為位置型 MG996R；上游使用 XINHUI `60KG` 連續旋轉伺服帶動分槽盤；分類器移到平台出口正下方且只保留一顆位置型 MG996R；全部馬達改用兩組獨立 `12 V／20 A` 電源與兩顆 LM25116。平台目標為低點 `7 cm`、高點 `18 cm`、水平長度 `55 cm`、寬度 `21 cm`、軌道內寬 `10 cm` 與站距 `16 cm`。上述機構尚未完成製作與實機驗收。

現行 Django 與 Firmware 仍在第 3 張照片保存後建立 `release_gate_3`，並在 Gate 3 放行與歸位後才開放人工分類；三站閘門也尚未實作 MG996R 的非阻塞慢速角度遞增。目標流程需改為第 3 張保存後讓果實留在 Gate 3，`classify_fruit` 再依序完成分類器就位、Gate 3 慢速放行、`1000 ms` 落果保持及兩者歸位。此軟體差異尚未實作，不得把新硬體契約視為目前已穩定運作。

## 已知問題

- ESP32 HTTPS 偶爾出現 read timeout；目前依冪等 retry 與 client 重建復原。
- WebRTC 預覽可能受瀏覽器、熱點或 ICE 狀態影響，但不應阻塞拍攝上傳。
- 分類器沒有位置回授，completed 只表示控制時序完成。
- 送料先後使用過連續旋轉 SG90 與 360° MG996R；MG996R 原型在 2026-08-12 曾出現 `feed_one` 已建立但馬達未實際轉動，最後回報 timeout。新目標改用 XINHUI `60KG` 與雙電源，尚未驗證是否排除未起轉、卡料與供電壓降。
- 2026-08-12 重開舊電池盒開關曾使送料 MG996R 暫時恢復。電壓下降、開關／接點電阻或電池盒供電能力都只是當時推論，沒有同步量測可確認根因；電池盒已退出目標方案。
- 同日拆裝時發生不明放電／電擊感與電腦短暫黑屏；舊 ESP32 的 CH340 仍可枚舉但 ROM bootloader 無回應，已停止使用。新板接回前必須完成斷電極性、裸線、平台金屬件、USB 回灌與共地檢查。
- 現有 metadata note 尚未正規化，不能直接作為完整模型特徵。
- 安全稽核仍限制系統只能部署於可信任、隔離的實驗室區網；詳見 [SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)。

## 待實機驗證

- 空軌感測器連續測試與 Echo timeout。
- 健康網路連續自動採集，量測首張照片延遲分布。
- 以高速錄影確認第 2、3 站 ready 前果實已停止。
- 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站與重複 trigger。
- 驗證第 3 張保存後 Gate 3 保持關閉、未分類資料鎖定、刪除時不自動開 Gate 3、分類器斷線，以及重複 command 不重複實體動作。
- 驗證送料前只有有效距離大於 `8.0 cm` 才可啟動；`0 cm`／Echo timeout 必須拒絕或立即停止並通知操作者。
- 以 `10` 顆作為現階段一體式送料筒裝載量，校正 XINHUI 最慢可靠驅動脈波及由低往高的最大運轉時間，確認分槽盤不碰壁、停滯、漏送或雙送；超過 `10` 顆與正式安全填料線維持未驗證。
- 分別驗證兩組 `12 V／20 A` 電源與 LM25116：記錄空載電壓、正常動作中最低電壓、峰值電流、線材／端子／模組溫升、馬達未起轉次數及 ESP32 reset；再以混合果形連續完成 `20` 顆 HC-SR04 終止送料、三站拍攝、Gate 3 等待分類與完整實體分流。
- 驗證三顆 MG996R 閘門以非阻塞小角度遞增放行及歸位，並以高速錄影確認分類器就位後才開 Gate 3、分類器在 Gate 3 開啟後保持目標角度至少 `1000 ms`。
- 模擬最大運轉逾時、送料中感測器無回音與逾時後果實才抵達，確認不補轉且自動送料保持暫停。
- 分別模擬 ESP32 與 Django 在送料 command／report 邊界重新啟動，確認不會自動重複送料。
- 建立 Dataset 第二份備份、正式切分、標註與模型驗收。

驗收方法與門檻見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)；實體安全檢查見 [`hardware_notes/硬體接線與驗收摘要.md`](../hardware_notes/硬體接線與驗收摘要.md)。

## 下一個里程碑

依 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md) 與 [ADR-0016](adr/0016-hardware-platform-feeder-sorter-redesign.md) 完成新平台、分槽盤送料、雙電源、Gate 3 等待分類與複合 `classify_fruit` 的軟體實作及完整實機驗收；後續未完成工作只使用符合新架構的 GitHub Issues 追蹤。
