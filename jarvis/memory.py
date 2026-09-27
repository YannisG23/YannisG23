"""Mémoire long terme de Jarvis : faits sur l'utilisateur et journal des conversations (SQLite)."""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Mots trop courants pour servir à une recherche.
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "à", "a", "au", "aux",
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "mon", "ma", "mes", "ton", "ta",
    "tes", "son", "sa", "ses", "est", "que", "qui", "quoi", "en", "pour", "par", "sur", "dans",
    "the", "an", "of", "to", "is", "my",
}


@dataclass
class Fact:
    id: int
    content: str
    category: str
    created_at: str

    def render(self) -> str:
        return f"#{self.id} [{self.category}] {self.content}"


class Memory:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def remember(self, content: str, category: str = "general") -> Fact:
        content = content.strip()
        if not content:
            raise ValueError("Le souvenir est vide.")
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO facts (content, category, created_at) VALUES (?, ?, ?)",
                (content, category.strip().lower() or "general", now),
            )
            self._db.commit()
        return Fact(cur.lastrowid, content, category, now)

    def forget(self, fact_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._db.commit()
        return cur.rowcount > 0

    def all_facts(self, limit: int = 200) -> list[Fact]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, content, category, created_at FROM facts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Fact(*row) for row in reversed(rows)]

    def search(self, query: str, limit: int = 20) -> list[Fact]:
        """Recherche par mots-clés, classée par nombre de mots trouvés."""
        words = [
            w for w in re.findall(r"\w+", query.lower()) if len(w) > 1 and w not in _STOPWORDS
        ]
        if not words:
            return self.all_facts(limit)
        scored: list[tuple[int, Fact]] = []
        for fact in self.all_facts(limit=10_000):
            haystack = f"{fact.content} {fact.category}".lower()
            score = sum(1 for w in words if w in haystack)
            if score:
                scored.append((score, fact))
        scored.sort(key=lambda item: (-item[0], -item[1].id))
        return [fact for _, fact in scored[:limit]]

    def log(self, role: str, content: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                "INSERT INTO conversation_log (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, now),
            )
            self._db.commit()

    def recent_log(self, limit: int = 20) -> list[tuple[str, str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content, created_at FROM conversation_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return list(reversed(rows))

    def close(self) -> None:
        self._db.close()
