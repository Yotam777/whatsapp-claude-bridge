@echo off
rem Maintainer: release a new version.  release.cmd X.Y.Z "what changed" [--dry-run]
chcp 65001 > nul
cd /d "%~dp0"
set "UV=uv"
where uv >nul 2>&1 || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" run --no-project python -X utf8 tools\release.py %*
