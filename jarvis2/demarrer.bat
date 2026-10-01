@echo off
REM Jarvis 2 : installe au premier lancement, démarre, ouvre Chrome.
chcp 65001 >nul
cd /d "%~dp0"

where node >nul 2>nul || (echo Node.js est introuvable : installe-le depuis https://nodejs.org puis relance. & pause & exit /b 1)

if not exist node_modules (
  echo Premier lancement : installation des dependances, ca prend quelques minutes...
  call npm install || (echo L'installation a echoue. & pause & exit /b 1)
)

REM Python de Jarvis (outils memoire/Windows) : le .venv de J:\IAMAISON\Jarvis par defaut.
if "%JARVIS_PY%"=="" if exist "J:\IAMAISON\Jarvis\.venv\Scripts\python.exe" set "JARVIS_PY=J:\IAMAISON\Jarvis\.venv\Scripts\python.exe"
if "%JARVIS_PY_HOME%"=="" if exist "J:\IAMAISON\Jarvis\jarvis" set "JARVIS_PY_HOME=J:\IAMAISON\Jarvis"
REM Le serveur d'outils a besoin du paquet mcp (serie 1.x) dans ce Python.
if not "%JARVIS_PY%"=="" (
  "%JARVIS_PY%" -c "import mcp" 2>nul || "%JARVIS_PY%" -m pip install -q "mcp>=1.2,<2"
)

REM Ouvre Chrome sur l'interface quand le serveur a eu le temps de demarrer.
start "" /b cmd /c "timeout /t 8 /nobreak >nul & start chrome http://localhost:5173"

REM Ajoute --writes pour autoriser les actions (envoyer, supprimer...) : npm start -- --writes
call npm start -- %*
pause
