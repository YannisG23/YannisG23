@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
echo === Installation de J.A.R.V.I.S. ===

set "PY="
for %%V in (3.12 3.11 3.10) do (
  if not defined PY ( py -%%V -c "" >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY ( python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python" )
if not defined PY (
  echo Python 3.10 a 3.12 introuvable. Installe Python 3.12 depuis python.org
  echo en cochant "Add python.exe to PATH", puis relance install.bat.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist .venv (
  echo Creation de l'environnement Python avec %PY%...
  %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip
pip install -e ".[all]" || goto :error

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Le fichier .env va s'ouvrir : mets ton prenom et ta ville, enregistre, puis ferme-le.
  notepad .env
)

echo.
call :claude_exe
python -m jarvis --login
echo.
python -m jarvis --doctor
echo.
echo Installation terminee. Double-clique sur jarvis.bat pour lancer ton assistant.
pause
exit /b 0

:claude_exe
rem Il faut un vrai claude.exe : le kit de Claude refuse le script claude.cmd installe par npm.
python -c "from jarvis.brain_subscription import find_claude_cli as f; import sys; sys.exit(0 if f() else 1)" >nul 2>nul
if errorlevel 1 (
  echo Installation de Claude Code pour Windows, necessaire pour parler a Claude...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
  echo.
)
exit /b 0

:error
echo.
echo L'installation a echoue. Copie le message ci-dessus et envoie-le moi.
pause
exit /b 1
