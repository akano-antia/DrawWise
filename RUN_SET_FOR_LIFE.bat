@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 drawwise.py --game set_for_life
) else (
  python drawwise.py --game set_for_life
)
