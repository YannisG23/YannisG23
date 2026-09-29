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
python -m jarvis --doctor
echo.
echo Dernieres erreurs enregistrees :
if exist "%USERPROFILE%\.jarvis\erreurs.log" (
  powershell -NoProfile -Command "Get-Content -Tail 40 -Encoding UTF8 \"$env:USERPROFILE\.jarvis\erreurs.log\""
) else (
  echo Aucune.
)
pause
