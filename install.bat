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
python -m jarvis --login
echo.
python -m jarvis --doctor
echo.
echo Installation terminee. Double-clique sur jarvis.bat pour lancer ton assistant.
pause
exit /b 0

:error
echo.
echo L'installation a echoue. Copie le message ci-dessus et envoie-le moi.
pause
exit /b 1
