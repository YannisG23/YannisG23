"""Activation par le prénom : « Jarvis, mets de la musique » ou « … c'est quoi la météo, Jarvis ? ».

Chaque phrase entendue est transcrite (rapidement), puis on y cherche le nom de l'assistant,
en tolérant les petites erreurs de transcription (« Jarvisse », « Jarvi »…). Le nom doit être
au début ou à la fin de la phrase, là où on le place naturellement quand on s'adresse à
quelqu'un ; « j'ai revu Jarvis dans Iron Man hier soir » ne le réveille donc pas.
"""

from __future__ import annotations

import difflib
import re

from ..memory import normalize

# Mots d'appel qui peuvent précéder le nom sans faire partie de la demande.
_CALLS = {"hey", "he", "hé", "eh", "ok", "okay", "dis", "salut", "coucou", "bonjour", "yo", "allo", "allô"}
_WORD = re.compile(r"[\w'’-]+")


class NameSpotter:
    def __init__(self, name: str, aliases: list[str] | tuple[str, ...] = (), edge_words: int = 3) -> None:
        self.name = name.strip()
        self.variants = [v.split() for v in {normalize(n) for n in (self.name, *aliases) if n.strip()}]
        self.edge_words = edge_words

    def _matches(self, words: list[str], start: int, variant: list[str]) -> bool:
        chunk = words[start:start + len(variant)]
        if len(chunk) != len(variant):
            return False
        heard, expected = "".join(chunk), "".join(variant)
        if heard == expected:
            return True
        if len(heard) < max(3, len(expected) - 2):
            return False
        return difflib.SequenceMatcher(None, heard, expected).ratio() >= 0.75

    def find(self, text: str) -> tuple[bool, str]:
        """Renvoie (nom entendu, demande sans le nom)."""
        spans = [(m.start(), m.end()) for m in _WORD.finditer(text)]
        words = [normalize(text[a:b]) for a, b in spans]
        for variant in self.variants:
            for i in range(len(words)):
                last = i + len(variant) - 1
                near_edge = i < self.edge_words or last >= len(words) - self.edge_words
                if near_edge and self._matches(words, i, variant):
                    before, after = text[: spans[i][0]], text[spans[last][1]:]
                    # Retire « hey », « ok », « dis »… juste avant le nom.
                    head = [w for w in _WORD.findall(before)]
                    if head and all(normalize(w) in _CALLS for w in head):
                        before = ""
                    command = f"{before.strip(' ,;:.!?')} {after.strip(' ,;:.!?')}".strip()
                    return True, command
        return False, ""
