@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto failed
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name LogoColorStudio --icon assets\logo.ico --add-data "index.html;." --add-data "assets;assets" --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-all resvg_py --collect-all PIL --hidden-import webview.platforms.edgechromium --hidden-import pythoncom --hidden-import pywintypes --collect-submodules win32com app.py
if errorlevel 1 goto failed
echo.
echo Build complete: dist\LogoColorStudio\LogoColorStudio.exe
echo Copy the entire LogoColorStudio folder, not only the EXE.
pause
exit /b 0
:failed
echo.
echo Build failed. Check the error above.
pause
exit /b 1
