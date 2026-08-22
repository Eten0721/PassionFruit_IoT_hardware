# Django／ESP32 安全與品質稽核

稽核日期：2026-07-11

## 2026-08-22 文件狀態

目前 `Django_Server/requirements.txt` 已固定使用 Django `5.2.16` LTS，因此下方 2026-07-11 原始表格中的 `Django==3.2` 風險已不再是現況。TLS 憑證驗證、API authentication、production settings、request／檔案大小限制與 JPEG 驗證仍未完成，本文件的可信任隔離區網限制繼續有效。

## 2026-07-14 追蹤狀態

此段為 `v1.2.3` 的後續追蹤，不改寫下方 `2026-07-11` 原始稽核結論。

- Django 已透過 `python-dotenv` 載入 repository 根目錄的 `.env`，本機使用隨機 `DJANGO_SECRET_KEY`，實際 `.env` 由 `.gitignore` 排除。這只改善本機密鑰管理；若 `.env` 缺失，程式仍會使用 development fallback key，且 `DEBUG=True`、`ALLOWED_HOSTS=['*']` 尚未修補，因此原高風險項目仍未關閉。
- ESP32 仍使用 `WiFiClientSecure::setInsecure()`；Django 多個寫入 API 仍為 `@csrf_exempt` 且沒有裝置或使用者驗證。新增的人工分類、sorter report、timing 與 reset 路徑也屬於相同的可信任區網假設，沒有因 `v1.2.3` 自動獲得保護。
- MG996R 整合後，正式 `esp32:esp32:esp32` sketch 編譯結果約為 Flash `82%`、RAM `15%`。Flash 餘裕降低，後續功能應避免加入重複 library、大型字串或第二套 JSON 實作。
- `Necessary_library/README.md` 已將正式依賴收斂為 ESP32 board package 與 `ESP32Servo 3.2.1`；舊 library、Node.js ZIP、SQL 與編譯產物不推送至 GitHub，降低誤用舊元件與 repository 膨脹風險。

上述變更當時尚未完成正式 deployment hardening。Django 後續已升級至 `5.2.16` LTS；任何對外網路部署前，仍必須完成 TLS 憑證驗證、API authentication、production settings、request／檔案大小限制與 JPEG 驗證，並重新執行安全稽核。

## 範圍與已完成檢查

- 檢閱 `Django_Server/fruit_app/`、Django settings 與正式三閘門 firmware。
- 執行 Django 測試、`manage.py check --deploy`、Dashboard JavaScript syntax check、`git diff --check` 與 `esp32:esp32:esp32` sketch 編譯。
- 正式 sketch 編譯成功：Flash 使用約 `81%`、RAM 使用約 `15%`。

## 結論

三站流程的狀態、站點／command 配對、照片原子保存與檔案路徑驗證可支援目前資料採集用途；但目前安全模型只適用可信任且隔離的實驗室區網。此版本不可宣稱為無漏洞，也不可直接公開到網際網路。

## 未修補風險

| 等級 | 項目 | 影響 | 建議後續處理 |
| --- | --- | --- | --- |
| 高 | ESP32 `WiFiClientSecure` 使用 `setInsecure()` | 可遭區網中間人偽造 Django 命令或回報。 | 在 `secrets.h` 提供受信任 CA／伺服器憑證並以 `setCACert()` 驗證。 |
| 高 | 多個 Django 寫入 API 使用 `@csrf_exempt` 且沒有驗證 | 區網其他裝置可觸發、上傳、分類、刪除或重置資料。 | 建立 ESP32 API token、Dashboard／相機登入或同等存取控制。 |
| 高 | `DEBUG=True`、`ALLOWED_HOSTS=['*']`、開發用預設 `SECRET_KEY` | 不適合部署，錯誤頁與 session 安全性不足。 | 以環境變數管理正式設定，關閉 debug、限制 host、設定隨機 secret。 |
| 中 | 上傳圖片與 WebRTC ICE 清單沒有明確上限 | 惡意或故障 client 可耗盡記憶體與磁碟。 | 限制 request／檔案大小、驗證 JPEG，並限制 ICE 清單長度。 |
| 中 | `Django==3.2` 已不受上游支援 | 無法取得後續安全修補。 | 此項為原始稽核紀錄；目前已升級至 Django `5.2.16` LTS。 |

Django 官方指出 `3.2` 的延長支援已於 `2024-04` 結束；本專案後續已完成 framework 升級，原始風險保留供稽核追溯。[Django 4.2 release notes](https://docs.djangoproject.com/en/dev/releases/4.2/)

## 本次明確不處理的項目

依目前決策，本次只建立稽核報告，不改變區網存取、TLS 驗證、Django authentication 或 framework 版本。任何將系統暴露於非可信任網路前，必須先完成上述所有高風險項目並重新執行 `check --deploy`。
