# Feuille de route (Yannis)

Objectif : finir vite l'infrastructure IA, puis passer l'essentiel du temps sur l'acquisition de clients.
Règle d'or : **fonctionnel > parfait**. Pas de reconstruction de ce qui existe en open source.

## Phase 1 — Jarvis V1 opérationnel (deadline : ce soir)

Jarvis V1 existe : on ne le reconstruit pas, on le rend opérationnel.

V1 est terminé quand :
1. il reçoit correctement une demande (voix, texte, appli) ✅
2. il sélectionne un modèle selon la tâche ✅ (ChatGPT converse, Claude agit, Codex pour le gros code)
3. Claude fonctionne ✅
4. GPT fonctionne ⏳ (code prêt, clé OpenAI à valider sur le PC)
5. les outils principaux fonctionnent 🟡 (PC, fichiers, mémoire, routines, web OK ; Gmail/Agenda pas encore connectés)
6. une panne d'un fournisseur ne fait pas tomber Jarvis ✅ (GPT → Claude, Claude en limite → Codex, voix et fenêtre isolées)
7. une tâche peut être déléguée puis son résultat récupéré ✅ (GPT → Claude → Codex)

Multi-modèles : un seul modèle pour les tâches simples ; relecture croisée (Claude ↔ Codex) seulement pour les tâches importantes.
Développement : Git est la source de vérité ; commit avant toute grosse modification faite par un agent.

## Phase 2 — Machine à Shorts IA (dès que V1 marche)

Pipeline V1 : 1 chaîne, 1 niche, 1 format, 1 Short complet, même moyen.
idée → hook + script → storyboard → prompts vidéo/image → Higgsfield → clips → voix + sous-titres → montage → Short → YouTube → stats → amélioration.

- Partir d'un projet open source mature pour le montage/voix/sous-titres plutôt que tout coder.
- Higgsfield = moteur vidéo de départ. DOTS seulement s'il rend une étape plus simple, fiable ou autonome.
- Demain : premier pipeline complet. Dimanche : « crée le prochain Short » → Short prêt à publier (étapes humaines identifiées).

## Phase 3 — Business (à partir de dimanche)

Stop aux développements non indispensables.
- Sites vitrines : 50 €/mois pendant 12 mois puis 30 €/mois (site, domaine, hébergement, sécurité, maintenance). Démo créée AVANT le contact. Premier objectif : 4 artisans → 4 démos → 4 appels.
- Relancia : 7 % des ventes récupérées, sans abonnement. Objectif : UN client pilote.

## Garde-fous

1. Pas de reconstruction d'un outil open source existant. 2. Pas de nouvelle idée business avant d'avoir exécuté celles-ci.
3. Pas de perfectionnement infini de Jarvis ni du pipeline. 4. Une V1 qui marche vaut mieux qu'une architecture parfaite.
5. Mesurer les résultats réels. 6. N'automatiser que ce qui marche déjà. 7. Contrôle croisé Claude/GPT seulement sur l'important.
8. Git avant toute grosse modification automatique. 9. À partir de dimanche : priorité commerciale.
