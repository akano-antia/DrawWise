@echo off
cd /d "%~dp0"
py -m unittest discover -s tests -v
if errorlevel 1 python -m unittest discover -s tests -v
pause
