# J.A.R.V.I.S. — ton assistant personnel

Un Jarvis de bureau façon Iron Man : tu dis « **Hey Jarvis** », tu parles, il te répond à voix haute et agit sur ton ordinateur, tes e-mails et ton agenda. Il se souvient aussi de toi d'une conversation à l'autre.

Son cerveau est **Claude** (API Anthropic). La voix et l'écoute tournent sur ton PC.

## Ce qu'il sait faire

| Domaine | Exemples de demandes |
|---|---|
| Conversation vocale | Mot d'activation « Hey Jarvis », mode conversation (tu enchaînes sans redire « Hey Jarvis »), voix neuronale française |
| Contrôle du PC | « Ouvre Spotify », « Mets du lo-fi sur YouTube », « Monte le son », « Morceau suivant », « Regarde mon écran, c'est quoi cette erreur ? », « Copie ça dans le presse-papiers », « État du PC ? » |
| Fichiers | « Trouve mes PDF de factures », « Qu'est-ce qu'il y a dans Téléchargements ? », « Lis ce fichier », « Écris-moi une note sur le bureau » |
| Web | Recherche web en direct (actus, résultats sportifs, prix...), météo |
| Gmail | « J'ai des nouveaux mails ? », « Lis-moi le mail de Léa », « Réponds-lui que je suis d'accord » |
| Agenda Google | « Qu'est-ce que j'ai demain ? », « Ajoute un rendez-vous chez le dentiste jeudi à 15 h » |
| Minuteurs et rappels | « Rappelle-moi de sortir les pâtes dans 10 minutes » |
| Mémoire long terme | « Retiens que ma sœur s'appelle Léa » ; il retient aussi seul ce qui compte (goûts, projets, proches) |
| Terminal | « Lance la commande ... » |

**Sécurité** : exécuter une commande, envoyer un e-mail, écrire un fichier ou modifier l'agenda demande toujours ton « oui » explicite. Le contenu des e-mails et des pages web est traité comme une donnée, pas comme un ordre.

## Installation

Il te faut **Python 3.10 à 3.12** (Windows, macOS ou Linux), un micro et des haut-parleurs.

```bash
git clone <ce dépôt> jarvis && cd jarvis
python -m venv .venv
# Windows : .venv\Scripts\activate    macOS/Linux : source .venv/bin/activate
pip install -e ".[all]"
```

1. Crée une clé d'API sur https://console.anthropic.com/settings/keys (usage payant à la consommation).
2. Copie `.env.example` en `.env`, colle ta clé et règle ton prénom et ta ville.
3. Lance :

```bash
python -m jarvis           # mode vocal (dis « Hey Jarvis »)
python -m jarvis --text    # mode clavier, pratique pour tester
python -m jarvis --memory  # voir ce que Jarvis a retenu de toi
```

Au premier lancement, le modèle de reconnaissance vocale Whisper (~500 Mo pour `small`) et le modèle « Hey Jarvis » se téléchargent automatiquement.

### Connecter Gmail et Google Agenda (optionnel)

1. Va sur https://console.cloud.google.com, crée un projet, puis active **Gmail API** et **Google Calendar API**.
2. Dans *Google Auth Platform* : écran de consentement en mode « Externe », puis ajoute ton adresse Gmail comme **utilisateur test**.
3. *Clients* → *Créer un client* → type **Application de bureau** → télécharge le JSON.
4. Renomme-le `google_credentials.json` et place-le dans le dossier `~/.jarvis/` (sous Windows : `C:\Users\<toi>\.jarvis\`).
5. Lance `python -m jarvis --setup-google` et accepte les autorisations dans le navigateur.

## Réglages utiles (`.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `JARVIS_MODEL` | Modèle Claude | `claude-opus-5` |
| `JARVIS_EFFORT` | Profondeur de réflexion : `low` (plus rapide), `medium`, `high` | `medium` |
| `JARVIS_VOICE` | Voix (`edge-tts --list-voices`) | `fr-FR-HenriNeural` |
| `JARVIS_WHISPER_MODEL` | Précision de l'écoute : `base`, `small`, `medium`, `large-v3` | `small` |
| `JARVIS_WAKE_THRESHOLD` | Sensibilité de « Hey Jarvis » (plus bas = plus sensible) | `0.5` |
| `JARVIS_FOLLOW_UP_SECONDS` | Durée d'attente d'une suite sans mot d'activation | `6` |

Pour des réponses plus rapides : `JARVIS_EFFORT=low`. Avec une carte graphique NVIDIA, Whisper l'utilise automatiquement, et `medium` ou `large-v3` deviennent confortables.

## Dépannage

- **Il ne réagit pas à « Hey Jarvis »** : baisse `JARVIS_WAKE_THRESHOLD` à `0.3`. Si le modèle ne se charge pas, Jarvis passe en mode « appuie sur Entrée pour parler ».
- **Linux** : il faut `portaudio` (`sudo apt install libportaudio2`) et, pour le mot d'activation, `tflite-runtime` n'existe pas pour tous les Python : utilise Python 3.10 ou 3.11.
- **macOS** : autorise le micro pour ton terminal, ainsi que l'*Accessibilité* (touches média) et l'*Enregistrement de l'écran* (« regarde mon écran »).
- **Il se coupe trop tôt ou trop tard** : parle après le bip ; la fin de phrase est détectée après environ une seconde de silence.

## Architecture

```
jarvis/
├── __main__.py        # ligne de commande
├── app.py             # boucles vocale et texte, confirmations
├── brain.py           # Claude : persona, boucle d'outils, recherche web, cache de prompt
├── memory.py          # mémoire long terme (SQLite dans ~/.jarvis)
├── config.py          # réglages (.env)
├── voice/
│   ├── listen.py      # micro, « Hey Jarvis » (openWakeWord), Whisper (faster-whisper)
│   └── speak.py       # voix neuronale (edge-tts), repli hors ligne (pyttsx3)
└── tools/             # ce que Jarvis peut faire
    ├── system_tools.py   # applis, médias, écran, presse-papiers, minuteurs, terminal
    ├── file_tools.py     # fichiers
    ├── web_tools.py      # météo
    ├── google_tools.py   # Gmail et Agenda
    └── memory_tools.py   # retenir, retrouver, oublier
```

Pour ajouter une capacité, écris une fonction décorée avec `@registry.tool(...)` dans `jarvis/tools/` : Claude la découvre et l'utilise tout seul.

Tests : `pip install -e ".[dev]" && pytest`.
