@echo off
rem Keeps the WhatsApp agent running: restarts it 15 s after any exit. Started at logon by Task Scheduler.
cd /d "%~dp0agent"
set "UV=uv"
where uv >nul 2>&1 || set "UV=%USERPROFILE%\.local\bin\uv.exe"
:loop
"%UV%" run -q --no-sync python -X utf8 agent.py >> agent.out 2>&1
timeout /t 15 /nobreak > nul
goto loop
