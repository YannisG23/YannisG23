"""Routines : des enchaînements réutilisables (« mode révision », « routine du matin »...).

Une routine est un fichier Markdown dans ~/.jarvis/routines/<nom>.md :

    # Titre
    > Description courte.

    1. Première étape en langage naturel.
    2. Deuxième étape.

L'assistant ne les exécute pas mécaniquement : run_routine lui renvoie les étapes,
et il les accomplit avec ses autres outils (musique, minuteurs, tâches, mémoire...).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .registry import ToolContext, registry

# Copiées au premier lancement, quand le dossier des routines est vide.
EXAMPLE_ROUTINES: dict[str, tuple[str, str, list[str]]] = {
    "mode-revision": (
        "Mode révision",
        "Se mettre au travail sans distraction, en Pomodoro, et se faire interroger sur ses cours.",
        [
            "Demande sur quelle matière ou quel chapitre on révise, si ce n'est pas déjà dit.",
            "Propose de fermer les distractions ouvertes (Discord, réseaux sociaux, jeux) : regarde les processus "
            "et ferme ce qu'il accepte, et suggère de mettre le téléphone en silencieux.",
            "Lance une playlist de concentration (lo-fi ou musique calme sans paroles, selon ses goûts mémorisés).",
            "Démarre un minuteur Pomodoro de 25 minutes intitulé « révision ».",
            "À la fin de chaque Pomodoro, lance une pause de 5 minutes, puis relance 25 minutes s'il veut continuer ; "
            "toutes les quatre séances, propose une pause plus longue.",
            "Pendant les pauses ou à sa demande, interroge-le sur ses cours : une question à la fois, "
            "corrige avec bienveillance, et note les points faibles en mémoire pour y revenir.",
        ],
    ),
    "routine-du-matin": (
        "Routine du matin",
        "Bien démarrer la journée : le point du jour, un peu de musique et les tâches à faire.",
        [
            "Salue-le brièvement selon l'heure.",
            "Fais le point du jour : date, météo, agenda du jour, e-mails importants non lus.",
            "Lance une musique douce ou entraînante selon ses goûts mémorisés.",
            "Liste ses tâches du jour et en retard, et propose de commencer par la plus importante.",
        ],
    ),
    "mode-soiree": (
        "Mode soirée",
        "Décompresser : une idée de film selon ses goûts, puis un rappel pour aller dormir.",
        [
            "Regarde dans ta mémoire ses goûts en films et séries, et ce qu'il a déjà vu.",
            "Propose-lui deux ou trois films qui lui correspondent, en une phrase chacun, et lance celui qu'il choisit.",
            "Demande à quelle heure il veut se coucher (ou utilise son habitude mémorisée), "
            "puis programme un minuteur de rappel pour aller dormir.",
        ],
    ),
}


@dataclass
class Routine:
    slug: str
    title: str
    description: str
    steps: list[str]

    def render(self) -> str:
        steps = "\n".join(f"{i}. {step}" for i, step in enumerate(self.steps, 1))
        return f"# {self.title}\n\n> {self.description}\n\n{steps}\n"


def slugify(name: str) -> str:
    """« Mode révision » → « mode-revision » (nom de fichier sûr)."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:60]


def parse_routine(slug: str, text: str) -> Routine:
    title, description, steps = slug.replace("-", " ").capitalize(), "", []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith(">"):
            description = (description + " " + line.lstrip("> ").strip()).strip()
        elif m := re.match(r"^(?:\d+[.)]|[-*])\s+(.*)", line):
            steps.append(m.group(1).strip())
        elif line and not line.startswith("#"):
            # Texte libre : une étape à part entière, ou la suite de la précédente.
            if steps and raw.startswith((" ", "\t")):
                steps[-1] += " " + line
            else:
                steps.append(line)
    return Routine(slug, title, description, steps)


def routines_dir(config) -> Path:
    folder = config.home / "routines"
    folder.mkdir(parents=True, exist_ok=True)
    # Exemples copiés une seule fois : s'il les supprime, ils ne reviennent pas.
    if not any(folder.glob("*.md")) and not config.load_state().get("routines_seeded"):
        for slug, (title, description, steps) in EXAMPLE_ROUTINES.items():
            (folder / f"{slug}.md").write_text(Routine(slug, title, description, steps).render(), encoding="utf-8")
        config.save_state(routines_seeded=True)
    return folder


def load_routines(config) -> list[Routine]:
    return [parse_routine(p.stem, p.read_text(encoding="utf-8")) for p in sorted(routines_dir(config).glob("*.md"))]


def find_routine(config, name: str, fuzzy: bool = True) -> Routine | None:
    """Retrouve une routine à partir de ce qui a été dit (« le mode révision », « révision »...).

    Sans fuzzy, seul le nom exact compte (pour une suppression, on ne devine pas)."""
    routines = load_routines(config)
    wanted = slugify(name)
    if not wanted:
        return None
    for r in routines:
        if wanted in (r.slug, slugify(r.title)):
            return r
    if not fuzzy:
        return None
    for r in routines:
        if wanted in r.slug or r.slug in wanted:
            return r
    close = difflib.get_close_matches(wanted, [r.slug for r in routines], n=1, cutoff=0.6)
    return next((r for r in routines if close and r.slug == close[0]), None)


@registry.tool(
    "Liste les routines de l'utilisateur (enchaînements réutilisables comme « mode révision » ou « routine du matin »).",
)
def list_routines(ctx: ToolContext) -> str:
    routines = load_routines(ctx.config)
    if not routines:
        return "Aucune routine pour l'instant. Tu peux en créer une avec create_routine."
    return "\n".join(f"- {r.title} ({r.slug}) : {r.description or 'sans description'}" for r in routines)


@registry.tool(
    "Lance une routine : renvoie ses étapes, que tu dois ensuite accomplir toi-même avec tes autres outils "
    "(musique, minuteurs, tâches, mémoire, agenda...). À utiliser dès que l'utilisateur demande une routine ou un "
    "mode par son nom (« mode révision », « lance ma routine du matin »).",
    properties={"name": {"type": "string", "description": "Nom de la routine, tel que dit par l'utilisateur."}},
    required=["name"],
)
def run_routine(ctx: ToolContext, name: str) -> str:
    routine = find_routine(ctx.config, name)
    if routine is None:
        names = ", ".join(r.title for r in load_routines(ctx.config)) or "aucune"
        return f"Aucune routine « {name} ». Routines disponibles : {names}."
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(routine.steps, 1))
    return (
        f"Routine « {routine.title} » : {routine.description}\n"
        f"Accomplis maintenant ces étapes dans l'ordre, avec tes outils, sans redemander la permission pour chacune "
        f"(les actions sensibles restent confirmées par le système). Annonce brièvement ce que tu lances.\n{steps}"
    )


@registry.tool(
    "Crée ou remplace une routine réutilisable, par exemple quand l'utilisateur dit « apprends cette routine : ... » "
    "ou « quand je dis mode sport, fais ceci ». Reformule les étapes en consignes claires et autonomes.",
    properties={
        "name": {"type": "string", "description": "Nom court de la routine, ex. « Mode sport »."},
        "description": {"type": "string", "description": "Une phrase qui résume son but."},
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Les étapes, dans l'ordre, en langage naturel.",
        },
    },
    required=["name", "description", "steps"],
)
def create_routine(ctx: ToolContext, name: str, description: str, steps: list) -> str:
    slug = slugify(name)
    clean_steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
    if not slug:
        return "Il me faut un nom de routine avec des lettres ou des chiffres."
    if not clean_steps:
        return "Il me faut au moins une étape pour créer la routine."
    path = routines_dir(ctx.config) / f"{slug}.md"
    existed = path.exists()
    routine = Routine(slug, name.strip(), " ".join(description.split()), [" ".join(s.split()) for s in clean_steps])
    path.write_text(routine.render(), encoding="utf-8")
    return f"Routine « {routine.title} » {'mise à jour' if existed else 'créée'} avec {len(clean_steps)} étape(s)."


@registry.tool(
    "Supprime une routine.",
    properties={"name": {"type": "string", "description": "Nom de la routine à supprimer."}},
    required=["name"],
    confirm=lambda args: f"supprimer la routine « {args.get('name', '')} »",
)
def delete_routine(ctx: ToolContext, name: str) -> str:
    routine = find_routine(ctx.config, name, fuzzy=False)
    if routine is None:
        return f"Aucune routine qui s'appelle exactement « {name} ». Vérifie son nom avec list_routines."
    (routines_dir(ctx.config) / f"{routine.slug}.md").unlink(missing_ok=True)
    return f"Routine « {routine.title} » supprimée."
