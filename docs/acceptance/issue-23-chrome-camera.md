# Issue #23：Chrome 新位址與離線拍攝驗收

[Issue #23](https://github.com/Eten0721/PassionFruit_IoT_hardware/issues/23) 的操作工具與記錄範本。行為與通過條件以 [CAPTURE_SPEC：裝置網路連線目標](../CAPTURE_SPEC.md#裝置網路連線目標) 及 [ADR-0018](../adr/0018-local-network-onboarding.md) 為準；本文不改變憑證或網路策略。已觀察結果只保留於 [目前狀態](../CURRENT_STATUS.md)，未完成工作仍由 Issue 追蹤。

## 準備

- 兩台已備妥執行環境的 Windows 電腦、iPhone Chrome、可保留 DHCP 與裝置互通的區網。使用路由器 Wi-Fi 驗證離線，避免切斷手機熱點的行動數據時連區網也消失。
- 沿用現有 HTTPS 設定；不安裝手機信任憑證、不改固定 IPv4、不停用防火牆。若現有操作無法開啟相機，記錄阻礙並回到來源規格決策。
- ESP32 不接電，伺服供電保持切斷。Dashboard 使用「蒐集模式」＋「純拍攝」；不使用送料測試、硬體模式或馬達復原。
- 先確認沒有待處理果實；若有，依 [既有清理程序](../../record_image/硬體接線與驗收摘要.md) 處理，不清空 Dataset、不覆蓋既有照片。
- 在每台電腦記錄 `git rev-parse HEAD` 與 `git status --short`，確認包含本次未提交修改。另一台電腦也須有 Issue #22 的選網與本機 QR 功能。

在已啟用 `PF` 的 PowerShell，從 Repository 根目錄啟動：

```powershell
cd Django_Server
python -s manage.py runsslserver 0.0.0.0:8000 --noreload
```

本機開啟 `https://127.0.0.1:8000/dashboard/`，確認目前 DHCP 介面後，使用頁面產生的相機 QR。命令沿用已備妥的環境；本票不包含環境安裝。

## 每個情境的操作

1. 記錄電腦識別、所選介面／IPv4、相機 URL、iOS 與 Chrome 完整版本。Chrome 版本由應用程式資訊人工補上，`userAgent` 不代替版本核對。
2. 用 iPhone 掃 QR，確認實際開在 Chrome。逐項記錄憑證警告、確認按鈕、重新掃碼或其他額外操作；沒有警告也寫明。頁面無法載入時保留畫面與原始錯誤，後續欄位填「未驗證」。
3. 手機展開「驗收資訊」，按「更新驗收資訊」，長按文字框複製初始紀錄，先收合「驗收資訊」以免遮住按鈕。點「開啟相機」並允許權限，再展開並更新一次；記錄是否詢問權限、已授權而未再詢問、拒絕或錯誤名稱。診斷可在未授權、相機失敗或 RTC 失敗時使用。
4. 電腦選「蒐集模式」＋「純拍攝」，等待手機拍攝端就緒，按「開始執行」。保持手機在前景，讓既有流程依序拍攝三張；記錄本輪 `fruit_id`。
5. 在 Dashboard 開啟三張原圖，並確認 Dataset 的 `temp/<fruit_id>/img_01.jpg`、`img_02.jpg`、`img_03.jpg` 都存在且可開啟。依果實實際狀態人工分類，備註填情境代號；再確認分類目錄中的三張照片，以及 `metadata.csv` 同一 `fruit_id` 的 `capture_count=3`、三個 `station_*_ok`。記錄實際欄位值、檔案大小與時間；不以縮圖或手機「已上傳」取代存檔證據。
6. 獨立觀察電腦 RTC 預覽：在鏡頭前移動物體，確認影像持續更新，記錄失敗原因。即使預覽失敗仍嘗試正式三張保存。手機再更新驗收資訊，保留完成或失敗時的紀錄。
7. 記錄開始、完成時間及證據位置。每輪完成分類後才進入下一情境；失敗的本輪先保留紀錄，再只刪除該輪暫存資料。

Dataset 可由 Dashboard「開啟資料夾」定位。照片與完整診斷記錄存於 Git 外的驗收資料夾，不提交照片、HAR、密碼或完整個人裝置資料。

`isSecureContext=true`、可用的相機 API 與實際相機影格只證明瀏覽器端條件；照片保存、RTC 與外網中斷需各自的證據。`browserOnline` 不證明能連 Internet，也不證明 WAN 已切斷。

## 三個必測情境

| 代號 | 操作差異 | 必留證據 |
|---|---|---|
| A：新 DHCP 位址 | 第一台電腦以 DHCP 取得與原 Demo 不同的 IPv4，重新啟動並確認介面，再掃本次 QR。 | 原 Demo IP、目前 IP、自動取得位址設定、QR 目標、警告與權限、三張保存、RTC。 |
| B：另一台電腦 | 停止第一台 Django，改由第二台已備妥環境的電腦啟動；確認其介面後重新掃碼，重做完整流程。 | 兩台電腦識別與版本、第二台 IP／URL、警告與權限、三張保存、RTC。 |
| C：完全離線 | 先完成上一輪，切斷路由器 WAN 與手機行動數據，保留 Wi-Fi／DHCP／區網互通，排除其他電腦連外介面。重新啟動 Django、關閉舊手機頁面並重新掃 QR 載入，再重做完整流程。 | WAN 切斷與行動數據停用的操作及時間、區網仍互通、重新啟動／載入、三張保存、RTC。 |

離線時在電腦 Chrome DevTools 的 Network 停用快取、保留請求紀錄，重新載入 Dashboard；確認本機頁面、CSS、JavaScript、QR 與 API 請求結果。手機請求由 Django log 輔助確認。切斷外網後仍能重新載入與保存才是離線證據，單憑「Wi-Fi 顯示無 Internet」或沒有看到外部 request 不足以通過。

若需診斷所選網路，可在電腦開啟 `https://127.0.0.1:8000/api/network/info/`，保存當時的 `server_run_id`、`network_revision`、所選介面與相機 URL。此 GET 不領取馬達命令；不要呼叫 ESP32 command、trigger 或 report 作為驗收工具。

## 結果範本

每個情境複製一份到 Git 外的驗收資料夾。結果只用「通過／失敗／未驗證」；尚未執行的欄位保持未驗證。

| 欄位 | 結果／證據 |
|---|---|
| 情境代號、日期、操作員、開始／結束時間 | 未驗證 |
| 電腦識別、commit、未提交檔案版本 | 未驗證 |
| 原 Demo IP、目前 DHCP IPv4、介面、相機 URL | 未驗證 |
| iPhone 型號、iOS、Chrome 完整版本 | 未驗證 |
| 憑證警告／額外確認與操作順序 | 未驗證 |
| isSecureContext、相機 API、權限詢問／結果／錯誤 | 未驗證 |
| 初始／開啟後／完成後診斷紀錄位置 | 未驗證 |
| fruit_id、三張原圖與暫存／分類後檔案證據 | 未驗證 |
| metadata 欄位值與本輪資料列 | 未驗證 |
| 正式照片保存結果與失敗原因 | 未驗證 |
| RTC 動態畫面結果與失敗原因 | 未驗證 |
| WAN／行動數據停用、其他連外路徑、區網互通證據 | 未驗證 |
| 冷啟動／重新載入與本機請求證據 | 未驗證 |
| 原需求之外的必要操作／來源規格待決策事項 | 未驗證 |

## 工具驗證

Repository 根目錄可執行診斷工具的無裝置自我檢查：

```powershell
node Django_Server/fruit_app/camera_diagnostics.test.cjs
```

此檢查涵蓋安全來源不成立、相機已開但 RTC 失敗、過期影格；不請求相機權限、不呼叫 API、不操作馬達。它不代替上述三個實機情境。
