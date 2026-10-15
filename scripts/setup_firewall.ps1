$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        $elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    finally { $identity.Dispose() }
    if (-not $elevated) {
        Write-Host '設定防火牆需要系統管理員權限，請在 Windows 提示中允許。'
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`""
        exit $process.ExitCode
    }

    $profiles = @(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne 'Disconnected' })
    $profiles | Select-Object InterfaceAlias, Name, NetworkCategory | Format-Table -AutoSize | Out-Host
    foreach ($connection in $profiles) {
        if ($connection.NetworkCategory -ne 'Public') { continue }
        Write-Host "網路「$($connection.Name)」（$($connection.InterfaceAlias)）目前為公用網路。"
        Write-Host '只有在可信任的實驗室網路才適合改為私人網路；請勿在公共 Wi-Fi 使用。'
        $answer = Read-Host '要將此網路改為私人網路嗎？（Y/N）'
        if ($answer -ine 'Y') {
            Write-Host '已略過此網路，私人網路防火牆規則不會套用到公用網路。'
            continue
        }
        Set-NetConnectionProfile -InterfaceIndex $connection.InterfaceIndex -NetworkCategory Private
    }
    if (-not (Get-NetFirewallRule -Name 'PassionFruit-Django-8000' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'PassionFruit-Django-8000' -DisplayName 'PassionFruit Django HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
    if (-not (Get-NetFirewallRule -Name 'PassionFruit-mDNS-5353' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'PassionFruit-mDNS-5353' -DisplayName 'PassionFruit mDNS' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5353 -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }
    Write-Host '設定完成：允許私人網路的同一子網路連入 HTTPS TCP 8000 與 mDNS UDP 5353。'
    Write-Host '公用網路不會開放這兩條規則。設定私人網路後，請重新雙擊「啟動系統.bat」。'
}
catch {
    Write-Host "防火牆設定失敗：$($_.Exception.Message)"
    Read-Host '按 Enter 關閉' | Out-Null
    exit 1
}
Read-Host '按 Enter 關閉' | Out-Null
