"""Consignes de comportement, gérées à la voix (voir jarvis/rules.py)."""

from __future__ import annotations

from .. import rules
from .registry import ToolContext, registry


@registry.tool(
    "Enregistre une consigne durable sur ta façon de te comporter, quand l'utilisateur te dit comment "
    "faire : « à partir de maintenant… », « arrête de… », « je veux que tu… », « quand je dis X, fais Y ». "
    "Ce n'est pas pour les faits sur lui (ça, c'est remember). Applique-la tout de suite.",
    properties={"rule": {"type": "string", "description": "La consigne, à l'impératif, claire et autonome."}},
    required=["rule"],
)
def add_rule(ctx: ToolContext, rule: str) -> str:
    saved = rules.add(ctx.config, rule)
    return f"Consigne enregistrée : {saved}" if saved else "Consigne vide."


@registry.tool("Liste tes consignes de comportement actuelles, numérotées.")
def list_rules(ctx: ToolContext) -> str:
    items = rules.load(ctx.config)
    if not items:
        return "Aucune consigne pour l'instant."
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(items, 1)) + f"\n(Fichier : {rules.path(ctx.config)})"


@registry.tool(
    "Supprime une consigne de comportement (numéro donné par list_rules), quand l'utilisateur ne la veut plus.",
    properties={"number": {"type": "integer", "description": "Numéro de la consigne dans list_rules."}},
    required=["number"],
)
def remove_rule(ctx: ToolContext, number: int) -> str:
    removed = rules.remove(ctx.config, number)
    return f"Consigne supprimée : {removed}" if removed else "Pas de consigne avec ce numéro."
