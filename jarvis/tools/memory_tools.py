"""Outils de mémoire long terme et de tâches."""

from __future__ import annotations

from .registry import ToolContext, registry


@registry.tool(
    "Mémorise durablement un fait sur l'utilisateur ou sa vie : préférence, info perso, proche, "
    "projet, habitude, objectif, info pratique (adresse, code, taille...). À utiliser dès que "
    "l'utilisateur partage quelque chose d'utile pour plus tard, ou dit « retiens que ». "
    "Un fait très proche d'un fait existant le met à jour au lieu de créer un doublon.",
    properties={
        "fact": {"type": "string", "description": "Le fait, formulé clairement et de façon autonome, à la 3e personne."},
        "category": {
            "type": "string",
            "description": "Catégorie courte : identite, preference, proches, travail, etudes, sante, projet, habitude, pratique, general.",
        },
        "importance": {
            "type": "integer",
            "description": "1 = détail, 2 = utile (défaut), 3 = essentiel (identité, proches, objectifs majeurs).",
        },
    },
    required=["fact"],
)
def remember(ctx: ToolContext, fact: str, category: str = "general", importance: int = 2) -> str:
    saved, created = ctx.memory.remember(fact, category, importance)
    return f"{'Mémorisé' if created else 'Souvenir existant mis à jour'} : {saved.render()}"


@registry.tool(
    "Cherche dans la mémoire long terme : faits connus sur l'utilisateur et résumés des conversations passées.",
    properties={"query": {"type": "string", "description": "Sujet ou mots-clés."}},
    required=["query"],
)
def recall(ctx: ToolContext, query: str) -> str:
    facts = ctx.memory.search(query, limit=15)
    episodes = ctx.memory.search_episodes(query, limit=5)
    if not facts and not episodes:
        return "Aucun souvenir trouvé pour cette recherche."
    parts = []
    if facts:
        parts.append("Faits :\n" + "\n".join(f.render() for f in facts))
    if episodes:
        parts.append("Conversations passées :\n" + "\n".join(e.render() for e in episodes))
    return "\n\n".join(parts)


@registry.tool(
    "Corrige ou complète un fait mémorisé (numéro #id), par exemple quand une info a changé.",
    properties={
        "fact_id": {"type": "integer"},
        "fact": {"type": "string", "description": "Nouvelle formulation complète."},
        "importance": {"type": "integer", "description": "1 à 3, optionnel."},
    },
    required=["fact_id", "fact"],
)
def update_memory(ctx: ToolContext, fact_id: int, fact: str, importance: int = 0) -> str:
    try:
        updated = ctx.memory.update_fact(int(fact_id), fact, importance=importance or None)
    except KeyError:
        return f"Aucun fait #{fact_id} en mémoire."
    return f"Mis à jour : {updated.render()}"


@registry.tool(
    "Oublie un fait mémorisé, via son numéro (#id). Utilise recall d'abord pour trouver l'id.",
    properties={"fact_id": {"type": "integer", "description": "Numéro du fait à supprimer."}},
    required=["fact_id"],
)
def forget(ctx: ToolContext, fact_id: int) -> str:
    if ctx.memory.forget(int(fact_id)):
        return f"Le fait #{fact_id} a été oublié."
    return f"Aucun fait #{fact_id} en mémoire."


@registry.tool(
    "Retrouve ce qui a été dit dans les conversations passées (journal complet), "
    "ex. « de quoi on a parlé hier ? », « qu'est-ce que je t'avais dit sur le voyage ? ».",
    properties={
        "query": {"type": "string", "description": "Mots-clés. Vide = derniers échanges."},
        "days": {"type": "integer", "description": "Période en jours. Défaut 30."},
    },
)
def search_conversations(ctx: ToolContext, query: str = "", days: int = 30) -> str:
    rows = ctx.memory.search_log(query, days=days)
    if not rows:
        return "Rien trouvé dans les conversations sur cette période."
    who = {"user": ctx.config.user_name, "assistant": ctx.config.assistant_name}
    return "\n".join(f"[{created[:16].replace('T', ' ')}] {who.get(role, role)} : {content[:400]}"
                     for role, content, created in rows)


@registry.tool(
    "Ajoute une tâche à la liste de tâches de l'utilisateur (visible dans le centre de commande).",
    properties={
        "title": {"type": "string"},
        "due": {"type": "string", "description": "Échéance optionnelle, format AAAA-MM-JJ ou AAAA-MM-JJ HH:MM."},
    },
    required=["title"],
)
def add_task(ctx: ToolContext, title: str, due: str = "") -> str:
    return f"Tâche ajoutée : {ctx.memory.add_task(title, due).render()}"


@registry.tool(
    "Liste les tâches de l'utilisateur.",
    properties={"include_done": {"type": "boolean", "description": "Inclure les tâches terminées."}},
)
def list_tasks(ctx: ToolContext, include_done: bool = False) -> str:
    tasks = ctx.memory.tasks(include_done=include_done)
    return "\n".join(t.render() for t in tasks) if tasks else "Aucune tâche en cours."


@registry.tool(
    "Marque une tâche comme faite (ou la supprime si delete=true).",
    properties={"task_id": {"type": "integer"}, "delete": {"type": "boolean"}},
    required=["task_id"],
)
def complete_task(ctx: ToolContext, task_id: int, delete: bool = False) -> str:
    ok = ctx.memory.delete_task(int(task_id)) if delete else ctx.memory.complete_task(int(task_id))
    if not ok:
        return f"Aucune tâche #{task_id}."
    return f"Tâche #{task_id} {'supprimée' if delete else 'terminée'}."
