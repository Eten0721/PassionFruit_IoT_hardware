# 目前狀態

更新日期：2026-07-26

本文件是可覆寫的目前快照。完成歷史由 Git、tag 與 release 保存；未完成工作的詳細規格與討論以 GitHub Issues 為準。

## 現行穩定功能

- Django 中央狀態機可協調 HC-SR04／手動觸發、手機單站拍攝與 ESP32 三站閘門。
- 每顆果實保存三張站點照片，照片保存成功後才放行下一閘門。
- 首站捷徑、timeout retry、transition trace、照片原子保存與未分類資料鎖定可運作。
- Capture 與 sorter 共用單一 motor command slot，command ID 可跨 Django 重啟保持遞增。
- 人工分類先提交 Dataset 與 metadata，再驅動 MG996R；硬體失敗不回滾資料。
- Dashboard 可管理拍攝 timing、檢查三張照片、分類與刪除；手機頁使用 single in-flight polling。
- 正式 Firmware 已按感測、閘門、分類器、HTTPS 與流程控制拆分模組。
- 硬體與模型採獨立 Repository，照片快照存於獨立資料目錄。
- 目前 Dataset 快照包含 `327` 顆果實與 `981` 張照片，尚待建立正式 train／valid／test 切分與第二份備份。

現行協定與數值見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)。

## 正在開發

- [#1 整合上游送料機構與 360° SG90](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1)
- [#2 分類器新增 180° SG90 出料閘門](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2)

## 已知問題

- ESP32 HTTPS 偶爾出現 read timeout；目前依冪等 retry 與 client 重建復原。
- WebRTC 預覽可能受瀏覽器、熱點或 ICE 狀態影響，但不應阻塞拍攝上傳。
- 分類器沒有位置回授，completed 只表示控制時序完成。
- 現有 metadata note 尚未正規化，不能直接作為完整模型特徵。
- 安全稽核仍限制系統只能部署於可信任、隔離的實驗室區網；詳見 [SECURITY_AUDIT_2026-07-11.md](SECURITY_AUDIT_2026-07-11.md)。

## 待實機驗證

- 空軌感測器連續測試與 Echo timeout。
- 健康網路連續自動採集，量測首張照片延遲分布。
- 以高速錄影確認第 2、3 站 ready 前果實已停止。
- 模擬 Wi-Fi 中斷、TLS timeout、手機未上傳、錯站與重複 trigger。
- 驗證未分類資料、刪除復原、分類器斷線與重複 command。
- 建立 Dataset 第二份備份、正式切分、標註與模型驗收。

驗收方法與門檻見 [DATA_COLLECTION_SPEC.md](DATA_COLLECTION_SPEC.md)；實體安全檢查見 [`hardware_notes/硬體接線與驗收摘要.md`](../hardware_notes/硬體接線與驗收摘要.md)。

## 下一個里程碑

先完成 Issue [#1](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/1) 的單顆送料機構設計與實機驗收，再處理 Issue [#2](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/2) 的分類後出料閘門。兩項都必須沿用現行互斥、command ID 與硬體安全邊界。
