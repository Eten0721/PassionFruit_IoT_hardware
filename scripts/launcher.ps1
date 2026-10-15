$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$repoRoot = Split-Path -Parent $PSScriptRoot
$logFile = Join-Path $repoRoot 'logs\launcher.log'

function Write-ProgressLog([string]$Message) {
    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Host $Message
}

function Resolve-CondaExecutable([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    if ((Split-Path -Leaf $Path) -ieq 'conda.exe') {
        return (Get-Item -LiteralPath $Path).FullName
    }
    if ((Split-Path -Leaf $Path) -ine 'conda.bat') { return $null }
    $root = Split-Path -Parent (Split-Path -Parent $Path)
    $executable = Join-Path $root 'Scripts\conda.exe'
    if (Test-Path -LiteralPath $executable -PathType Leaf) { return $executable }
    return $null
}

function Find-Conda {
    $found = Resolve-CondaExecutable $env:CONDA_EXE
    if ($found) { return $found }
    foreach ($name in @('conda.exe', 'conda')) {
        foreach ($command in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
            $found = Resolve-CondaExecutable $command.Path
            if ($found) { return $found }
        }
    }
    $roots = @(
        @($env:USERPROFILE, 'miniforge3'), @($env:USERPROFILE, 'anaconda3'),
        @($env:USERPROFILE, 'miniconda3'), @($env:LOCALAPPDATA, 'miniforge3'),
        @($env:ProgramData, 'miniforge3'), @($env:ProgramData, 'anaconda3'),
        @($env:ProgramData, 'miniconda3'), @("$env:SystemDrive\", 'miniforge3')
    )
    foreach ($candidate in $roots) {
        if (-not $candidate[0]) { continue }
        $root = Join-Path $candidate[0] $candidate[1]
        $found = Resolve-CondaExecutable (Join-Path $root 'Scripts\conda.exe')
        if ($found) { return $found }
    }
    return $null
}

function Get-MiniforgeRoot {
    # conda 不支援含空白或非 ASCII 字元的路徑；中文使用者名稱改裝到系統磁碟根目錄。
    if ($env:USERPROFILE -and $env:USERPROFILE -match '^[\x21-\x7E]+$') {
        return Join-Path $env:USERPROFILE 'miniforge3'
    }
    return Join-Path "$env:SystemDrive\" 'miniforge3'
}

function Install-Miniforge {
    $installRoot = Get-MiniforgeRoot
    $installer = Join-Path $env:TEMP "PF-Miniforge-$([guid]::NewGuid().ToString('N')).exe"
    $checksumFile = "$installer.sha256"
    $downloadUrl = 'https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe'
    try {
        Write-ProgressLog '找不到 conda，將為目前使用者下載約 90 MB 的 Miniforge，需要網際網路。'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $installer
        Write-ProgressLog '下載 Miniforge SHA-256 校驗檔……'
        Invoke-WebRequest -UseBasicParsing -Uri "$downloadUrl.sha256" -OutFile $checksumFile
        Write-ProgressLog '驗證 Miniforge SHA-256……'
        $checksumText = Get-Content -LiteralPath $checksumFile -Raw
        if ($checksumText -notmatch '^\s*([a-fA-F0-9]{64})(?:\s|$)') {
            throw 'miniforge_checksum_invalid：校驗檔格式錯誤，請稍後重試。'
        }
        $expected = $Matches[1]
        $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        if ($actual -ine $expected) {
            throw 'miniforge_checksum_mismatch：安裝檔 SHA-256 不符，已停止安裝，請重新啟動下載。'
        }
        Write-ProgressLog "安裝 Miniforge 到 $installRoot……"
        # NSIS 的 /D 必須最後傳入，且不可加引號，包含空白的目錄亦同。
        $process = Start-Process -Wait -PassThru -WindowStyle Hidden -FilePath $installer -ArgumentList '/InstallationType=JustMe','/RegisterPython=0','/AddToPath=0','/S',"/D=$installRoot"
        if ($process.ExitCode -ne 0) {
            throw "miniforge_install_failed：安裝程式結束碼 $($process.ExitCode)，請團隊協助檢查使用者目錄與安裝紀錄。"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'Scripts\conda.exe') -PathType Leaf)) {
            throw "miniforge_install_path_missing：安裝後找不到 $installRoot\Scripts\conda.exe，請團隊協助確認目錄字元與安裝結果。"
        }
        Write-ProgressLog 'Miniforge 安裝完成。'
    }
    finally {
        foreach ($path in @($installer, $checksumFile)) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force
            }
        }
    }
}

function Find-PFEnvironment([string]$Conda) {
    $json = & $Conda env list --json
    if ($LASTEXITCODE -ne 0) { throw "conda_env_list_failed：無法列出環境，結束碼 $LASTEXITCODE。" }
    $environments = ($json -join "`n") | ConvertFrom-Json
    foreach ($path in $environments.envs) {
        if ((Split-Path -Leaf $path) -ieq 'PF') { return $path }
    }
    return $null
}

try {
    New-Item -ItemType Directory -Path (Join-Path $repoRoot 'logs') -Force | Out-Null
    Write-ProgressLog '尋找 conda……'
    $conda = Find-Conda
    if (-not $conda) {
        Install-Miniforge
        Write-ProgressLog '重新尋找 conda……'
        $conda = Find-Conda
    }
    if (-not $conda) { throw 'conda_not_found：安裝後仍找不到 conda.exe，請將紀錄交給團隊。' }
    Write-ProgressLog "使用 conda：$conda"
    Write-ProgressLog '尋找 PF 環境……'
    $pfPrefix = Find-PFEnvironment $conda
    if (-not $pfPrefix) {
        Write-ProgressLog '建立 PF 環境，安裝 Python 3.14.4 與 pip，需要網際網路……'
        & $conda create -n PF -c conda-forge --override-channels python=3.14.4 pip -y
        if ($LASTEXITCODE -ne 0) { throw "conda_create_failed：建立 PF 失敗，結束碼 $LASTEXITCODE。" }
        $pfPrefix = Find-PFEnvironment $conda
    }
    if (-not $pfPrefix) { throw 'pf_environment_missing：建立後找不到 PF，請將紀錄交給團隊。' }
    Write-ProgressLog "設定 PF 執行環境：$pfPrefix"
    $python = Join-Path $pfPrefix 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "pf_python_missing：找不到 $python，請團隊協助修復 PF 環境。"
    }
    $paths = @($pfPrefix)
    foreach ($suffix in @('Library\mingw-w64\bin', 'Library\usr\bin', 'Library\bin', 'Scripts', 'bin')) {
        $paths += Join-Path $pfPrefix $suffix
    }
    $env:PATH = ($paths -join ';') + ';' + $env:PATH
    $env:CONDA_PREFIX = $pfPrefix
    $env:CONDA_DEFAULT_ENV = 'PF'
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Write-ProgressLog '執行啟動前檢查與系統啟動……'
    & $python -I (Join-Path $repoRoot 'scripts\launch.py')
    $code = $LASTEXITCODE
    Write-ProgressLog "啟動器結束，結束碼：$code"
    exit $code
}
catch {
    $message = $_.Exception.Message
    try { Write-ProgressLog "啟動失敗：$message" }
    catch { Write-Host "啟動失敗：$message（無法寫入紀錄檔）" }
    Write-Host "請確認錯誤後重試，或將紀錄交給團隊。紀錄檔：$logFile"
    exit 1
}
