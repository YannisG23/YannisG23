---
name: equipe-jarvis
description: Règles de travail en équipe des sessions Claude sur le projet Jarvis de Yannis (chef de projet, sessions cloud, sessions sur le PC via remote-control). À lire au début de toute session qui travaille sur Jarvis, qu'elle code, teste sur le PC ou fasse un compte rendu.
---

# Travailler en équipe sur Jarvis

Jarvis est l'assistant vocal personnel de Yannis (Python, dossier `jarvis/`). Plusieurs sessions Claude s'y partagent le travail.

## Les rôles

- **Chef de projet** : session cloud « Jarvis : chef de projet ». Répartit les tâches, relit, fusionne et pousse sur `claude/jarvis-personnel-bfhr70`. C'est la seule branche que Yannis installe.
- **Sessions de développement** (cloud) : une tâche précise chacune, sur leur propre branche `claude/jarvis-<sujet>`. Le chef de projet relit et fusionne.
- **Sessions PC** (environnement « DESKTOP-HD3M8M7 », dossier `J:\IAMAISON\Jarvis`) : installent, mettent à jour et testent sur le vrai PC Windows de Yannis. Elles ne modifient pas le code : elles rapportent.

## Règles communes

1. Toute session du projet porte l'étiquette `jarvis` : c'est ce qui la range dans le groupe Jarvis de la barre latérale. Titre : « Jarvis : <rôle ou sujet> ».
2. Yannis est débutant et parle français : on lui écrit en français simple. Une commande à taper est donnée en entier, en précisant le terminal (PowerShell ou cmd ; `irm` n'existe que dans PowerShell ; dans cmd, `cd /d J:\...` pour changer de disque).
3. On ne touche jamais au fichier `.env` ni à `%USERPROFILE%\.jarvis` (mémoire, état, nom choisi) sans l'accord explicite de Yannis.
4. Pas de pull request sans demande de Yannis. Les commits se terminent par les lignes d'attribution demandées par la session.
5. Le quota de l'abonnement est partagé avec Jarvis lui-même : les sessions PC tournent avec Sonnet, une seule session PC à la fois, missions courtes. Les suivis automatiques (send_later) sont autorisés mais espacés (10 à 20 min).
6. Avant de pousser : `PYTHONPATH=. .venv/bin/python -m pytest -q tests` (ou `.venv\Scripts\python -m pytest -q tests` sous Windows) doit passer. Chaque correction ajoute un test quand c'est possible.

## Sur le PC (sessions PC)

- Mettre à jour : `git pull` dans `J:\IAMAISON\Jarvis` (branche `claude/jarvis-personnel-bfhr70`), puis `.venv\Scripts\pip install -e ".[all]"`.
- Outils de diagnostic : `.venv\Scripts\python -m jarvis --doctor`, `--micros` (liste des micros et haut-parleurs), `--text --no-browser` (test sans micro), journal `%USERPROFILE%\.jarvis\erreurs.log`.
- Le mode auto peut bloquer l'exécution de code téléchargé : si c'est le cas, on s'arrête et on demande à Yannis son autorisation écrite dans la session. On ne cherche pas à contourner le blocage.

## Format du compte rendu

Terminer chaque mission par un compte rendu court, que Yannis peut recopier au chef de projet :

```
RÉSULTAT : OK / PROBLÈMES
Fait : <étapes réalisées>
Marche : <ce qui fonctionne>
Plante : <message d'erreur exact, commande qui l'a produit>
Extrait erreurs.log : <lignes utiles>
À décider par Yannis : <choix en attente, ex. JARVIS_MIC=...>
```
