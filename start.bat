@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  pyw -3 app.py
) else (
  start "" pythonw.exe app.py
)
