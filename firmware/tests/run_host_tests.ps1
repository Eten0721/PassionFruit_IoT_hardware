$ErrorActionPreference = 'Stop'
$firmwareRoot = Join-Path $PSScriptRoot '..\Three_Gate_Data_Collection'
$buildDir = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$binary = Join-Path $buildDir 'control_network_test.exe'
$sourceDir = Join-Path $buildDir 'src'
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
Get-ChildItem -LiteralPath $firmwareRoot -File | Where-Object { $_.Extension -in '.h', '.cpp' -and $_.Name -notin 'secrets.h', 'network.local.h' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sourceDir }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'host\secrets.example.h') -Destination (Join-Path $sourceDir 'secrets.h')
Set-Content -LiteralPath (Join-Path $sourceDir 'network.local.h') -Value 'const char* manualDjangoOrigin = "https://192.0.2.9:9443";' -Encoding ascii
$sources = Get-ChildItem -LiteralPath $sourceDir -Filter '*.cpp' | ForEach-Object { $_.FullName }
$discoveryBinary = Join-Path $buildDir 'discovery_test.exe'
& g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'discovery_test.cpp') @sources -o $discoveryBinary
if ($LASTEXITCODE -ne 0) { throw 'Discovery harness compilation failed.' }
& $discoveryBinary
if ($LASTEXITCODE -ne 0) { throw 'Discovery harness failed.' }
# Only an ignored build copy isolates the old motion suite from onboarding.
$configPath = [System.IO.Path]::GetFullPath((Join-Path $sourceDir 'Config.h'))
$allowedBuildRoot = [System.IO.Path]::GetFullPath($buildDir) + [System.IO.Path]::DirectorySeparatorChar
if (-not $configPath.StartsWith($allowedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test adapter path.' }
$config = Get-Content -LiteralPath $configPath -Raw
if (-not $config.Contains('kNetworkDiagnosticsOnly = true;')) { throw 'Expected production diagnostic guard.' }
try {
    Set-Content -LiteralPath $configPath -Value $config.Replace('kNetworkDiagnosticsOnly = true;', 'kNetworkDiagnosticsOnly = false;') -Encoding ascii
    & g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'control_network_test.cpp') @sources -o $binary
    if ($LASTEXITCODE -ne 0) { throw 'Host harness compilation failed.' }
    & $binary
    if ($LASTEXITCODE -ne 0) { throw 'Host harness failed.' }
} finally {
    Set-Content -LiteralPath $configPath -Value $config -Encoding ascii
}
