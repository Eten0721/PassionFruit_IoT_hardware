## 文件導覽

開始修改前，依工作範圍閱讀：

1. `docs/PROJECT_CONTEXT.md`：架構、Repository 邊界與核心原則。
2. `docs/DATA_COLLECTION_SPEC.md`：三站流程、API、command、GPIO、角度與 timing。
3. `docs/CURRENT_STATUS.md`：目前穩定功能、問題、實機驗證與里程碑。
4. `docs/adr/`：與修改範圍相關的架構決策。
5. `docs/HARDWARE_DEVELOPMENT_REPORT.md` 與 `hardware_notes/硬體接線與驗收摘要.md`：機構、接線或維修工作。

未完成工作以 GitHub Issues 為準，不在本文件維護待辦或版本進度。

## Repository 邊界

- 硬體、Firmware 與 Django 由本 Repository 維護。
- 模型訓練與檢測層位於 `external/ps-quality-detection-system/`，保留獨立 `.git`。
- 兩個 Repository 分別 commit 與 push；父層不得追蹤 child Repository、照片、模型輸出或權重。
- Dataset 實體位於獨立資料目錄，不納入 Git。

## Git 安全

- 修改前執行 `git status`；大幅修改前先說明檔案與策略。
- 修改後先顯示 `git diff` 或變更摘要，不直接 commit。
- commit 前檢查 `git diff --cached --name-only` 與 `git diff --cached`，並明確指定 `git add <檔案>`。
- 不提交 `secrets.h`、`.env`、Wi-Fi 密碼、API key、原始照片、`runs/`、`last.pt`、歷史 checkpoint 或大型訓練輸出。
- 正式模型權重只可在模型 Repository 通過驗收後以 Git LFS 管理。
- 不使用 `git reset --hard`，除非使用者明確要求；回溯優先使用 `git revert` 或恢復指定檔案。
- 保留工作樹中與任務無關的既有修改。

## 驗證要求

Firmware 或 Django 狀態機變更後至少執行：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\pf_iot_env\python.exe manage.py test fruit_app
```

另需編譯目前 ESP32 板型並執行 `git diff --check`。不要用系統預設 Python 執行 Django 測試。

只修改文件時，驗證行數、相對連結、唯一來源與 `git diff --check`；不必執行 Django 測試或 Firmware 編譯。

## 程式風格

- 優先使用 guard clauses／early returns，避免過深的 nested `if/else`。
- 保持主要成功流程扁平、清楚、可測試；函式短小且責任單一。
- 無效參數、設定錯誤或必要路徑不存在時丟出明確 exception。
- 相機 unavailable、capture failed、fruit not detected、low confidence、motor failed 等預期問題使用結構化結果，例如 `{ "ok": false, "reason": "camera_unavailable" }`。
- 錯誤原因必須具體，不只回傳 `False` 或 `None`。
- 優先擴充現有模組與標準函式庫，不為單一用途新增抽象或 dependency。

## 硬體與資源安全

- camera、serial port、GPIO、motor controller 與 file 必須可靠釋放。
- early return 不得跳過 cleanup；優先使用 context manager 或 `try/finally`。
- 硬體狀態異常時，以安全停止為優先。
- 硬體電壓、外部供電與共地要求不得以軟體容錯取代。

## 文件維護

- 優先重寫、合併與刪除，不預設在檔尾追加。
- 一項事實只保留一個 authoritative source；其他文件只放摘要與連結，不為「保持同步」複製內容。
- `AGENTS.md` 只保存長期規則；`PROJECT_CONTEXT.md` 只保存架構與責任邊界。
- `DATA_COLLECTION_SPEC.md` 是採集流程、硬體參數與協定的唯一規格；`CURRENT_STATUS.md` 是可覆寫快照。
- 機構演進寫入 `HARDWARE_DEVELOPMENT_REPORT.md`；接線安全寫入 `硬體接線與驗收摘要.md`。
- 未完成工作用 GitHub Issues；完成歷史由 Git 保存；架構決策使用獨立 ADR。
- 文件超過既定篇幅時，先精簡再加入內容。

## Agent skills

### Issue tracker

GitHub Issues 只追蹤自 `2026-07-26` 起尚未完成的工作。See `docs/agents/issue-tracker.md`.

### Triage labels

使用預設五個 triage labels。See `docs/agents/triage-labels.md`.

### Domain docs

採 single-context 文件配置。See `docs/agents/domain.md`.
