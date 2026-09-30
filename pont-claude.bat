@echo off
chcp 65001 >nul
title Pont Claude (remote-control)
cd /d "%~dp0"
rem Garde le PC relie a l'equipe Claude : relance le pont automatiquement s'il se coupe.
set CLAUDE=claude
where claude >nul 2>nul || set CLAUDE="%USERPROFILE%\.local\bin\claude.exe"
:boucle
echo [%date% %time%] Pont Claude demarre. Laisse cette fenetre ouverte (tu peux la reduire).
%CLAUDE% remote-control
echo Le pont s'est arrete. Nouvelle tentative dans 20 secondes (ferme la fenetre pour arreter)...
timeout /t 20 /nobreak >nul
goto boucle
