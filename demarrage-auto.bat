@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Lance pont-claude.bat automatiquement a chaque ouverture de session Windows (fenetre reduite).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Pont Claude.lnk'); $s.TargetPath='%~dp0pont-claude.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (echo Echec de la creation du raccourci. & pause & exit /b 1)
echo C'est fait : le pont Claude demarrera tout seul a chaque ouverture de session.
echo Pour l'enlever : Win+R, tape shell:startup, supprime « Pont Claude ».
start "" "%~dp0pont-claude.bat"
pause
