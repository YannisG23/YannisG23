"""Compteur de consommation des cerveaux : quota Claude (abonnement) et dépenses OpenAI (clé API).

Sert à équilibrer : quand le quota Claude se tend, Jarvis passe en mode économie (Haiku, Codex en
priorité pour le code) ; quand le budget OpenAI du mois est atteint, ChatGPT laisse la conversation à
Claude. Les chiffres sont gardés dans ~/.jarvis/usage.json (par jour et par mois).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Compteur partagé de la session (posé par le noyau), lu par les cerveaux et les outils.
current: "UsageTracker | None" = None

# Seuil d'utilisation du quota Claude (fenêtre en cours) à partir duquel on économise.
CLAUDE_ECONOMY_AT = 0.75


class UsageTracker:
    def __init__(self, path: Path, openai_budget: float = 10.0,
                 price_in: float = 0.40, price_out: float = 1.60) -> None:
        self.path = path
        self.openai_budget = openai_budget  # dollars par mois
        self.price_in, self.price_out = price_in, price_out  # dollars par million de jetons
        self._lock = threading.Lock()
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("days", {})
        self.data.setdefault("claude", {})

    # ---------------------------------------------------------------- écriture

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _bucket(self, provider: str) -> dict[str, float]:
        day = self.data["days"].setdefault(datetime.now().strftime("%Y-%m-%d"), {})
        return day.setdefault(provider, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})

    def record(self, provider: str, tokens_in: int = 0, tokens_out: int = 0, cost: float | None = None) -> None:
        """provider : « claude », « gpt » ou « codex ». cost en dollars (calculé pour gpt si absent)."""
        if cost is None and provider == "gpt":
            cost = (tokens_in * self.price_in + tokens_out * self.price_out) / 1_000_000
        with self._lock:
            b = self._bucket(provider)
            b["calls"] += 1
            b["tokens_in"] += int(tokens_in)
            b["tokens_out"] += int(tokens_out)
            b["cost"] = round(b["cost"] + float(cost or 0.0), 6)
            self._save()

    def claude_status(self, status: str, utilization: float | None, resets_at: float | None) -> None:
        with self._lock:
            self.data["claude"] = {"status": status, "utilization": utilization,
                                   "resets_at": resets_at, "at": time.time()}
            self._save()

    # ---------------------------------------------------------------- lecture

    def _sum(self, provider: str, prefix: str) -> dict[str, float]:
        total = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        for day, providers in self.data["days"].items():
            if day.startswith(prefix) and provider in providers:
                for k in total:
                    total[k] += providers[provider].get(k, 0)
        return total

    def today(self, provider: str) -> dict[str, float]:
        return self._sum(provider, datetime.now().strftime("%Y-%m-%d"))

    def month(self, provider: str) -> dict[str, float]:
        return self._sum(provider, datetime.now().strftime("%Y-%m"))

    @property
    def openai_over_budget(self) -> bool:
        return self.openai_budget > 0 and self.month("gpt")["cost"] >= self.openai_budget

    @property
    def claude_utilization(self) -> float | None:
        info = self.data.get("claude") or {}
        resets = info.get("resets_at")
        if resets and resets < time.time():
            return None  # fenêtre réinitialisée depuis
        return info.get("utilization")

    @property
    def claude_tight(self) -> bool:
        """Quota Claude tendu : alerte reçue ou utilisation au-delà du seuil."""
        info = self.data.get("claude") or {}
        resets = info.get("resets_at")
        if resets and resets < time.time():
            return False
        util = info.get("utilization")
        return info.get("status") in {"allowed_warning", "rejected"} or (util is not None and util >= CLAUDE_ECONOMY_AT)

    def summary(self) -> dict[str, Any]:
        info = self.data.get("claude") or {}
        return {
            "claude": {"today": self.today("claude"), "utilization": self.claude_utilization,
                       "status": info.get("status"), "resets_at": info.get("resets_at"),
                       "economy": self.claude_tight},
            "gpt": {"today": self.today("gpt"), "month": self.month("gpt"), "budget": self.openai_budget,
                    "over_budget": self.openai_over_budget},
            "codex": {"today": self.today("codex")},
        }

    def spoken(self) -> str:
        s = self.summary()
        parts = []
        util = s["claude"]["utilization"]
        if util is not None:
            line = f"Quota Claude utilisé à {round(util * 100)} %"
            if s["claude"]["resets_at"]:
                line += f", réinitialisé vers {datetime.fromtimestamp(s['claude']['resets_at']):%H:%M}"
            parts.append(line)
        else:
            parts.append(f"Claude : {int(s['claude']['today']['calls'])} demandes aujourd'hui")
        if s["claude"]["economy"]:
            parts.append("mode économie actif")
        g = s["gpt"]
        parts.append(f"OpenAI : {g['month']['cost']:.2f} $ ce mois-ci sur un budget de {g['budget']:.0f} $")
        parts.append(f"Codex : {int(s['codex']['today']['calls'])} tâches aujourd'hui")
        return ". ".join(parts) + "."
