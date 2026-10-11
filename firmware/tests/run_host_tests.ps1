$ErrorActionPreference = 'Stop'
$firmwareRoot = Join-Path $PSScriptRoot '..\Three_Gate_Data_Collection'
$buildDir = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$binary = Join-Path $buildDir 'control_network_test.exe'
$sourceDir = Join-Path $buildDir 'src'
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
Get-ChildItem -LiteralPath $firmwareRoot -File | Where-Object { $_.Extension -in '.h', '.cpp' -and $_.Name -ne 'secrets.h' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sourceDir }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'host\secrets.example.h') -Destination (Join-Path $sourceDir 'secrets.h')
$sources = Get-ChildItem -LiteralPath $sourceDir -Filter '*.cpp' | ForEach-Object { $_.FullName }
& g++ -std=c++17 -Wall -Wextra -Werror -Wno-sign-compare -Wno-unused-parameter -pthread "-I$PSScriptRoot\host" "-I$sourceDir" (Join-Path $PSScriptRoot 'control_network_test.cpp') @sources -o $binary
if ($LASTEXITCODE -ne 0) { throw 'Host harness compilation failed.' }
& $binary
if ($LASTEXITCODE -ne 0) { throw 'Host harness failed.' }
