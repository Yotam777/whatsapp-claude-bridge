@echo off
rem Setup wizard for the WhatsApp <-> Claude tool. Needs uv (https://docs.astral.sh/uv/) and, for chatting, Claude Code.
chcp 65001 > nul
cd /d "%~dp0"
where uv >nul 2>&1 || (echo uv is required: powershell -c "irm https://astral.sh/uv/install.ps1 | iex" & exit /b 1)
uv run --no-project python -X utf8 tools\setup.py
pause
