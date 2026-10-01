"""Centre de commande : serveur web local (http://127.0.0.1:8765) + flux d'événements en direct.

Sécurité : le serveur n'écoute que sur 127.0.0.1, vérifie l'en-tête Host (contre le
« DNS rebinding ») et exige un jeton secret, généré à chaque lancement, pour toute l'API.
Un site web ouvert dans ton navigateur ne peut donc pas piloter Jarvis.
"""

from __future__ import annotations

import json
import queue
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ..tools import google_tools, system_tools, web_tools

STATIC = Path(__file__).parent / "static"


class _Cache:
    """Petit cache à durée de vie pour les appels lents (Google, météo)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, compute: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        value = compute()
        with self._lock:
            self._data[key] = (time.monotonic(), value)
        return value

    def clear(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class _Server(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR laisserait un deuxième Jarvis écouter le même port sans erreur :
    # deux assistants répondraient alors en même temps. On refuse donc de partager le port.
    allow_reuse_address = sys.platform != "win32"


class Dashboard:
    def __init__(self, core: Any, port: int) -> None:
        self.core = core
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.cache = _Cache()
        self.server = _Server(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#token={self.token}"

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True, name="jarvis-dashboard").start()

    def stop(self) -> None:
        self.server.shutdown()

    # ------------------------------------------------------------------ données

    def state(self) -> dict:
        core, config = self.core, self.core.config
        return {
            "state": core.state,
            "name": config.assistant_name,
            "hint": core.voice.activation_hint if core.voice else "Mode clavier : écris ta demande ci-dessous",
            "user": config.user_name,
            "model": (config.model if not config.uses_subscription or config.model_is_explicit
                      else "Sonnet (abonnement)"),
            "effort": config.effort,
            "voice": config.tts_voice,
            "brain": ("ChatGPT (conversation) + Claude (actions)" if config.gpt_conversation
                      else "Abonnement Claude" if config.uses_subscription else "API Anthropic"),
            # Quel cerveau répond : « claude » ou « chatgpt » (relais Codex quand Claude est en limite d'usage).
            "active_brain": getattr(core.brain, "active_brain", "claude"),
            "codex_delegation": config.codex_delegation,
            "usage": core.usage.summary() if getattr(core, "usage", None) else None,
            "tts": getattr(core.speaker, "engine_label", "Edge (gratuite)"),
            "voice_enabled": core.voice is not None,
            "wake_word": bool(core.voice and core.voice.listener.has_wake_word),
            "google": google_tools.is_connected(config),
            "city": config.city,
            "session_turns": core.brain.turns,
            "context_tokens": core.brain.context_tokens,
            "max_context_tokens": config.max_context_tokens,
            "stats": core.memory.stats(),
            "history": list(core.bus.history),
            "pending_confirms": [
                {"id": c.id, "action": c.action} for c in list(core._confirms.values()) if not c.done.is_set()
            ],
        }

    def system(self) -> dict:
        snapshot = system_tools.system_snapshot()
        snapshot["timers"] = system_tools.active_timers()
        voice = self.core.voice
        snapshot["mic_level"] = round(voice.listener.level, 1) if voice else 0
        return snapshot

    def overview(self) -> dict:
        config, ctx = self.core.config, self.core.brain.ctx
        result: dict[str, Any] = {"weather": None, "events": None, "emails": None, "errors": []}
        if config.city:
            try:
                found = self.cache.get("weather", 600, lambda: web_tools.fetch_weather(config.city, config.language, 3))
                if found:
                    place, data = found
                    cur = data["current"]
                    result["weather"] = {
                        "place": place,
                        "temp": round(cur["temperature_2m"]),
                        "feels": round(cur["apparent_temperature"]),
                        "text": web_tools.describe_weather_code(cur["weather_code"]),
                        "code": cur["weather_code"],
                        "wind": round(cur["wind_speed_10m"]),
                        "days": [
                            {
                                "date": d,
                                "text": web_tools.describe_weather_code(data["daily"]["weather_code"][i]),
                                "min": round(data["daily"]["temperature_2m_min"][i]),
                                "max": round(data["daily"]["temperature_2m_max"][i]),
                                "rain": data["daily"]["precipitation_probability_max"][i],
                            }
                            for i, d in enumerate(data["daily"]["time"])
                        ],
                    }
            except Exception as exc:
                result["errors"].append(f"Météo : {exc}")
        if google_tools.is_connected(config):
            try:
                result["events"] = self.cache.get("events", 120, lambda: google_tools.list_events(ctx, days=2))
            except Exception as exc:
                result["errors"].append(f"Agenda : {exc}")
            try:
                result["emails"] = self.cache.get("emails", 120, lambda: google_tools.list_emails(ctx, max_results=8))
            except Exception as exc:
                result["errors"].append(f"Gmail : {exc}")
        return result

    def memory(self, query: str) -> dict:
        mem = self.core.memory
        facts = mem.search(query, limit=200) if query else mem.all_facts(limit=2000)
        return {
            "facts": [f.to_dict() for f in facts],
            "episodes": [e.to_dict() for e in mem.recent_episodes(limit=30)][::-1],
            "tasks": [t.to_dict() for t in mem.tasks(include_done=True)],
            "stats": mem.stats(),
        }

    # ------------------------------------------------------------------- routes

    def route(self, method: str, path: str, body: dict) -> tuple[int, Any]:
        core, mem = self.core, self.core.memory
        routes: list[tuple[str, str, Callable[..., Any]]] = [
            ("GET", r"/api/state", lambda: self.state()),
            ("GET", r"/api/system", lambda: self.system()),
            ("GET", r"/api/overview", lambda: self.overview()),
            ("POST", r"/api/overview/refresh", lambda: [self.cache.clear(k) for k in ("weather", "events", "emails")] and {"ok": True}),
            ("POST", r"/api/ask", lambda: core.submit(str(body.get("text", "")), "dashboard") and {"ok": True}),
            ("POST", r"/api/briefing", lambda: core.briefing() and {"ok": True}),
            ("POST", r"/api/reset", lambda: core.new_conversation() and {"ok": True}),
            ("POST", r"/api/stop", lambda: core.stop_speaking() or {"ok": True}),
            ("POST", r"/api/listen", self._listen),
            ("POST", r"/api/confirm/(\d+)", lambda i: {"ok": core.resolve_confirm(int(i), bool(body.get("approved")))}),
            ("POST", r"/api/facts", lambda: mem.remember(str(body.get("fact", "")), str(body.get("category", "general")),
                                                        int(body.get("importance", 2)))[0].to_dict()),
            ("PUT", r"/api/facts/(\d+)", lambda i: mem.update_fact(int(i), body.get("fact"), body.get("category"),
                                                                  body.get("importance")).to_dict()),
            ("DELETE", r"/api/facts/(\d+)", lambda i: {"ok": mem.forget(int(i))}),
            ("DELETE", r"/api/episodes/(\d+)", lambda i: {"ok": mem.delete_episode(int(i))}),
            ("POST", r"/api/tasks", lambda: mem.add_task(str(body.get("title", "")), str(body.get("due", ""))).to_dict()),
            ("POST", r"/api/tasks/(\d+)/toggle", lambda i: {"ok": mem.complete_task(int(i), bool(body.get("done", True)))}),
            ("DELETE", r"/api/tasks/(\d+)", lambda i: {"ok": mem.delete_task(int(i))}),
        ]
        for route_method, pattern, handler in routes:
            match = re.fullmatch(pattern, path)
            if match and route_method == method:
                return 200, handler(*match.groups())
        return 404, {"error": "introuvable"}

    def _listen(self) -> dict:
        if self.core.voice is None:
            return {"ok": False, "error": "Mode vocal désactivé"}
        self.core.voice.push_to_talk()
        return {"ok": True}

    # ------------------------------------------------------------------ serveur

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Jarvis"

            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                return host in {f"127.0.0.1:{dashboard.port}", f"localhost:{dashboard.port}"}

            def _authorized(self, query: dict) -> bool:
                token = self.headers.get("X-Jarvis-Token") or (query.get("token") or [""])[0]
                return secrets.compare_digest(token, dashboard.token)

            def _dispatch(self, method: str) -> None:
                if not self._host_ok():
                    return self._send(403, {"error": "hôte refusé"})
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if method == "GET" and not url.path.startswith("/api/"):
                    return self._static(url.path)
                if not self._authorized(query):
                    return self._send(401, {"error": "jeton invalide"})
                if url.path == "/api/events":
                    return self._events()
                body: dict = {}
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    try:
                        body = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
                    except json.JSONDecodeError:
                        return self._send(400, {"error": "JSON invalide"})
                try:
                    if method == "GET" and url.path == "/api/memory":
                        return self._send(200, dashboard.memory((query.get("q") or [""])[0]))
                    status, payload = dashboard.route(method, url.path, body)
                    self._send(status, payload)
                except (KeyError, ValueError) as exc:
                    self._send(400, {"error": str(exc)})
                except Exception as exc:
                    self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

            def _static(self, path: str) -> None:
                name = "index.html" if path in {"/", ""} else path.lstrip("/")
                file = (STATIC / name).resolve()
                if STATIC.resolve() not in file.parents or not file.is_file():
                    return self._send(404, {"error": "introuvable"})
                types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
                self._send(200, file.read_bytes(), types.get(file.suffix, "application/octet-stream"))

            def _events(self) -> None:
                q = dashboard.core.bus.subscribe()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    while True:
                        try:
                            event = q.get(timeout=15)
                            chunk = f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                        except queue.Empty:
                            chunk = ": keepalive\n\n"
                        self.wfile.write(chunk.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    dashboard.core.bus.unsubscribe(q)

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_PUT(self) -> None:
                self._dispatch("PUT")

            def do_DELETE(self) -> None:
                self._dispatch("DELETE")

        return Handler
