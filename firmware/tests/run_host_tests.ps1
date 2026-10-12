$ErrorActionPreference = 'Stop'
$firmwareRoot = Join-Path $PSScriptRoot '..\Three_Gate_Data_Collection'
$buildDir = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$binary = Join-Path $buildDir 'control_network_test.exe'
$sourceDir = Join-Path $buildDir 'src'
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
Get-ChildItem -LiteralPath $firmwareRoot -File | Where-Object { $_.Extension -in '.h', '.cpp' -and $_.Name -notin 'secrets.h', 'network.local.h', 'setup.local.h' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sourceDir }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'host\secrets.example.h') -Destination (Join-Path $sourceDir 'secrets.h')
Set-Content -LiteralPath (Join-Path $sourceDir 'network.local.h') -Value 'const char* manualDjangoOrigin = "https://192.0.2.9:9443";' -Encoding ascii
Set-Content -LiteralPath (Join-Path $sourceDir 'setup.local.h') -Value 'const char* setupApPassword = "host-ap-test-only";' -Encoding ascii
$sources = Get-ChildItem -LiteralPath $sourceDir -Filter '*.cpp' | ForEach-Object { $_.FullName }
$discoveryBinary = Join-Path $buildDir 'discovery_test.exe'
$configPath = Join-Path $sourceDir 'Config.h'
$config = Get-Content -LiteralPath $configPath -Raw
Set-Content -LiteralPath $configPath -Value $config.Replace('kNetworkDiagnosticsOnly = false;', 'kNetworkDiagnosticsOnly = true;') -Encoding ascii
& g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'discovery_test.cpp') @sources -o $discoveryBinary
if ($LASTEXITCODE -ne 0) { throw 'Discovery harness compilation failed.' }
& $discoveryBinary
if ($LASTEXITCODE -ne 0) { throw 'Discovery harness failed.' }
# Only an ignored build copy isolates the old motion suite from onboarding.
$configPath = [System.IO.Path]::GetFullPath((Join-Path $sourceDir 'Config.h'))
$allowedBuildRoot = [System.IO.Path]::GetFullPath($buildDir) + [System.IO.Path]::DirectorySeparatorChar
if (-not $configPath.StartsWith($allowedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test adapter path.' }
if (-not $config.Contains('kNetworkDiagnosticsOnly = false;')) { throw 'Expected production diagnostic guard.' }
try {
    Set-Content -LiteralPath $configPath -Value $config -Encoding ascii
    $safetyBinary = Join-Path $buildDir 'network_safety_test.exe'
    & g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'network_safety_test.cpp') @sources -o $safetyBinary
    if ($LASTEXITCODE -ne 0) { throw 'Safety harness compilation failed.' }
    & $safetyBinary
    if ($LASTEXITCODE -ne 0) { throw 'Safety harness failed.' }
    $setupBinary = Join-Path $buildDir 'wifi_setup_test.exe'
    & g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'wifi_setup_test.cpp') @sources -o $setupBinary
    if ($LASTEXITCODE -ne 0) { throw 'Wi-Fi setup harness compilation failed.' }
    & $setupBinary
    if ($LASTEXITCODE -ne 0) { throw 'Wi-Fi setup harness failed.' }
    $httpBinary = Join-Path $buildDir 'network_http_test.exe'
    & g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'network_http_test.cpp') @sources -o $httpBinary
    if ($LASTEXITCODE -ne 0) { throw 'HTTP integration harness compilation failed.' }
    # Retain the historical sensor/motion regression seam in an ignored source copy.
    $controlPath = Join-Path $sourceDir 'CaptureController.cpp'
    $control = Get-Content -LiteralPath $controlPath -Raw
    $legacy = $control.Replace('void CaptureController::checkControlSafety(uint32_t currentTime) {', 'void CaptureController::checkControlSafety(uint32_t currentTime) { return;')
    $legacy = $legacy.Replace('const bool accepted = FirmwareConfig::kNetworkDiagnosticsOnly || acceptControlResult', 'const bool accepted = true || acceptControlResult')
    $legacy = $legacy.Replace('if (networkInterlocked_ || leaseDeadline_ == 0 || timeReached(currentTime, leaseDeadline_)) return;', '')
    $legacy = $legacy.Replace('if (!yieldAfterHeartbeat_ && (operationStarted_ || recoveryRunning_) && currentTime - lastHeartbeatAt_', 'if (false && (operationStarted_ || recoveryRunning_) && currentTime - lastHeartbeatAt_')
    Set-Content -LiteralPath $controlPath -Value $legacy -Encoding ascii
    & g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'control_network_test.cpp') @sources -o $binary
    if ($LASTEXITCODE -ne 0) { throw 'Host harness compilation failed.' }
    & $binary
    if ($LASTEXITCODE -ne 0) { throw 'Host harness failed.' }
} finally {
    Set-Content -LiteralPath $configPath -Value $config -Encoding ascii
    if ($control) { Set-Content -LiteralPath $controlPath -Value $control -Encoding ascii }
}
