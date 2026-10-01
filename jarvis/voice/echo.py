"""Reconnaître sa propre voix : sans casque bien isolé, le micro capte ce que dit l'assistant.

Si une phrase « entendue » reprend presque mot pour mot ce qu'il vient de dire, c'est un écho :
on l'ignore, sinon il se répondrait à lui-même et répéterait les mêmes phrases en boucle.
"""

from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher

ECHO_WINDOW = 6.0  # secondes après la fin d'une phrase pendant lesquelles son écho peut revenir
MIN_WORDS = 3  # « oui », « non merci » : trop court pour juger
MATCH = 0.9  # part de la phrase entendue retrouvée telle quelle dans ce qu'il a dit


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def is_echo(heard: str, recent: list[tuple[float, str]], now: float, window: float = ECHO_WINDOW) -> bool:
    """recent : (moment où la phrase a fini d'être dite — inf si en cours —, phrase)."""
    heard_n = normalize(heard)
    if len(heard_n.split()) < MIN_WORDS:
        return False
    spoken = " ".join(normalize(text) for end, text in recent if math.isinf(end) or now - end <= window)
    if not spoken:
        return False
    matcher = SequenceMatcher(None, heard_n, spoken, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks() if block.size >= 4)
    return matched / len(heard_n) >= MATCH
