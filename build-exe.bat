@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onefile --name LogoColorStudio --add-data "index.html;." --add-data "samples;samples" --collect-all resvg_py --collect-all PIL --hidden-import pythoncom --hidden-import pywintypes --collect-submodules win32com app.py
if errorlevel 1 (
  echo Build failed. See README_FA.txt for requirements.
  pause
) else (
  echo Created dist\LogoColorStudio.exe
  pause
)
