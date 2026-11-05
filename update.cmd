@echo off
rem Update the WhatsApp <-> Claude tool to the latest release (automatic rollback if the self-test fails).
chcp 65001 > nul
cd /d "%~dp0"
set "UV=uv"
where uv >nul 2>&1 || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" run --no-project python -X utf8 tools\update.py %*
