@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_firewall.ps1"
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%
