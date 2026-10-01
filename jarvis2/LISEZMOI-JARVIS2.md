# Jarvis 2 (en français)

Origine : https://github.com/adewaskar/jarvis (MIT, voir `LICENSE`), adapté pour Yannis : interface holographique, voix dans le navigateur, cerveau = Claude (Agent SDK) via un petit pont Node, et les outils du Jarvis Python (mémoire, tâches, routines, Windows, Codex, crédits) branchés comme serveur MCP.

## Lancer sur Windows

Double-clic sur `jarvis2\demarrer.bat` (ou dans cmd : `cd /d J:\IAMAISON\Jarvis\jarvis2` puis `demarrer.bat`).
Premier lancement : `npm install` automatique. Ensuite Chrome s'ouvre sur http://localhost:5173 : clique sur INITIALISE, autorise le micro, dis « Jarvis, … ».
Pour autoriser les actions sensibles du pont (écrire des fichiers, shell…) : `demarrer.bat --writes`.
Prérequis : Node 20+, Chrome, Claude Code connecté (`claude` lancé une fois).

## Réglages (variables d'environnement)

- `JARVIS_MODEL` (défaut `sonnet`), `JARVIS_EFFORT` (défaut `low`).
- `JARVIS_PY` : python du serveur d'outils (défaut `J:\IAMAISON\Jarvis\.venv\Scripts\python.exe`) ; `JARVIS_PY_HOME` : dossier du projet Python ; `JARVIS_PY_DISABLE=1` pour le couper. Il faut `pip install -e ".[mcp]"` dans ce .venv.
- Nom : lu dans `%USERPROFILE%\.jarvis\state.json` (clé `assistant_name`), sinon « Jarvis ».
- `ELEVENLABS_API_KEY` : voix et transcription multilingues (français). Sans clé : voix fr-FR du navigateur (Windows : Microsoft Henri/Denise « Online » si installées).

## Mot d'activation en français

Fonctionnement inchangé : le navigateur écoute (fr-FR) et réagit quand il entend le nom. On peut dire « Jarvis », « Salut Jarvis », « Dis Jarvis », « Ok Jarvis ». Les variantes mal transcrites (Travis, Jervis…) fonctionnent aussi. Si tu renommes l'assistant dans `state.json`, la persona prend le nouveau nom, mais le mot qui le réveille reste « Jarvis » (à changer dans `WAKE` de `src/lib/voice.ts`).

## Limites connues

- Pas encore de conversation ChatGPT ni d'équilibrage des crédits dans le pont (ils restent dans le Jarvis Python).
- Les outils `chrome_*` (pilotage de ton Chrome) passent par un socket Unix : indisponibles sous Windows, le pont continue sans.
