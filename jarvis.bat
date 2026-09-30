@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (
  echo Lance d'abord install.bat.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -c "import webview" 2>nul || (echo Installation de la fenetre de bureau... & pip install pywebview)
python -m jarvis --app %*
pause
