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
echo Connexion a ton compte Claude : une page va s'ouvrir dans ton navigateur.
python -m jarvis --login
echo.
python -m jarvis --doctor
pause
