# ADR-0021：雙擊啟動器與 Release ZIP 散布

- Status: Accepted
- Date: 2026-10-16

## Context

非資工背景的使用者從 GitHub 取得專案後，不會自行安裝 Python `3.14.4`、建立 conda 環境、選擇 CUDA 或 CPU 版 PyTorch，也不會手動輸入 `runsslserver`。Demo 需要能交給組員的 Windows 筆電從零安裝。曾評估 Docker：容器內看不到實體網卡與區網 IP，mDNS 群播不易穿出 Docker Desktop 的虛擬機，會破壞 [ADR-0018](0018-local-network-onboarding.md) 的網路選擇、相機 QR 與 ESP32 探索；安裝 Docker Desktop（WSL2、虛擬化、數 GB 映像）對目標使用者也比 conda 更困難，且無法省去 macOS 相容所需的程式修改。

## Decision

採「雙擊啟動器 + conda `PF` 環境 + GitHub Release ZIP」，不採用 Docker。

- 入口 `啟動系統.bat` 只呼叫 PowerShell；PowerShell 只負責找到或靜默安裝 Miniforge、建立 `PF`。其餘邏輯集中在只依賴標準函式庫的 `scripts/launch.py`，讓將來支援其他平台時只需新增薄入口。
- 環境名稱與開發者共用 `PF`，避免重複安裝數 GB 套件，也讓 AGENTS.md 的測試指令保持不變。
- 依賴是否過期以 stamp 摘要判斷（Python 版本、PyTorch 類型與兩份 requirements），日常啟動不需網路；PyTorch 版本集中於 `Django_Server/requirements-torch.txt`，並以 `nvidia-smi` 決定 CUDA `12.8` 或 CPU 版。
- 防火牆屬系統安全設定，獨立為需要 UAC 的 `首次設定防火牆.bat`，不在每次啟動時修改。
- 散布以 GitHub 「Include Git LFS objects in archives」讓 Release／Download ZIP 含真實模型，不另建打包 workflow。

## Consequences

一般使用者不需要 Git、Git LFS 或命令列；首次安裝仍需網路。ZIP 安裝沒有 `git pull`，更新時須手動搬移 `Django_Server/dataset/` 與 `runtime_config/`；將資料移出專案目錄屬資料分區規格變更，另行決定。ZIP 下載會消耗 Git LFS 頻寬額度。

目前只支援 Windows。macOS 仍須將網卡清單改為不依賴 PowerShell、內建中文字型並新增 `.command` 入口，另以 Issue 追蹤。Miniforge 自動安裝與從零建立 `PF` 的流程須在乾淨電腦實測，開發機驗證只涵蓋既有環境路徑。
