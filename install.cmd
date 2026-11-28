@echo off
rem LLM Wiki installer for Windows: runs install.ps1 with PowerShell.
rem -ExecutionPolicy Bypass runs install.ps1 without changing the settings of
rem Windows. The full explanation is in INSTALLER.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
