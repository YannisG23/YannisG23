"""Identité de l'assistant : son nom se change à la voix (« à partir de maintenant, tu t'appelles Kali »)."""

from __future__ import annotations

import re

from .registry import ToolContext, registry

_VALID_NAME = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ' -]{0,23}$")


def clean_name(raw: str) -> str:
    """Nom propre et prononçable : lettres, espaces, tirets ; trois mots au plus."""
    name = " ".join(raw.strip().strip("«»\"'.,!?").split())
    if not _VALID_NAME.match(name) or len(name.split()) > 3 or len(name) < 2:
        raise ValueError(f"« {raw} » ne fait pas un bon nom : choisis un mot de 2 à 24 lettres.")
    return " ".join(part[:1].upper() + part[1:] for part in name.split())


@registry.tool(
    "Change ton propre nom, quand l'utilisateur te le demande (« à partir de maintenant tu t'appelles Kali »). "
    "Ce nom devient aussi le mot qui te réveille. Donne des orthographes proches dans aliases si le nom "
    "risque d'être mal transcrit par la reconnaissance vocale (ex. Kali → Kaly).",
    properties={
        "new_name": {"type": "string", "description": "Le nouveau nom, tel qu'il s'écrit."},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Orthographes alternatives que la transcription pourrait produire (facultatif).",
        },
    },
    required=["new_name"],
    confirm=lambda args: f"changer mon nom en « {args.get('new_name', '').strip()} »",
)
def rename_assistant(ctx: ToolContext, new_name: str, aliases: list | None = None) -> str:
    name = clean_name(new_name)
    alias_list = [a.strip() for a in (aliases or []) if isinstance(a, str) and a.strip()][:5]
    old = ctx.config.assistant_name
    ctx.config.assistant_name = name
    ctx.config.name_aliases = alias_list
    ctx.config.save_state(assistant_name=name, name_aliases=alias_list)
    return (f"C'est fait : je ne m'appelle plus {old} mais {name}. Pour me réveiller, il faut maintenant dire "
            f"« {name} ». Le changement est actif dès la fin de ta réponse et il est conservé après un redémarrage.")
