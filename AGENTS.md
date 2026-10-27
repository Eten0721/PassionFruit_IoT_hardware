## 文件導覽

開始修改前，依工作範圍閱讀：

1. `docs/PROJECT_CONTEXT.md`：架構、Repository 邊界與核心原則。
2. `docs/CAPTURE_SPEC.md`：硬體與純拍攝流程、檢測、分類、API、command、GPIO、角度與 timing。
3. `docs/CURRENT_STATUS.md`：目前穩定功能、問題、實機驗證與里程碑。
4. `docs/adr/`：與修改範圍相關的架構決策。
5. `record_image/電源供應器接線注意事項.md`：接線安全、上電前檢查與維修。

未完成工作以 GitHub Issues 為準，不在本文件維護待辦或版本進度。

## Repository 邊界

- 硬體、Firmware、Django 本地部署推論與 `Django_Server/models/` 的正式部署模型由本 Repository 維護；部署與權重替換不需要外部 checkout 或資料。
- 模型訓練與研究推論 pipeline 位於選用的獨立 checkout `D:\ps-quality-detection-system\`，不放在本 Repository 內；見 `docs/模型開發環境.md`。
- 兩個 Repository 分別 commit 與 push；父層不得追蹤 child Repository、照片、訓練輸出或歷史 checkpoint。
- Dataset 實體位於獨立資料目錄，不納入 Git。

## Git 安全

- 修改前執行 `git status`；大幅修改前先說明檔案與策略。
- 修改後先顯示 `git diff` 或變更摘要，不直接 commit。
- commit 前檢查 `git diff --cached --name-only` 與 `git diff --cached`，並明確指定 `git add <檔案>`。
- 不提交 `secrets.h`、`.env`、Wi-Fi 密碼、API key、原始照片、`runs/`、`last.pt`、歷史 checkpoint 或大型訓練輸出。
- 通過驗收的正式部署模型固定命名為 `ROI.pt`、`Color.pt`、`Wrinkle.pt`、`Defect.pt`、`GradeStage1.json`、`GradeStage2.json`，只可存於 `Django_Server/models/` 並以 Git LFS 管理。
- 不使用 `git reset --hard`，除非使用者明確要求；回溯優先使用 `git revert` 或恢復指定檔案。
- 保留工作樹中與任務無關的既有修改。

## 版本與 commit 格式

- 每個 commit 標題都使用 `vX.Y.Z, 中文說明`，例如 `v1.2.16, 新增版本與 commit 格式規則`；同一個 PR 的 commit 都用該 PR 的版本號，PR 標題用同一字串。Squash 合併時，只有一個 commit 的 PR 沿用 commit 標題，多個 commit 則沿用 PR 標題，合併時確認 squash 標題一致。
- 版本號以 `main` 上最新帶版本號的 commit 為基準，每合併一個 PR 遞增一次：一般變更加第三碼；要發布 Release 的重大改版加第二碼並將第三碼歸零（`v1.2.x` → `v1.3.0`）。同時開啟多個 PR 時，以合併順序為準，合併前更新 PR 與 squash 標題。
- GitHub Release 的 tag 與其對應 squash commit 的版本號相同，發布步驟見 [部署說明 9.2](docs/環境部署說明文件.md#92-發布-release)。

## 驗證要求

Firmware 或 Django 狀態機變更後至少執行：

```powershell
cd Django_Server
C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s manage.py test fruit_app
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
- 設計或重構 module 時，遵循 `codebase-design` 的深模組原則：小介面、隱藏複雜度，避免 pass-through module。

## 硬體與資源安全

- camera、serial port、GPIO、motor controller 與 file 必須可靠釋放。
- early return 不得跳過 cleanup；優先使用 context manager 或 `try/finally`。
- 硬體狀態異常時，以安全停止為優先。
- 硬體電壓、外部供電與共地要求不得以軟體容錯取代。

## 文件維護

- 優先重寫、合併與刪除，不預設在檔尾追加。
- 一項事實只保留一個 authoritative source；其他文件只放摘要與連結，不為「保持同步」複製內容。
- `AGENTS.md` 只保存長期規則；`PROJECT_CONTEXT.md` 只保存架構與責任邊界。
- `CAPTURE_SPEC.md` 是拍攝、檢測、分類流程、硬體參數與協定的唯一規格；`CURRENT_STATUS.md` 是可覆寫快照。
- 機構決策寫入相關 ADR，演進歷史由 Git 保存；接線安全與維修檢查寫入 `電源供應器接線注意事項.md`。
- 未完成工作用 GitHub Issues；完成歷史由 Git 保存；架構決策使用獨立 ADR。
- 文件超過既定篇幅時，先精簡再加入內容。

## Agent skills

### Issue tracker

GitHub Issues 只追蹤自 `2026-07-26` 起尚未完成的工作。See `docs/agents/issue-tracker.md`.

### Triage labels

使用預設五個 triage labels。See `docs/agents/triage-labels.md`.

### Domain docs

採 single-context 文件配置。See `docs/agents/domain.md`.
