@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install -r requirements.txt
) else (
  python -m pip install -r requirements.txt
)
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Check your internet connection and Python installation.
) else (
  echo.
  echo Setup complete. You can now run start.bat.
)
pause
