"""Outils de mémoire long terme."""

from __future__ import annotations

from .registry import ToolContext, registry


@registry.tool(
    "Mémorise durablement un fait sur l'utilisateur (préférence, info perso, projet, "
    "proche, habitude...). À utiliser dès que l'utilisateur partage quelque chose d'utile "
    "pour plus tard, ou quand il dit « retiens que... ».",
    properties={
        "fact": {"type": "string", "description": "Le fait, formulé clairement et de façon autonome."},
        "category": {
            "type": "string",
            "description": "Catégorie courte : preference, perso, travail, proches, sante, projet, general...",
        },
    },
    required=["fact"],
)
def remember(ctx: ToolContext, fact: str, category: str = "general") -> str:
    saved = ctx.memory.remember(fact, category)
    return f"Mémorisé : {saved.render()}"


@registry.tool(
    "Cherche dans la mémoire long terme les faits liés à un sujet.",
    properties={"query": {"type": "string", "description": "Mots-clés à chercher."}},
    required=["query"],
)
def recall(ctx: ToolContext, query: str) -> str:
    facts = ctx.memory.search(query)
    if not facts:
        return "Aucun souvenir trouvé pour cette recherche."
    return "\n".join(f.render() for f in facts)


@registry.tool(
    "Oublie un fait mémorisé, via son numéro (#id). Utilise recall d'abord pour trouver l'id.",
    properties={"fact_id": {"type": "integer", "description": "Numéro du fait à supprimer."}},
    required=["fact_id"],
)
def forget(ctx: ToolContext, fact_id: int) -> str:
    if ctx.memory.forget(int(fact_id)):
        return f"Le fait #{fact_id} a été oublié."
    return f"Aucun fait #{fact_id} en mémoire."
