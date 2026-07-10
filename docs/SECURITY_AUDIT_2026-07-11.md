# Django／ESP32 安全與品質稽核

稽核日期：2026-07-11

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
| 中 | `Django==3.2` 已不受上游支援 | 無法取得後續安全修補。 | 規劃升級至受支援的 Django LTS 並回歸測試。 |

Django 官方指出 `3.2` 的延長支援已於 `2024-04` 結束；升級規劃應納入後續安全工作。[Django 4.2 release notes](https://docs.djangoproject.com/en/dev/releases/4.2/)

## 本次明確不處理的項目

依目前決策，本次只建立稽核報告，不改變區網存取、TLS 驗證、Django authentication 或 framework 版本。任何將系統暴露於非可信任網路前，必須先完成上述所有高風險項目並重新執行 `check --deploy`。
