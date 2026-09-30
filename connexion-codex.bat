@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Installe et connecte Codex (OpenAI) : il faut un abonnement ChatGPT (Plus ou Pro).
where codex >nul 2>nul
if errorlevel 1 (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo Il faut Node.js pour installer Codex : telecharge-le sur https://nodejs.org puis relance ce fichier.
    pause
    exit /b 1
  )
  echo Installation de Codex...
  call npm install -g @openai/codex
  echo.
)
echo Connexion a ton compte ChatGPT : une page va s'ouvrir dans ton navigateur.
echo Choisis « Sign in with ChatGPT ».
call codex login
echo.
if exist .venv\Scripts\python.exe .venv\Scripts\python -m jarvis --doctor
pause
