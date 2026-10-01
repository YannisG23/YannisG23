@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Connecte Gmail et Google Agenda a Jarvis.
rem Avant : cree le client OAuth sur console.cloud.google.com et telecharge son fichier JSON.
set "CRED=%USERPROFILE%\.jarvis\google_credentials.json"
if not exist "%USERPROFILE%\.jarvis" mkdir "%USERPROFILE%\.jarvis"
if exist "%CRED%" goto installer
set "FOUND="
for %%f in ("%USERPROFILE%\Downloads\client_secret*.json") do set "FOUND=%%f"
if not defined FOUND goto manquant
echo Fichier Google trouve dans Telechargements : %FOUND%
copy /y "%FOUND%" "%CRED%" >nul
goto installer

:manquant
echo Je ne trouve pas le fichier Google client_secret...json dans ton dossier Telechargements.
echo Telecharge-le depuis console.cloud.google.com, Clients, puis relance ce fichier.
pause
exit /b 1

:installer
echo Installation des bibliotheques Google...
.venv\Scripts\python -m pip install -q -e ".[google]"
echo.
echo Une page Google va s'ouvrir : choisis ton compte, puis Continuer.
echo Si Google dit que l'application n'est pas validee : Parametres avances, puis Acceder a Jarvis.
.venv\Scripts\python -m jarvis --setup-google
echo.
pause
