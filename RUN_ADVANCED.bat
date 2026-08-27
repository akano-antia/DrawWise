@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 drawwise.py --advanced
) else (
  python drawwise.py --advanced
)
