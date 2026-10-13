# Issue #29 資料分區驗收

契約唯一來源：[CAPTURE_SPEC.md：資料分區](../CAPTURE_SPEC.md#資料分區)。本文件記錄驗證與人工操作準備，不維護另一份規格。

## 自動化驗證

`fruit_app.test_dataset_modes` 使用獨立 TemporaryDirectory，驗證兩模式同編號的照片與結果、counter、刪除、重置、重啟、舊頁面或缺少模式的請求、模式保存失敗、紀錄損壞時拒絕拍攝與上傳，以及 Windows 占用。模型邊界使用既有測試產物，沒有對正式 Dataset 執行清理。

2026-10-14 於 `PF` 環境執行 `python -s manage.py test fruit_app`：202 項全數通過（含本分區 12 項，另涵蓋模式紀錄損壞後被其他復原鎖覆蓋仍禁止資料變更）。本 Issue 不改動 Firmware，未重新編譯 ESP32。人工驗收仍未執行。

## 已準備的空白資料區

本機已準備 ignored `firmware/tests/build/issue29_acceptance/`，內有 `start.py`；資料與 runtime 設定都限制在該目錄，初始使用蒐集＋純拍攝。正式模型沿用現有部署權重。此啟動檔是本機驗收產物，不納入 Git。

先停止一般測試伺服器，避免同網路存在兩個控制端。由 Repository 根目錄執行：

```powershell
C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s firmware\tests\build\issue29_acceptance\start.py
```

開啟 `https://localhost:8443/dashboard/`，確認本次網路，再以更新的 QR Code 連接相機手機。驗收全程維持「純拍攝」。每輪拍攝前，確認資料管理顯示 `issue29_acceptance\dataset\collection` 或 `issue29_acceptance\dataset\detection`；若不符，停止驗收。

## 操作員紀錄

| 步驟 | 檢查方式 | 預期 | 實際結果 |
|---|---|---|---|
| 蒐集一輪 | 拍攝三張，選「上等」，開啟資料位置 | `collection/上等/fruit_001/` 有三張照片，下一筆為 `fruit_002` | 未執行 |
| 檢測一輪 | 切換自動檢測，拍攝三張，等待結果，開啟資料位置 | `detection/results/fruit_001/` 有原圖、成功產物及結果；編號獨立 | 未執行 |
| 只刪檢測 | 刪除剛完成的檢測，再回蒐集資料區 | 檢測暫存與結果移除；蒐集照片、metadata、下一筆編號保留 | 未執行 |
| 重置與重啟 | 切到檢測，只重置測試區，停止並重新執行相同 `start.py` | 重啟仍是檢測模式，下一筆 `fruit_001`；蒐集維持 `fruit_002`，正式舊資料原位保留 | 未執行 |
| 處理中切換 | 拍攝中或推論中嘗試切換模式 | UI 停用並保留阻擋原因；API 回 `409` | 未執行 |

軟體回歸與占用模擬不能代替上述瀏覽器、手機與人工觀察。實際結果由操作員填寫，不預先勾選通過。
