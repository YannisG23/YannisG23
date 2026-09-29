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

rem Il faut un vrai claude.exe : le kit de Claude refuse le script claude.cmd installe par npm.
python -c "from jarvis.brain_subscription import find_claude_cli as f; import sys; sys.exit(0 if f() else 1)" >nul 2>nul
if errorlevel 1 (
  echo Installation de Claude Code pour Windows, necessaire pour parler a Claude...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
  echo.
)

echo Connexion a ton compte Claude : une page va s'ouvrir dans ton navigateur si besoin.
python -m jarvis --login
echo.
python -m jarvis --doctor
pause
