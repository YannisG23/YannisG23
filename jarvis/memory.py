"""Mémoire long terme de Jarvis (SQLite).

- facts : ce que Jarvis sait de l'utilisateur, avec une importance (1 à 3).
- episodes : le résumé de chaque conversation passée (mémoire « épisodique »).
- conversation_log : le journal brut des échanges, consultable par recherche.
- tasks : la liste de tâches de l'utilisateur.

La recherche est faite en Python : accents ignorés, mots ramenés à leur racine
(« restaurants » trouve « restaurant »), score de type BM25 simplifié.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    importance INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at TEXT
);
"""

_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "au", "aux", "ce", "ces",
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles", "mon", "ma", "mes", "ton", "ta",
    "tes", "son", "sa", "ses", "est", "sont", "que", "qui", "quoi", "en", "pour", "par", "sur",
    "dans", "avec", "pas", "ne", "se", "sa", "moi", "toi", "lui", "leur", "y", "l", "d", "j", "m",
    "t", "s", "c", "qu", "n", "the", "an", "of", "to", "is", "my", "and", "est-ce", "quel", "quelle",
    "fait", "faire", "ai", "as", "avoir", "etre", "suis", "es", "comment", "quand", "ou",
    "va", "vas", "vais", "veux", "veut", "peux", "peut", "dis", "dit", "tout", "tous", "cette", "cet",
    "plus", "tres", "bien", "aussi", "alors", "donc", "mais", "si", "oui", "non", "ok", "jarvis",
    "quelle", "quels", "quelles", "est", "c'est", "rien", "chose", "ca", "cela",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize(text: str) -> str:
    text = text.lower().replace("œ", "oe").replace("æ", "ae").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def stem(word: str) -> str:
    """Racinisation très simple, suffisante pour le français courant."""
    for suffix in ("ements", "ement", "ations", "ation", "euses", "euse", "eurs", "eur",
                   "ions", "ies", "es", "s", "x", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return [stem(w) for w in words if len(w) > 1 and w not in _STOPWORDS]


def _score(query_terms: list[str], docs: list[list[str]]) -> list[float]:
    """BM25 simplifié sur une petite collection."""
    if not docs:
        return []
    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in set(query_terms)}
    scores = []
    for doc in docs:
        score = 0.0
        for term in set(query_terms):
            tf = doc.count(term)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(doc) / avg_len))
        scores.append(score)
    return scores


def similarity(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Fact:
    id: int
    content: str
    category: str
    importance: int
    created_at: str
    updated_at: str

    def render(self) -> str:
        return f"#{self.id} [{self.category}] {self.content}"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Episode:
    id: int
    started_at: str
    ended_at: str
    summary: str

    def render(self) -> str:
        return f"[{self.started_at[:16].replace('T', ' ')}] {self.summary}"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Task:
    id: int
    title: str
    due: str
    done: bool
    created_at: str

    def render(self) -> str:
        due = f" (pour {self.due})" if self.due else ""
        return f"#{self.id} {'[fait] ' if self.done else ''}{self.title}{due}"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


_FACT_COLS = "id, content, category, importance, created_at, updated_at"


class Memory:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._migrate()
            self._db.commit()
        self.on_change = lambda kind: None  # branché par le centre de commande

    def _migrate(self) -> None:
        """Met à niveau une base créée par la première version de Jarvis."""
        fact_cols = {row[1] for row in self._db.execute("PRAGMA table_info(facts)")}
        for col, ddl in [
            ("importance", "INTEGER NOT NULL DEFAULT 2"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
            ("last_used_at", "TEXT"),
            ("use_count", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in fact_cols:
                self._db.execute(f"ALTER TABLE facts ADD COLUMN {col} {ddl}")
        self._db.execute("UPDATE facts SET updated_at = created_at WHERE updated_at = ''")
        log_cols = {row[1] for row in self._db.execute("PRAGMA table_info(conversation_log)")}
        if "session_id" not in log_cols:
            self._db.execute("ALTER TABLE conversation_log ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")

    def _changed(self, kind: str) -> None:
        try:
            self.on_change(kind)
        except Exception:
            pass

    # ------------------------------------------------------------------ facts

    def remember(self, content: str, category: str = "general", importance: int = 2) -> tuple[Fact, bool]:
        """Enregistre un fait. Si un fait très proche existe, il est mis à jour.

        Renvoie (fait, créé) ; créé vaut False si un fait existant a été mis à jour.
        """
        content = content.strip()
        if not content:
            raise ValueError("Le souvenir est vide.")
        category = (category or "general").strip().lower()
        importance = max(1, min(int(importance), 3))
        for fact in self.all_facts(limit=100_000):
            if similarity(fact.content, content) >= 0.75:
                return self.update_fact(fact.id, content, category, max(importance, fact.importance)), False
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO facts (content, category, importance, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (content, category, importance, now, now),
            )
            self._db.commit()
        self._changed("facts")
        return Fact(cur.lastrowid, content, category, importance, now, now), True

    def update_fact(self, fact_id: int, content: str | None = None, category: str | None = None,
                    importance: int | None = None) -> Fact:
        fact = self.get_fact(fact_id)
        if fact is None:
            raise KeyError(f"Aucun fait #{fact_id}")
        fact.content = (content or fact.content).strip()
        fact.category = (category or fact.category).strip().lower()
        fact.importance = max(1, min(int(importance or fact.importance), 3))
        fact.updated_at = _now()
        with self._lock:
            self._db.execute(
                "UPDATE facts SET content = ?, category = ?, importance = ?, updated_at = ? WHERE id = ?",
                (fact.content, fact.category, fact.importance, fact.updated_at, fact.id),
            )
            self._db.commit()
        self._changed("facts")
        return fact

    def get_fact(self, fact_id: int) -> Fact | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_FACT_COLS} FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return Fact(*row) if row else None

    def forget(self, fact_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._db.commit()
        if cur.rowcount:
            self._changed("facts")
        return cur.rowcount > 0

    def all_facts(self, limit: int = 1000) -> list[Fact]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Fact(*row) for row in reversed(rows)]

    def core_facts(self, limit: int = 150) -> list[Fact]:
        """Les faits toujours présents à l'esprit de Jarvis : les plus importants, puis les plus utilisés."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts "
                "ORDER BY importance DESC, use_count DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return sorted((Fact(*row) for row in rows), key=lambda f: (f.category, f.id))

    def search(self, query: str, limit: int = 20, min_score: float = 0.0) -> list[Fact]:
        terms = tokenize(query)
        facts = self.all_facts(limit=100_000)
        if not terms:
            return facts[-limit:]
        scores = _score(terms, [tokenize(f"{f.content} {f.category}") for f in facts])
        ranked = sorted(
            ((s + 0.15 * f.importance, f) for s, f in zip(scores, facts) if s > min_score),
            key=lambda item: -item[0],
        )
        found = [f for _, f in ranked[:limit]]
        self._touch(f.id for f in found)
        return found

    def _touch(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE facts SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                [(_now(), i) for i in ids],
            )
            self._db.commit()

    # --------------------------------------------------------------- episodes

    def add_episode(self, summary: str, started_at: str, ended_at: str | None = None) -> Episode:
        ended_at = ended_at or _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO episodes (started_at, ended_at, summary) VALUES (?, ?, ?)",
                (started_at, ended_at, summary.strip()),
            )
            self._db.commit()
        self._changed("episodes")
        return Episode(cur.lastrowid, started_at, ended_at, summary.strip())

    def recent_episodes(self, limit: int = 6) -> list[Episode]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, started_at, ended_at, summary FROM episodes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Episode(*row) for row in reversed(rows)]

    def search_episodes(self, query: str, limit: int = 5, min_score: float = 0.0) -> list[Episode]:
        with self._lock:
            rows = self._db.execute("SELECT id, started_at, ended_at, summary FROM episodes").fetchall()
        episodes = [Episode(*row) for row in rows]
        terms = tokenize(query)
        if not terms or not episodes:
            return []
        scores = _score(terms, [tokenize(e.summary) for e in episodes])
        ranked = sorted(
            ((s, e) for s, e in zip(scores, episodes) if s > min_score), key=lambda item: -item[0]
        )
        return [e for _, e in ranked[:limit]]

    def delete_episode(self, episode_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
            self._db.commit()
        if cur.rowcount:
            self._changed("episodes")
        return cur.rowcount > 0

    # -------------------------------------------------------------------- log

    def log(self, role: str, content: str, session_id: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO conversation_log (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )
            self._db.commit()

    def session_log(self, session_id: str) -> list[tuple[str, str, str]]:
        with self._lock:
            return self._db.execute(
                "SELECT role, content, created_at FROM conversation_log WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

    def recent_log(self, limit: int = 20) -> list[tuple[str, str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content, created_at FROM conversation_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return list(reversed(rows))

    def search_log(self, query: str, days: int = 30, limit: int = 15) -> list[tuple[str, str, str]]:
        since = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content, created_at FROM conversation_log WHERE created_at >= ? ORDER BY id",
                (since,),
            ).fetchall()
        terms = tokenize(query)
        if not terms:
            return rows[-limit:]
        scores = _score(terms, [tokenize(r[1]) for r in rows])
        ranked = sorted(((s, r) for s, r in zip(scores, rows) if s > 0), key=lambda item: -item[0])
        return sorted((r for _, r in ranked[:limit]), key=lambda r: r[2])

    # ------------------------------------------------------------------ tasks

    def add_task(self, title: str, due: str = "") -> Task:
        title = title.strip()
        if not title:
            raise ValueError("La tâche est vide.")
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO tasks (title, due, created_at) VALUES (?, ?, ?)", (title, due.strip(), now)
            )
            self._db.commit()
        self._changed("tasks")
        return Task(cur.lastrowid, title, due.strip(), False, now)

    def tasks(self, include_done: bool = False) -> list[Task]:
        where = "" if include_done else "WHERE done = 0"
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, title, due, done, created_at FROM tasks {where} "
                "ORDER BY done, CASE WHEN due = '' THEN 1 ELSE 0 END, due, id"
            ).fetchall()
        return [Task(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows]

    def complete_task(self, task_id: int, done: bool = True) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET done = ?, done_at = ? WHERE id = ?",
                (int(done), _now() if done else None, task_id),
            )
            self._db.commit()
        if cur.rowcount:
            self._changed("tasks")
        return cur.rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._db.commit()
        if cur.rowcount:
            self._changed("tasks")
        return cur.rowcount > 0

    # ------------------------------------------------------------------ misc

    def stats(self) -> dict:
        with self._lock:
            count = lambda table: self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return {
                "facts": count("facts"),
                "episodes": count("episodes"),
                "messages": count("conversation_log"),
                "open_tasks": self._db.execute("SELECT COUNT(*) FROM tasks WHERE done = 0").fetchone()[0],
            }

    def close(self) -> None:
        with self._lock:
            self._db.close()
