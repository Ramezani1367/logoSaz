@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 app.py
) else (
  python app.py
)
if errorlevel 1 (
  echo.
  echo برنامه با خطا متوقف شد. راهنمای README_FA.txt را ببینید.
  pause
)
