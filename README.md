# Jarvis — ton assistant personnel

Ton assistant personnel de bureau : tu l'appelles par son nom, comme une personne (« **Jarvis, mets de la musique** »), il te répond avec une voix naturelle et agit sur ton ordinateur, tes e-mails, ton agenda et tes tâches. Il se souvient de toi d'une conversation à l'autre, et tout se pilote depuis un **centre de commande** dans ton navigateur.

Son cerveau est **Claude**, branché sur **ton abonnement Claude** (Pro ou Max) via Claude Code : pas de facture à l'usage. L'écoute, la voix, la mémoire et le centre de commande tournent sur ton PC.

## Ce qu'il sait faire

| Domaine | Exemples |
|---|---|
| Conversation vocale | Tu l'appelles par son nom, au début ou à la fin de ta phrase (« Jarvis, quel temps demain ? », « … baisse le son, Jarvis »), puis tu enchaînes sans le rappeler. Il commence à parler dès la première phrase de sa réponse, et tu lui coupes la parole en l'appelant (ou Échap, ou le bouton ■). Au premier lancement, **il choisit lui-même son nom** et te demande de valider. Ensuite tu peux le renommer à la voix (« à partir de maintenant tu t'appelles Kali ») ou lui redemander de choisir (« choisis-toi un nom »). |
| Centre de commande | Une présence animée qui réagit à ta voix et à la sienne, avec ses paroles en sous-titres ; conversation en direct, état du PC, météo, agenda, e-mails, mémoire modifiable, tâches, minuteurs, journal des actions, boutons « Faire le point » et « Nouvelle conversation », palette de commandes et raccourcis clavier. |
| Mémoire | Il retient seul ce qui compte (goûts, proches, projets, habitudes), retrouve les souvenirs utiles à chaque message et résume chaque conversation. Tu peux lui demander « de quoi on a parlé mardi ? ». |
| Briefing | « Fais-moi le point » : date, météo, agenda du jour, e-mails importants, tâches. |
| Contrôle du PC | « Ouvre Spotify », « Mets du lo-fi », « Monte le son », « Regarde mon écran, c'est quoi cette erreur ? », « Qu'est-ce qui ralentit mon PC ? » |
| Fichiers | « Trouve mes PDF de factures », « Lis ce fichier », « Écris une note sur le bureau » |
| Web | Recherche web en direct et lecture de pages, météo |
| Gmail | « J'ai des nouveaux mails ? », « Lis-moi le mail de Léa », « Réponds-lui que je suis d'accord » |
| Agenda Google | « Qu'est-ce que j'ai demain ? », « Ajoute dentiste jeudi à 15 h ». Il te prévient 10 minutes avant chaque rendez-vous. |
| Tâches et rappels | « Ajoute réviser le partiel à ma liste pour vendredi », « Rappelle-moi de sortir les pâtes dans 10 minutes » |
| Terminal | « Lance la commande … » |

**Sécurité** : exécuter une commande, envoyer un e-mail, écrire un fichier ou modifier l'agenda demande toujours ton accord (« oui » à la voix, bouton dans le centre de commande, ou « o » dans le terminal). Le contenu des e-mails et des pages web est traité comme une donnée, jamais comme un ordre. Le centre de commande n'est accessible que depuis ton PC, avec un jeton secret renouvelé à chaque lancement.

## Installation

Il te faut **Python 3.10 à 3.12** (Windows, macOS ou Linux), un micro, des haut-parleurs et un **compte Claude Pro ou Max**. Claude Code est installé automatiquement avec Jarvis.

### En un clic

- **Windows** : double-clique sur `install.bat`. Il installe tout, ouvre le fichier `.env` pour ton prénom et ta ville, te fait connecter ton compte Claude dans le navigateur, puis lance le diagnostic. Ensuite, lance Jarvis avec `jarvis.bat`. Deux autres raccourcis : `connexion.bat` (reconnecter ton compte Claude) et `diagnostic.bat` (tout vérifier et afficher les dernières erreurs).
- **macOS / Linux** : `./install.sh`, remplis `.env`, puis `./jarvis.sh`.

### À la main

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate    macOS/Linux : source .venv/bin/activate
pip install -e ".[all]"
cp .env.example .env        # puis remplis ton prénom et ta ville
python -m jarvis --login    # connecte ton compte Claude (une seule fois)
```

### Utilisation

```bash
python -m jarvis --doctor   # vérifie tout : compte Claude, micro, haut-parleurs, voix, Whisper, Google
python -m jarvis            # voix + centre de commande (s'ouvre dans le navigateur)
python -m jarvis --text     # sans micro ni voix : clavier + centre de commande
python -m jarvis --memory   # voir ce que Jarvis a retenu de toi
```

Au premier lancement, le modèle de reconnaissance vocale Whisper (~500 Mo pour `small`) se télécharge automatiquement. Si le micro pose problème, Jarvis démarre quand même, au clavier et dans le centre de commande.

Le lien du centre de commande s'affiche dans le terminal (`http://127.0.0.1:8765/#token=…`). Garde l'onglet ouvert. Si tu le fermes, reprends le lien depuis le terminal.

Au premier lancement de la matinée, Jarvis te fait un briefing (météo, agenda, e-mails, tâches) au lieu d'un simple bonjour (`JARVIS_DAILY_BRIEFING=0` pour le désactiver).

### Connecter Gmail et Google Agenda (optionnel)

1. Va sur https://console.cloud.google.com, crée un projet, puis active **Gmail API** et **Google Calendar API**.
2. Dans *Google Auth Platform* : écran de consentement en mode « Externe », puis ajoute ton adresse Gmail comme **utilisateur test**.
3. *Clients* → *Créer un client* → type **Application de bureau** → télécharge le JSON.
4. Renomme-le `google_credentials.json` et place-le dans `~/.jarvis/` (sous Windows : `C:\Users\<toi>\.jarvis\`).
5. Lance `python -m jarvis --setup-google` et accepte les autorisations dans le navigateur.

### Raccourcis du centre de commande

| Touche | Action |
|---|---|
| `Espace` | Parler |
| `Échap` | L'interrompre, ou fermer une fenêtre |
| `Ctrl` + `K` | Palette de commandes (ou taper directement une question) |
| `/` | Écrire une demande |
| `F` | Mode concentration : juste lui et la conversation |
| `?` | Aide |

## Comment fonctionne sa mémoire

- **Faits** : ce qu'il sait de toi, classés par catégorie et par importance (détail, utile, essentiel). Les essentiels et les plus utilisés sont toujours présents à son esprit. Les autres sont retrouvés automatiquement quand ton message en parle.
- **Conversations** : après 20 minutes sans échange, quand tu cliques sur « Nouvelle conversation », quand tu quittes, ou quand une conversation devient très longue, Jarvis la résume et en extrait les nouvelles infos durables. Les derniers résumés sont repris au début de chaque conversation.
- **Journal** : tout est archivé et consultable (« qu'est-ce que je t'avais dit sur le voyage ? »).
- Tout est stocké en local dans `~/.jarvis/memory.sqlite3`. Tu peux corriger, noter ou supprimer chaque souvenir depuis l'onglet **Mémoire** du centre de commande.

## Routines

Une routine est un enchaînement que Jarvis sait refaire à la demande : « **mode révision** », « lance ma **routine du matin** », « **mode soirée** ». Il lit les étapes et les accomplit avec ses outils (musique, minuteurs, tâches, mémoire, agenda).

- Trois routines d'exemple sont installées au premier lancement : **mode révision** (couper les distractions, playlist de concentration, Pomodoro 25/5, il t'interroge sur tes cours), **routine du matin** (point du jour, musique, tâches du jour) et **mode soirée** (un film selon tes goûts, rappel pour aller dormir).
- Apprends-lui les tiennes à la voix : « **apprends cette routine** : mode sport, mets une playlist énergique puis un minuteur de 30 minutes ». « Quelles sont mes routines ? » les liste ; la suppression demande confirmation.
- Chaque routine est un simple fichier Markdown dans `~/.jarvis/routines/`, modifiable à la main :

```markdown
# Mode sport
> Se motiver pour une séance.

1. Lance une playlist énergique.
2. Démarre un minuteur de 30 minutes.
```

## Réglages (`.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `JARVIS_BRAIN` | `abonnement` (compte Claude, prix fixe) ou `api` (clé API, facturée à l'usage) | `abonnement` |
| `JARVIS_MODEL` | Modèle Claude | `claude-opus-5` |
| `JARVIS_EFFORT` | Profondeur de réflexion : `low` (plus rapide), `medium`, `high` | `medium` |
| `JARVIS_VOICE` | Voix (`edge-tts --list-voices`) | `fr-FR-RemyMultilingualNeural` |
| `JARVIS_VOICE_RATE` | Débit de parole | `+5%` |
| `JARVIS_WHISPER_MODEL` | Précision de l'écoute : `base`, `small`, `medium`, `large-v3` | `small` |
| `JARVIS_NAME` | Impose un nom dès le départ (sinon il choisit le sien au premier lancement). Un nom donné à la voix l'emporte sur ce réglage. | `Jarvis` en attendant |
| `JARVIS_NAME_ALIASES` | Autres orthographes du nom que la transcription pourrait écrire | vide |
| `JARVIS_WAKE_MODE` | `nom` (l'appeler par son nom) ou `hey` (« Hey Jarvis », seule formule de ce mode, plus léger pour le PC) | `nom` |
| `JARVIS_WAKE_THRESHOLD` | Sensibilité de « Hey Jarvis » en mode `hey` | `0.5` |
| `JARVIS_FOLLOW_UP_SECONDS` | Attente d'une suite sans mot d'activation | `6` |
| `JARVIS_DAILY_BRIEFING` | Briefing au premier lancement de la matinée | `1` |
| `JARVIS_IDLE_MINUTES` | Inactivité avant de ranger la conversation en mémoire | `20` |
| `JARVIS_EVENT_REMINDER_MINUTES` | Avance des rappels de rendez-vous (0 = désactivé) | `10` |
| `JARVIS_DASHBOARD_PORT` | Port du centre de commande | `8765` |

| `JARVIS_TTS` | Voix : `edge` (gratuite) ou `elevenlabs` (premium) | `edge` |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | Clé et voix ElevenLabs | voix « Daniel » |
| `JARVIS_BARGE_IN` | Lui couper la parole en l'appelant | `1` |

Autres voix naturelles en français : `fr-FR-VivienneMultilingualNeural`, `fr-FR-HenriNeural`, `fr-FR-DeniseNeural`, `fr-CA-ThierryNeural`.
Pour des réponses plus rapides : `JARVIS_EFFORT=low`. Avec une carte graphique NVIDIA, Whisper l'utilise automatiquement, et `medium` ou `large-v3` deviennent confortables.

## Combien ça coûte

- **Cerveau** : ton abonnement Claude (Pro ou Max), à prix fixe. Les échanges avec Jarvis comptent dans les limites d'utilisation de ton abonnement, partagées avec ton usage normal de Claude. Si tu atteins la limite, Jarvis te le dit et te donne l'heure de réinitialisation. Pour dépenser moins de quota : `JARVIS_EFFORT=low`, ou `JARVIS_MODEL=claude-sonnet-5`.
- **Voix** : gratuite par défaut (Edge). ElevenLabs est payant au-delà de son petit quota gratuit, et Jarvis repasse tout seul sur la voix gratuite quand le quota est épuisé.
- **Mode API** (`JARVIS_BRAIN=api`) : facturé à l'usage par Anthropic, utile si tu n'as pas d'abonnement.
- Tout le reste (écoute, mot d'activation, mémoire, centre de commande, météo) est gratuit et tourne sur ton PC.

Ce mode abonnement est prévu pour **ton usage personnel**, sur ton PC et avec ton compte. Anthropic n'autorise pas à proposer une connexion avec un compte Claude dans un produit distribué à d'autres personnes : pour partager Jarvis, chacun utilise son propre compte, ou le mode API.

## Dépannage

- **Il ne réagit pas quand tu l'appelles** : dis son nom au début ou à la fin de ta phrase, bien détaché. Si la transcription l'écrit autrement (visible dans le terminal), ajoute cette orthographe dans `JARVIS_NAME_ALIASES`. Tu peux aussi cliquer sur le micro, ou appuyer sur Espace dans le centre de commande.
- **Le PC rame quand il écoute** : en mode `nom`, il transcrit ce qui se dit autour du micro pour repérer son nom. Sur un PC sans carte graphique NVIDIA, passe en `JARVIS_WAKE_MODE=hey` : tu diras alors « Hey Jarvis » (seule formule disponible dans ce mode), bien plus léger.
- **Il s'entend parler** : utilise un casque, ou baisse le volume des haut-parleurs.
- **Linux** : il faut `portaudio` (`sudo apt install libportaudio2`). Pour le mot d'activation, `tflite-runtime` n'existe pas pour toutes les versions de Python : utilise Python 3.10 ou 3.11.
- **macOS** : autorise, pour ton terminal, le micro, l'*Accessibilité* (touches média) et l'*Enregistrement de l'écran* (« regarde mon écran »).
- **Port occupé** : change `JARVIS_DASHBOARD_PORT`.
- **« Je n'arrive pas à joindre Claude »** : ferme-le, double-clique sur `connexion.bat`, puis relance `jarvis.bat`. Si ça continue, lance `diagnostic.bat` et envoie ce qu'il affiche : le détail de l'erreur s'y trouve (il est aussi dans l'onglet Activité et dans `~/.jarvis/erreurs.log`). Les commandes `python -m jarvis …` se tapent dans un terminal, pas dans le centre de commande.
- **Il se coupe tout seul pendant qu'il parle** : il a cru s'entendre appeler dans sa propre voix. Utilise un casque, ou mets `JARVIS_BARGE_IN=0`.

## Architecture

```
jarvis/
├── __main__.py          # ligne de commande
├── doctor.py            # diagnostic de l'installation
├── app.py               # lancement : noyau + voix + centre de commande + terminal
├── core.py              # noyau : file de requêtes, bus d'événements, confirmations, tâches de fond
├── brain.py             # Claude via l'API : persona, outils, rappel de souvenirs, consolidation
├── brain_subscription.py # Claude via Claude Code et ton abonnement (outils en serveur MCP)
├── memory.py            # mémoire SQLite : faits, conversations résumées, journal, tâches
├── config.py            # réglages (.env)
├── voice/
│   ├── listen.py        # micro, Whisper (faster-whisper), « Hey Jarvis » en option (openWakeWord)
│   ├── wakename.py      # repère son nom dans ce qui se dit (activation naturelle)
│   ├── speak.py         # voix neuronale (edge-tts) en file, repli hors ligne (pyttsx3)
│   └── loop.py          # boucle vocale : mot d'activation, conversation, confirmations
├── dashboard/
│   ├── server.py        # serveur local + API + flux d'événements en direct (SSE)
│   └── static/          # interface du centre de commande
└── tools/               # ce que Jarvis sait faire
    ├── system_tools.py  # applis, médias, écran, presse-papiers, processus, minuteurs, terminal
    ├── file_tools.py    # fichiers
    ├── web_tools.py     # météo
    ├── google_tools.py  # Gmail et Agenda
    ├── memory_tools.py  # mémoire, conversations, tâches
    └── routine_tools.py # routines (~/.jarvis/routines/*.md)
```

Pour ajouter une capacité, écris une fonction décorée avec `@registry.tool(...)` dans `jarvis/tools/` : Claude la découvre et l'utilise tout seul.

Tests : `pip install -e ".[dev]" && pytest`.
