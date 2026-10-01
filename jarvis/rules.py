"""Consignes de comportement : le « mode d'emploi » de l'assistant, comme un CLAUDE.md.

Elles vivent dans ~/.jarvis/consignes.md, un simple fichier texte que l'utilisateur peut ouvrir et
modifier à la main, ou enrichir à la voix (« à partir de maintenant, … »). Elles sont lues au début
de chaque conversation et passent avant les habitudes par défaut.
Différent de la mémoire : la mémoire, ce sont des faits sur l'utilisateur ; les consignes, c'est
comment l'assistant doit se comporter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TEMPLATE = """# Consignes de {name}
# Comment {name} doit se comporter avec {user}. Une consigne par ligne, commençant par « - ».
# Tu peux modifier ce fichier à la main, ou dire à {name} : « À partir de maintenant, … ».
# Les lignes commençant par # sont des commentaires.

- Réponds toujours à la dernière chose que {user} vient de dire, sans revenir sur la question d'avant.
- Quand {user} dit « vas-y », « ok », « oui », fais tout de suite ce que tu venais de proposer, sans le reformuler.
- Ne dis jamais qu'un accès manque (mails, agenda, fichiers…) sans avoir d'abord essayé l'outil.
- Ne répète pas une phrase que tu viens de dire ; pas de formule d'introduction (« Je t'entends bien », « Bien sûr »).
- Si tu n'as pas compris (phrase coupée, mot bizarre), demande simplement de répéter au lieu de deviner.
- Va droit au but : une à trois phrases, puis propose de détailler si c'est utile.
"""

_RULE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


def path(config: Any) -> Path:
    return Path(config.home) / "consignes.md"


def _ensure(config: Any) -> Path:
    file = path(config)
    if not file.exists():
        file.write_text(TEMPLATE.format(name=config.assistant_name, user=config.user_name), encoding="utf-8")
    return file


def load(config: Any) -> list[str]:
    try:
        text = _ensure(config).read_text(encoding="utf-8")
    except OSError:
        return []
    return [m.group(1) for line in text.splitlines() if (m := _RULE.match(line))]


def prompt_block(config: Any) -> str:
    rules = load(config)
    if not rules:
        return ""
    lines = "\n".join(f"- {rule}" for rule in rules)
    return (f"# Tes consignes (données par {config.user_name} : elles passent avant tes habitudes)\n{lines}")


def add(config: Any, rule: str) -> str:
    rule = " ".join(rule.split()).lstrip("-* ").strip()
    if not rule:
        return ""
    if rule.lower() in {r.lower() for r in load(config)}:
        return rule
    file = _ensure(config)
    text = file.read_text(encoding="utf-8")
    file.write_text(text.rstrip("\n") + f"\n- {rule}\n", encoding="utf-8")
    return rule


def remove(config: Any, number: int) -> str | None:
    """Retire la consigne n° number (à partir de 1, dans l'ordre de load()). Renvoie son texte."""
    file = _ensure(config)
    lines = file.read_text(encoding="utf-8").splitlines()
    index = 0
    for i, line in enumerate(lines):
        match = _RULE.match(line)
        if match:
            index += 1
            if index == number:
                del lines[i]
                file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return match.group(1)
    return None
