from __future__ import annotations

import base64
import json
import time
import urllib.request
from types import SimpleNamespace

import pytest

from jarvis.brain import Brain, RefusalError, SentenceSplitter
from jarvis.config import Config
from jarvis.core import Core, is_yes
from jarvis.memory import Memory, similarity, tokenize
from jarvis.tools import registry
from jarvis.tools.google_tools import extract_body
from jarvis.tools.registry import Registry
from jarvis.tools.web_tools import format_weather
from jarvis.voice.speak import clean_for_speech


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_USER_NAME", "Yannis")
    monkeypatch.setenv("JARVIS_CITY", "")
    return Config()


@pytest.fixture
def memory(config):
    mem = Memory(config.memory_db)
    yield mem
    mem.close()


# ---------------------------------------------------------------- faux Claude

def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def response(stop_reason, *content, input_tokens=100):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=10,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage)


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.resp.content:
            if block.type == "text":
                # Le texte arrive par petits morceaux, comme en vrai.
                for i in range(0, len(block.text), 7):
                    yield SimpleNamespace(type="text", text=block.text[i:i + 7])

    def get_final_message(self):
        return self.resp


class FakeClient:
    def __init__(self, *responses, summary=None):
        self.responses = list(responses)
        self.summary = summary
        self.calls = []
        self.create_calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream, create=self._create))

    def _stream(self, **kwargs):
        # Copie superficielle : l'historique continue d'être modifié après l'appel.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return _Stream(self.responses.pop(0))

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        return response("end_turn", text(json.dumps(self.summary or {"summary": "Rien.", "facts": []})))


# ---------------------------------------------------------------- mémoire

def test_tokenize_ignores_accents_and_plurals():
    assert tokenize("Les Restaurants préférés") == tokenize("restaurant prefere")
    assert similarity("Yannis aime le café noir", "yannis aime le cafe noir") == 1.0


def test_memory_remember_search_forget(memory):
    jazz, created = memory.remember("Yannis adore le jazz et Miles Davis", "preference")
    assert created
    memory.remember("La sœur de Yannis s'appelle Léa", "proches", importance=3)
    assert memory.search("musique jazz")[0].id == jazz.id
    assert "Léa" in memory.search("soeur")[0].content
    assert memory.forget(jazz.id)
    assert not memory.forget(jazz.id)
    assert len(memory.all_facts()) == 1


def test_memory_deduplicates_similar_facts(memory):
    first, _ = memory.remember("Yannis habite à Lyon")
    second, created = memory.remember("Yannis habite à Lyon.", importance=3)
    assert not created and second.id == first.id and second.importance == 3
    assert len(memory.all_facts()) == 1


def test_core_facts_prioritize_importance(memory):
    for i in range(5):
        memory.remember(f"détail numéro {i} sur un sujet {i}", importance=1)
    key, _ = memory.remember("Yannis est étudiant en informatique", importance=3)
    assert key.id in {f.id for f in memory.core_facts(limit=2)}


def test_episodes_tasks_and_log(memory):
    memory.add_episode("Yannis a préparé son voyage au Japon.", "2026-09-20T10:00:00")
    assert memory.search_episodes("Japon")[0].summary.startswith("Yannis")
    task = memory.add_task("Appeler le garagiste", "2026-09-30")
    assert memory.complete_task(task.id)
    assert memory.tasks() == [] and len(memory.tasks(include_done=True)) == 1
    memory.log("user", "On parle du voyage au Japon", "s1")
    assert memory.search_log("japon")[0][1] == "On parle du voyage au Japon"


def test_memory_migrates_v1_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, "
        "category TEXT NOT NULL DEFAULT 'general', created_at TEXT NOT NULL);"
        "CREATE TABLE conversation_log (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, "
        "content TEXT NOT NULL, created_at TEXT NOT NULL);"
        "INSERT INTO facts (content, category, created_at) VALUES ('Aime le thé', 'preference', '2026-01-01T00:00:00');"
    )
    db.commit()
    db.close()
    mem = Memory(path)
    fact = mem.all_facts()[0]
    assert fact.content == "Aime le thé" and fact.importance == 2 and fact.updated_at
    mem.log("user", "salut", "s")
    mem.close()


# ---------------------------------------------------------------- outils

def test_registry_definitions_are_sorted_and_valid():
    defs = registry.definitions()
    names = [d["name"] for d in defs]
    assert names == sorted(names)
    for d in defs:
        assert d["input_schema"]["type"] == "object"
        assert d["eager_input_streaming"] is True
        assert set(d["input_schema"]["required"]) <= set(d["input_schema"]["properties"])
    for expected in ["remember", "recall", "update_memory", "search_conversations", "add_task",
                     "open_application", "gmail_list", "calendar_create", "get_weather", "list_processes"]:
        assert expected in names


def test_sensitive_tools_require_confirmation():
    for name in ["run_command", "gmail_send", "calendar_create", "calendar_delete", "write_text_file"]:
        assert registry.get(name).confirm is not None, name


def test_tool_input_validation():
    tool = registry.get("set_timer")
    assert tool.validate({"seconds": 60, "label": "pâtes"}) is None
    assert "manquants" in tool.validate({"seconds": 60})
    assert "type integer" in tool.validate({"seconds": "60", "label": "x"})
    assert "inconnus" in tool.validate({"seconds": 1, "label": "x", "foo": 1})
    assert "l'un de" in registry.get("media_control").validate({"action": "explode"})


# ---------------------------------------------------------------- cerveau

def test_sentence_splitter():
    splitter = SentenceSplitter()
    out = []
    for piece in ["Bonjour Yannis. Il fait ", "beau aujourd'hui ! Tu veux ", "sortir ?"]:
        out += splitter.feed(piece)
    assert out == ["Bonjour Yannis.", "Il fait beau aujourd'hui !"]
    assert splitter.flush() == "Tu veux sortir ?"


def test_brain_streams_sentences(config, memory):
    client = FakeClient(response("end_turn", text("Bonjour Yannis. Tout est opérationnel.")))
    brain = Brain(config, memory, registry, client=client)
    sentences, deltas = [], []
    assert brain.ask("Salut", on_sentence=sentences.append, on_delta=deltas.append) == (
        "Bonjour Yannis. Tout est opérationnel.")
    assert sentences == ["Bonjour Yannis.", "Tout est opérationnel."]
    assert "".join(deltas) == "Bonjour Yannis. Tout est opérationnel."
    call = client.calls[0]
    assert call["model"] == config.model and call["fallbacks"] == "default"
    assert call["thinking"] == {"type": "adaptive"}
    assert "] Salut" in call["messages"][0]["content"]
    types = {t.get("type") for t in call["tools"]}
    assert {"web_search_20260209", "web_fetch_20260209"} <= types
    assert [role for role, *_ in memory.recent_log()] == ["user", "assistant"]


def test_brain_runs_tools(config, memory):
    client = FakeClient(
        response("tool_use", text("Je le note."), tool_use("t1", "remember", {"fact": "Aime le café noir"})),
        response("end_turn", text("C'est retenu.")),
    )
    events = []
    brain = Brain(config, memory, registry, client=client, on_event=lambda k, d: events.append(k))
    assert brain.ask("Retiens que j'aime le café noir") == "Je le note. C'est retenu."
    assert memory.search("café")[0].content == "Aime le café noir"
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and "Mémorisé" in tool_result["content"]
    assert events == ["tool_start", "tool_end"]


def test_brain_rejects_invalid_tool_input(config, memory):
    client = FakeClient(
        response("tool_use", tool_use("t1", "set_timer", {"seconds": 10})),
        response("end_turn", text("Oups.")),
    )
    Brain(config, memory, registry, client=client).ask("minuteur")
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "label" in result["content"]


def test_brain_confirmation_denied(config, memory):
    ran = []
    reg = Registry()

    @reg.tool("danger", confirm=lambda a: "faire un truc risqué")
    def danger() -> str:
        ran.append(True)
        return "fait"

    asked = []
    client = FakeClient(
        response("tool_use", tool_use("t1", "danger", {})),
        response("end_turn", text("D'accord, j'annule.")),
    )
    brain = Brain(config, memory, reg, client=client, confirm=lambda action: asked.append(action) or False)
    brain.ask("vas-y")
    assert asked == ["faire un truc risqué"] and not ran
    assert "refusé" in client.calls[1]["messages"][-1]["content"][0]["content"]


def test_brain_tool_errors_are_reported(config, memory):
    reg = Registry()

    @reg.tool("boom")
    def boom() -> str:
        raise OSError("disque plein")

    client = FakeClient(
        response("tool_use", tool_use("t1", "boom", {}), tool_use("t2", "inconnu", {})),
        response("end_turn", text("Oups.")),
    )
    Brain(config, memory, reg, client=client).ask("go")
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [True, True]
    assert "disque plein" in results[0]["content"]


def test_brain_refusal_rolls_back_turn(config, memory):
    client = FakeClient(response("refusal"), response("end_turn", text("Ok.")))
    brain = Brain(config, memory, registry, client=client)
    with pytest.raises(RefusalError):
        brain.ask("question interdite")
    assert brain.messages == []
    brain.ask("autre chose")
    assert len(client.calls[1]["messages"]) == 1


def test_brain_forces_conclusion_after_too_many_steps(config, memory):
    config.max_tool_steps = 2
    loop = [response("tool_use", tool_use(f"t{i}", "list_timers", {})) for i in range(2)]
    client = FakeClient(*loop, response("end_turn", text("Je m'arrête là.")))
    assert Brain(config, memory, registry, client=client).ask("boucle") == "Je m'arrête là."
    assert "tool_choice" not in client.calls[1] and client.calls[2]["tool_choice"] == {"type": "none"}


def test_brain_injects_relevant_memories(config, memory):
    memory.remember("Le chat de Yannis s'appelle Pixel", "proches")
    client = FakeClient(response("end_turn", text("a")), response("end_turn", text("b")))
    brain = Brain(config, memory, registry, client=client)
    # Faits déjà dans le prompt système : pas de doublon dans le message.
    brain.ask("Comment va mon chat ?")
    assert "Pixel" in client.calls[0]["system"][1]["text"]
    assert "<souvenirs_pertinents>" not in client.calls[0]["messages"][0]["content"]
    # Fait appris après le début de la session : ajouté au message.
    memory.remember("Pixel est un chat roux de trois ans", "proches")
    brain.ask("Il a quel âge Pixel ?")
    assert "trois ans" in client.calls[1]["messages"][-1]["content"]
    assert client.calls[0]["system"] == client.calls[1]["system"]  # préfixe stable pour le cache


def test_consolidation_creates_episode_and_facts(config, memory):
    summary = {"summary": "Yannis a parlé de son projet de voyage au Japon.",
               "facts": [{"fact": "Yannis prépare un voyage au Japon", "category": "projet", "importance": 2}]}
    client = FakeClient(response("end_turn", text("Super projet.")), summary=summary)
    brain = Brain(config, memory, registry, client=client)
    brain.ask("Je vais au Japon en avril")
    old_session = brain.session_id
    brain.consolidate()
    assert memory.recent_episodes()[0].summary == summary["summary"]
    assert memory.search("Japon")[0].content == "Yannis prépare un voyage au Japon"
    assert brain.session_id != old_session and brain.messages == []
    fmt = client.create_calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "voyage au Japon" in brain.system[1]["text"]


# ---------------------------------------------------------------- noyau et centre de commande

def test_core_confirmation_via_dashboard(config, memory):
    core = Core(config, memory=memory, client=FakeClient())
    result = {}
    import threading

    t = threading.Thread(target=lambda: result.update(ok=core.confirm("supprimer un fichier")))
    t.start()
    for _ in range(100):
        if core._confirms:
            break
        time.sleep(0.01)
    confirm_id = next(iter(core._confirms))
    assert core.resolve_confirm(confirm_id, True)
    t.join(2)
    assert result["ok"] is True


def test_dashboard_api(config, memory):
    from jarvis.dashboard.server import Dashboard

    client = FakeClient(response("end_turn", text("Bonjour depuis le centre de commande.")))
    core = Core(config, memory=memory, client=client)
    board = Dashboard(core, port=0)
    board.port = board.server.server_address[1]
    board.start()
    base = f"http://127.0.0.1:{board.port}"

    def call(method, path, body=None, token=board.token):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Jarvis-Token": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as err:
            return err.code, None

    try:
        with urllib.request.urlopen(base + "/", timeout=5) as res:
            assert b"J.A.R.V.I.S." in res.read()
        assert call("GET", "/api/state", token="mauvais")[0] == 401
        status, state = call("GET", "/api/state")
        assert status == 200 and state["user"] == "Yannis" and state["voice_enabled"] is False
        fact = call("POST", "/api/facts", {"fact": "Aime les films de SF", "importance": 3})[1]
        assert call("PUT", f"/api/facts/{fact['id']}", {"fact": "Adore les films de SF"})[1]["content"] == "Adore les films de SF"
        task = call("POST", "/api/tasks", {"title": "Réviser", "due": "2026-10-01"})[1]
        assert call("POST", f"/api/tasks/{task['id']}/toggle", {"done": True})[1]["ok"]
        mem = call("GET", "/api/memory?q=films")[1]
        assert mem["facts"][0]["content"] == "Adore les films de SF" and mem["tasks"][0]["done"]
        assert call("GET", "/api/system")[1]["cpu"] >= 0
        assert call("POST", "/api/ask", {"text": "Salut"})[1]["ok"]
        for _ in range(200):
            if any(e["type"] == "assistant_message" for e in core.bus.history):
                break
            time.sleep(0.01)
        assert any(e["type"] == "assistant_message" and "centre de commande" in e["data"]["text"]
                   for e in core.bus.history)
        assert call("DELETE", f"/api/facts/{fact['id']}")[1]["ok"]
        assert call("GET", "/api/nope")[0] == 404
    finally:
        board.stop()


# ---------------------------------------------------------------- utilitaires

def test_is_yes():
    assert is_yes("Oui vas-y")
    assert is_yes("ok")
    assert not is_yes("non surtout pas")
    assert not is_yes("")


def test_clean_for_speech():
    assert clean_for_speech("**Salut** ! Voir [ici](https://x.com) 🚀") == "Salut ! Voir ici"


def test_extract_body_prefers_plain_text():
    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": enc("<p>Bonjour <b>toi</b></p>")}},
            {"mimeType": "text/plain", "body": {"data": enc("Bonjour toi")}},
        ],
    }
    assert extract_body(payload) == "Bonjour toi"
    assert extract_body({"mimeType": "text/html", "body": {"data": enc("<p>Salut</p><br>ça va")}}) == "Salut\n\nça va"


def test_format_weather():
    data = {
        "current": {"temperature_2m": 18.4, "apparent_temperature": 17.2, "weather_code": 2,
                    "wind_speed_10m": 12.0, "relative_humidity_2m": 60},
        "daily": {"time": ["2026-09-27"], "weather_code": [61], "temperature_2m_min": [11.0],
                  "temperature_2m_max": [19.6], "precipitation_probability_max": [70]},
    }
    out = format_weather("Paris, France", data)
    assert "partiellement nuageux, 18°C" in out and "pluie légère, 11 à 20°C, pluie 70%" in out
