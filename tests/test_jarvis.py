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
            assert b"Centre de commande" in res.read()
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


def test_first_launch_today_and_greeting(config):
    from datetime import datetime

    from jarvis.app import _first_launch_today, _greeting

    morning = datetime(2026, 9, 28, 8, 30)
    assert _first_launch_today(config, morning)
    assert not _first_launch_today(config, morning.replace(hour=11))
    assert _first_launch_today(config, datetime(2026, 9, 29, 7, 0))
    assert _greeting(config, morning).startswith("Bonjour Yannis")
    assert _greeting(config, morning.replace(hour=20)).startswith("Bonsoir")
    assert "debout" in _greeting(config, morning.replace(hour=2))


# ---------------------------------------------------------------- cerveau abonnement (Claude Code)

import asyncio

import claude_agent_sdk as real_sdk


def _stream_text(text):
    events = [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
    events += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + 6]}}
               for i in range(0, len(text), 6)]
    events += [{"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]
    return [real_sdk.StreamEvent(uuid=f"u{i}", session_id="s", event=e) for i, e in enumerate(events)] + [
        real_sdk.AssistantMessage(content=[real_sdk.TextBlock(text=text)], model="claude-opus-5")]


def _result(is_error=False, **extra):
    return real_sdk.ResultMessage(subtype="error" if is_error else "success", duration_ms=1, duration_api_ms=1,
                                  is_error=is_error, num_turns=1, session_id="s",
                                  usage={"input_tokens": 50, "cache_read_input_tokens": 4000, "output_tokens": 20},
                                  **extra)


class FakeSDK:
    """claude_agent_sdk avec un faux ClaudeSDKClient : types, options et outils MCP restent les vrais."""

    def __init__(self, *turns, summary=None):
        self.turns = list(turns)
        self.summary = summary
        self.options = []
        self.prompts = []
        sdk = self

        class Client:
            def __init__(self, options):
                sdk.options.append(options)

            async def connect(self):
                pass

            async def query(self, prompt):
                sdk.prompts.append(prompt)

            async def receive_response(self):
                for message in sdk.turns.pop(0):
                    yield message

            async def interrupt(self):
                pass

            async def disconnect(self):
                pass

        self.ClaudeSDKClient = Client

    def __getattr__(self, name):
        return getattr(real_sdk, name)

    async def query(self, prompt, options):
        self.prompts.append(prompt)
        yield _result(structured_output=self.summary)


@pytest.fixture
def sub_brain(config, memory, monkeypatch):
    from jarvis.brain_subscription import SubscriptionBrain

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ne-doit-pas-servir")
    made = []

    def make(*turns, summary=None, **kwargs):
        sdk = FakeSDK(*turns, summary=summary)
        brain = SubscriptionBrain(config, memory, registry, sdk=sdk, **kwargs)
        made.append(brain)
        return brain, sdk

    yield make
    for brain in made:
        brain.close()


def test_subscription_brain_streams_and_uses_subscription(sub_brain, memory):
    import os

    memory.remember("Yannis joue de la guitare", "loisirs", importance=3)
    brain, sdk = sub_brain([*_stream_text("Salut Yannis. Prêt à jouer ?"), _result()])
    sentences, deltas = [], []
    reply = brain.ask("Salut", on_sentence=sentences.append, on_delta=deltas.append)
    assert reply == "Salut Yannis. Prêt à jouer ?"
    assert sentences == ["Salut Yannis.", "Prêt à jouer ?"]
    assert "".join(deltas) == reply
    assert "ANTHROPIC_API_KEY" not in os.environ  # sinon Claude Code facturerait l'API
    opts = sdk.options[0]
    # Personnalité et mémoire passent par un fichier (limite de longueur de ligne de commande sous Windows).
    assert opts.system_prompt["type"] == "file"
    prompt = open(opts.system_prompt["path"], encoding="utf-8").read()
    assert "Tu es Jarvis" in prompt and "guitare" in prompt
    assert "mcp__jarvis__remember" in opts.allowed_tools and "WebSearch" in opts.allowed_tools
    assert opts.tools == ["WebSearch", "WebFetch"] and opts.setting_sources == []
    # Sans JARVIS_MODEL dans .env : Sonnet, rapide à la voix.
    assert opts.include_partial_messages and opts.model == "sonnet"
    assert "] Salut" in sdk.prompts[0]
    assert brain.turns == 1 and brain.context_tokens == 4070


def test_subscription_retries_then_explains_failure(sub_brain, monkeypatch):
    from jarvis import brain_subscription
    from jarvis.brain import BrainError

    monkeypatch.setenv("JARVIS_MODEL", "claude-opus-5")
    monkeypatch.setattr(brain_subscription, "auth_status", lambda: {"loggedIn": False})
    events = []
    brain, sdk = sub_brain([*_stream_text("Me revoilà."), _result()],
                           on_event=lambda kind, data: events.append((kind, data)))
    assert brain._model == "claude-opus-5"
    failures = {"left": 1}
    Client = sdk.ClaudeSDKClient

    class Flaky(Client):
        async def connect(self):
            if failures["left"]:
                failures["left"] -= 1
                raise real_sdk.ProcessError("Command failed", exit_code=1, stderr="model not available")

    sdk.ClaudeSDKClient = Flaky
    # Premier échec : nouvel essai automatique, avec le modèle de l'abonnement.
    assert brain.ask("Salut") == "Me revoilà."
    assert brain._model is None and sdk.options[-1].model is None
    assert any(k == "error" and "model not available" in d["message"] for k, d in events)
    assert "model not available" in (brain.config.home / "erreurs.log").read_text(encoding="utf-8")
    # Deux échecs de suite : message clair (ici, compte non connecté).
    failures["left"] = 2
    brain._client = None  # connexion perdue : il devra se reconnecter
    with pytest.raises(BrainError, match="connexion point bat"):
        brain.ask("Tu es là ?")


def test_subscription_tools_run_with_confirmation(sub_brain, memory):
    asked = []
    brain, _ = sub_brain(confirm=lambda action: asked.append(action) or False)
    out = asyncio.run(brain._tool_handlers["remember"]({"fact": "Yannis aime les mangas"}))
    assert "Mémorisé" in out["content"][0]["text"] and memory.search("mangas")
    out = asyncio.run(brain._tool_handlers["run_command"]({"command": "echo hi"}))
    assert "refusé" in out["content"][0]["text"] and asked


def test_subscription_limit_is_explained(sub_brain):
    from jarvis.brain import BrainError

    info = real_sdk.RateLimitInfo(status="rejected")
    brain, sdk = sub_brain([real_sdk.RateLimitEvent(rate_limit_info=info, uuid="r", session_id="s"),
                            _result(is_error=True, result="limit reached")])
    with pytest.raises(BrainError, match="limite"):
        brain.ask("Salut")
    assert sdk.turns == []  # la réponse a été lue jusqu'au bout


def test_subscription_consolidation(sub_brain, memory):
    summary = {"summary": "Yannis a parlé de son concert.", "facts": [
        {"fact": "Yannis va à un concert de rap samedi", "category": "loisirs", "importance": 2}]}
    brain, sdk = sub_brain([*_stream_text("Génial !"), _result()], summary=summary)
    brain.ask("Je vais à un concert samedi")
    brain.consolidate()
    assert memory.recent_episodes()[0].summary == summary["summary"]
    assert memory.search("concert")[0].content.startswith("Yannis va")
    assert brain.turns == 0


# ---------------------------------------------------------------- voix et interruption

def test_elevenlabs_quota_falls_back_to_free_voice(monkeypatch):
    from jarvis.voice import speak

    def refuse(*args, **kwargs):
        raise speak.ElevenLabsQuotaError("quota épuisé")

    monkeypatch.setattr(speak, "synth_elevenlabs", refuse)
    speaker = object.__new__(speak.Speaker)  # sans ouvrir la sortie audio
    speaker.elevenlabs = {"api_key": "k", "voice_id": "v", "model": "m"}
    speaker.openai = None
    warnings = []
    speaker.on_warning = warnings.append
    assert speaker._synth_premium("Bonjour") is None
    assert speaker.elevenlabs is None and "quota épuisé" in warnings[0]


def test_api_brain_interrupt_rolls_back(config, memory):
    from jarvis.brain import Interrupted

    client = FakeClient(response("end_turn", text("Une très longue réponse qui n'en finit pas.")))
    brain = Brain(config, memory, registry, client=client)
    with pytest.raises(Interrupted):
        brain.ask("Raconte", on_delta=lambda d: brain.interrupt())
    assert brain.messages == [] and brain.turns == 0


# ---------------------------------------------------------------- activation par le prénom

def test_name_spotter_natural_phrases():
    from jarvis.voice.wakename import NameSpotter

    spot = NameSpotter("Jarvis")
    assert spot.find("Jarvis, mets de la musique.") == (True, "mets de la musique")
    assert spot.find("Il fait quel temps demain Jarvis ?") == (True, "Il fait quel temps demain")
    assert spot.find("Dis Jarvis, tu peux baisser le son ?") == (True, "tu peux baisser le son")
    assert spot.find("Jarvisse, ouvre Spotify") == (True, "ouvre Spotify")  # erreur de transcription
    assert spot.find("Jarvis ?") == (True, "")
    # Le nom au milieu d'une phrase qui ne s'adresse pas à lui ne le réveille pas.
    assert spot.find("J'ai revu le film avec Jarvis dans Iron Man hier soir, c'était bien")[0] is False
    assert spot.find("le service est fermé")[0] is False
    assert NameSpotter("Nova").find("Nova, lance un minuteur") == (True, "lance un minuteur")
    orion = NameSpotter("Orion")
    assert orion.find("Orion, quelle heure est-il ?") == (True, "quelle heure est-il")
    assert orion.find("Orian, mets la musique") == (True, "mets la musique")  # erreur de transcription
    assert orion.find("On voit la constellation d'Orion ce soir dans le ciel bien dégagé")[0] is False
    assert NameSpotter("Tony Stark").find("Tony Stark, allume la lumière") == (True, "allume la lumière")


def test_voice_loop_submits_command_said_with_name(config, memory):
    import numpy as np

    from jarvis.voice.loop import VoiceLoop

    class Listener:
        has_wake_word = False
        wake_threshold = 0.5
        level = 0.0

        def __init__(self):
            self.heard = ["on regarde un film ce soir", "Jarvis, mets du rap"]

        def record_utterance(self, start_timeout, max_seconds):
            return np.zeros(16000, dtype=np.int16)

        def transcribe(self, audio, fast=False):
            return self.heard[0]

        def flush(self):
            pass

    core = Core(config, memory=memory, client=FakeClient())
    submitted = []
    core.submit = lambda text, source="text": submitted.append((text, source))
    loop = VoiceLoop(core, Listener())
    assert loop._called() == (False, "")  # conversation sans son nom : ignorée
    loop.listener.heard.pop(0)
    assert loop._called() == (True, "mets du rap")
    assert "par mon nom" in loop.activation_hint
    # Après un changement de nom, c'est le nouveau nom qui le réveille.
    loop.rename("Kali", [])
    assert loop.listener.name == "Kali"
    loop.listener.heard = ["Jarvis, mets du rap"]
    assert loop._called() == (False, "")
    loop.listener.heard = ["Kali, mets du rap"]
    assert loop._called() == (True, "mets du rap")


# ---------------------------------------------------------------- changer de nom à la voix

def test_clean_name():
    from jarvis.tools.identity_tools import clean_name

    assert clean_name("  kali. ") == "Kali"
    assert clean_name("« tony stark »") == "Tony Stark"
    for bad in ["", "x", "R2D2!!", "un nom beaucoup beaucoup trop long pour un assistant", "a b c d"]:
        with pytest.raises(ValueError):
            clean_name(bad)


def test_rename_by_voice_updates_everything(config, memory):
    client = FakeClient(
        response("tool_use", tool_use("t1", "rename_assistant", {"new_name": "kali", "aliases": ["Kaly"]})),
        response("end_turn", text("C'est noté.")),
    )
    core = Core(config, memory=memory, client=client)
    core.brain.confirm = lambda action: action == "prendre le nom « kali »"

    class Voice:
        renamed = None

        def rename(self, name, aliases):
            Voice.renamed = (name, aliases)

    core.voice = Voice()
    old_session = core.brain.session_id
    core.ask("À partir de maintenant tu t'appelles Kali", timeout=5)
    assert config.assistant_name == "Kali"
    assert Voice.renamed == ("Kali", ["Kaly"])
    assert core.brain.session_id != old_session  # nouvelle conversation avec la nouvelle personnalité
    assert "Tu es Kali" in core.brain.system[0]["text"]
    # Conservé après un redémarrage, même si le .env dit autre chose.
    assert Config().assistant_name == "Kali" and Config().name_aliases == ["Kaly"]


def test_first_launch_lets_the_assistant_choose_its_name(config, memory, monkeypatch):
    from jarvis.app import NAMING_PROMPT, _needs_name

    monkeypatch.delenv("JARVIS_NAME", raising=False)
    assert _needs_name(config)
    config.save_state(naming_done=True)
    assert not _needs_name(config)  # une seule fois
    config.save_state(naming_done=False)
    monkeypatch.setenv("JARVIS_NAME", "Friday")
    assert not _needs_name(config)  # nom imposé dans .env
    # La consigne interne ne s'affiche pas comme un message de l'utilisateur.
    core = Core(config, memory=memory, client=FakeClient(response("end_turn", text("Je m'appelle Vega."))))
    core.ask(NAMING_PROMPT.format(user="Yannis"), source="system", timeout=5)
    kinds = [e["type"] for e in core.bus.history]
    assert "user_message" not in kinds and "assistant_message" in kinds


def test_find_claude_cli_rejects_windows_cmd_shim(tmp_path, monkeypatch):
    from jarvis import brain_subscription as bs

    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off")
    native = tmp_path / "claude.exe"
    monkeypatch.setattr(bs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(bs, "_claude_candidates", lambda: [shim])
    assert bs.find_claude_cli() is None  # le script npm est refusé par le kit de Claude
    native.write_text("")
    monkeypatch.setattr(bs, "_claude_candidates", lambda: [shim, native])
    assert bs.find_claude_cli() == str(native)


# ---------------------------------------------------------------- routines

def test_routines_examples_copied_once(config, memory):
    from jarvis.tools import ToolContext
    from jarvis.tools.routine_tools import routines_dir

    ctx = ToolContext(config=config, memory=memory)
    listing = registry.get("list_routines").run(ctx, {})
    for title in ["Mode révision", "Routine du matin", "Mode soirée"]:
        assert title in listing
    folder = routines_dir(config)
    assert (folder / "mode-revision.md").exists()
    # Supprimées par l'utilisateur, les routines d'exemple ne reviennent pas.
    for path in folder.glob("*.md"):
        path.unlink()
    assert "Aucune routine" in registry.get("list_routines").run(ctx, {})


def test_run_routine_returns_steps(config, memory):
    from jarvis.tools import ToolContext

    ctx = ToolContext(config=config, memory=memory)
    run = registry.get("run_routine")
    for said in ["mode révision", "Mode Revision", "révision", "mode revison"]:
        out = run.run(ctx, {"name": said})
        assert "Mode révision" in out and "25 minutes" in out and "5 minutes" in out, said
    assert "Aucune routine" in run.run(ctx, {"name": "karaoké"})


def test_create_and_delete_routine(config, memory):
    from jarvis.tools import ToolContext
    from jarvis.tools.routine_tools import parse_routine, routines_dir

    ctx = ToolContext(config=config, memory=memory)
    create = registry.get("create_routine")
    assert create.validate({"name": "x", "description": "y", "steps": "pas une liste"})
    out = create.run(ctx, {"name": "Mode sport", "description": "Se motiver.",
                           "steps": ["Lance une playlist énergique.", "  ", "Démarre un minuteur de 30 minutes."]})
    assert "créée avec 2 étape(s)" in out
    path = routines_dir(config) / "mode-sport.md"
    routine = parse_routine("mode-sport", path.read_text(encoding="utf-8"))
    assert routine.title == "Mode sport" and routine.description == "Se motiver."
    assert routine.steps == ["Lance une playlist énergique.", "Démarre un minuteur de 30 minutes."]
    assert "playlist énergique" in registry.get("run_routine").run(ctx, {"name": "sport"})
    assert "mise à jour" in create.run(ctx, {"name": "mode sport", "description": "d", "steps": ["a"]})

    delete = registry.get("delete_routine")
    assert delete.confirm is not None
    assert "mode sport" in delete.confirm({"name": "mode sport"})
    assert "exactement" in delete.run(ctx, {"name": "sport"})  # pas de suppression approximative
    assert "supprimée" in delete.run(ctx, {"name": "Mode sport"})
    assert not path.exists()


def test_parse_routine_accepts_hand_written_files():
    from jarvis.tools.routine_tools import parse_routine

    text = "# Mode ménage\n> Ranger vite.\n\n- Mets de la musique\n* Minuteur de 15 minutes\n  pour chaque pièce\nFélicite-moi"
    r = parse_routine("mode-menage", text)
    assert r.title == "Mode ménage" and r.description == "Ranger vite."
    assert r.steps == ["Mets de la musique", "Minuteur de 15 minutes pour chaque pièce", "Félicite-moi"]


def test_brain_learns_routine_by_voice(config, memory):
    client = FakeClient(
        response("tool_use", tool_use("t1", "create_routine", {
            "name": "Mode lecture", "description": "Lire au calme.",
            "steps": ["Lance de la musique classique.", "Minuteur de 45 minutes."]})),
        response("end_turn", text("C'est retenu.")),
    )
    core = Core(config, memory=memory, client=client)
    core.ask("Apprends cette routine : mode lecture, musique classique puis 45 minutes de lecture", timeout=5)
    assert (config.home / "routines" / "mode-lecture.md").exists()
    assert "run_routine" in core.brain.system[0]["text"]


# ---------------------------------------------------------------- périphériques audio et arrêt

def test_audio_device_resolution(monkeypatch):
    from jarvis.voice import devices

    fake = [
        {"index": 0, "name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "max_output_channels": 0},
        {"index": 1, "name": "Micro (Blue Yeti)", "max_input_channels": 1, "max_output_channels": 0},
        {"index": 2, "name": "Casque (HyperX Cloud)", "max_input_channels": 0, "max_output_channels": 2},
    ]
    fake.append({"index": 3, "name": "Micro (Blue Yeti)", "max_input_channels": 1, "max_output_channels": 0,
                 "hostapi": 2})
    for d in fake[:3]:
        d["hostapi"] = 0
    monkeypatch.setattr(devices, "_devices", lambda: fake)
    monkeypatch.setattr(devices, "_default_hostapi", lambda: 0)
    assert devices.resolve("", "input") is None
    assert devices.resolve("yeti", "input") == 1
    assert devices.resolve("1", "input") == 1
    assert devices.resolve("hyperx", "output") == 2
    with pytest.raises(ValueError):
        devices.resolve("hyperx", "input")  # un casque sans micro n'est pas une entrée
    with pytest.raises(ValueError):
        devices.resolve("7", "output")


def test_shutdown_never_hangs_on_slow_consolidation(config, memory):
    core = Core(config, memory=memory, client=FakeClient())
    core.brain.messages.append({"role": "user", "content": "Salut"})
    core.brain.consolidate = lambda: time.sleep(30)
    start = time.monotonic()
    core.shutdown(consolidate_timeout=0.2)
    assert time.monotonic() - start < 5


def test_exit_phrases():
    from jarvis.app import _EXIT

    for phrase in ("quitter", "Quit", "au revoir", "Au revoir Nova !", "eteins-toi", "arrête-toi Jarvis", "ferme-toi"):
        assert _EXIT.match(phrase), phrase
    for phrase in ("stop", "quitte pas", "au revoir à tous les deux", "quelle heure est-il"):
        assert not _EXIT.match(phrase), phrase


def test_open_application_refuse_injection_shell(monkeypatch):
    from jarvis.tools import system_tools

    monkeypatch.setattr(system_tools, "SYSTEM", "Windows")
    lancé = []
    monkeypatch.setattr(system_tools.subprocess, "Popen", lambda *a, **k: lancé.append(a))
    assert "invalide" in system_tools.open_application('x" & calc & "')
    assert not lancé
    system_tools.open_application("Spotify")
    assert len(lancé) == 1


def test_open_path_refuse_les_executables(tmp_path):
    from jarvis.tools import file_tools

    script = tmp_path / "evil.bat"
    script.write_text("echo hi")
    assert "n'ouvre pas" in file_tools.open_path(str(script))


def test_subscription_ferme_l_ancienne_connexion_avant_de_reessayer(sub_brain):
    brain, sdk = sub_brain([*_stream_text("Ok."), _result()])
    Client = sdk.ClaudeSDKClient
    closed = []

    class Broken(Client):
        async def query(self, prompt):
            raise real_sdk.ProcessError("Command failed", exit_code=1, stderr="boom")

        async def disconnect(self):
            closed.append(self)

    sdk.ClaudeSDKClient = Broken
    orig = sdk.ClaudeSDKClient
    calls = {"n": 0}

    def factory(options):
        calls["n"] += 1
        return orig(options) if calls["n"] == 1 else Client(options)

    sdk.ClaudeSDKClient = factory
    assert brain.ask("Salut") == "Ok."
    assert len(closed) == 1  # le processus Claude Code du premier essai n'est pas laissé orphelin


def test_core_worker_survit_a_une_erreur_de_consolidation(config, memory):
    core = Core(config, memory=memory, client=FakeClient())

    def boom():
        raise RuntimeError("résumé impossible")

    core._consolidate = boom
    bad = core.new_conversation()
    assert bad.done.wait(2) and bad.ok is False
    core._consolidate = lambda: None
    good = core.new_conversation()
    assert good.done.wait(2) and good.ok  # le thread de travail tourne toujours


def test_voice_loop_survit_a_une_erreur_audio():
    from types import SimpleNamespace

    from jarvis.voice.loop import VoiceLoop

    events = []

    class Listener:
        name = "Jarvis"
        has_wake_word = False
        calls = 0

        def flush(self):
            pass

        def listen(self, start_timeout):
            Listener.calls += 1
            if Listener.calls == 1:
                raise OSError("micro débranché")
            return "bonjour"

    core = SimpleNamespace(
        config=SimpleNamespace(assistant_name="Jarvis", name_aliases=[], wake_by_name=False, barge_in=False),
        bus=SimpleNamespace(publish=lambda kind, data=None: events.append(kind)),
        speaker=SimpleNamespace(wait=lambda timeout=0: None, speaking=False),
        state="idle", set_state=lambda s: None, busy=False, awaiting_follow_up=False)
    loop = VoiceLoop(core, Listener())
    loop.start()
    try:
        assert loop.listen_once(timeout=1) == ""  # l'erreur ne bloque pas l'appelant
        assert loop.listen_once(timeout=1) == "bonjour"  # et la boucle tourne toujours
        assert "error" in events
    finally:
        loop.stop()


# ---------------------------------------------------------------- Codex

def test_codex_absent(monkeypatch):
    from jarvis.tools import codex_tools

    monkeypatch.setattr(codex_tools, "find_codex", lambda: None)
    assert "connexion-codex.bat" in codex_tools.ask_codex("Salut")


def test_codex_lecture_seule_par_stdin(monkeypatch, tmp_path):
    from pathlib import Path

    from jarvis.tools import codex_tools

    seen = {}

    def fake_run(command, input, **kwargs):
        seen.update(command=command, input=input, cwd=kwargs["cwd"], env=kwargs["env"])
        Path(command[command.index("--output-last-message") + 1]).write_text("Deuxième avis : ok.", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="journal", stderr="")

    monkeypatch.setattr(codex_tools, "find_codex", lambda: "codex")
    monkeypatch.setattr(codex_tools.subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-voix")
    question = 'Ton avis ? " & calc'
    assert codex_tools.ask_codex(question, str(tmp_path)) == "Deuxième avis : ok."
    assert seen["input"] == question and question not in seen["command"]  # jamais dans la ligne de commande
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "read-only"
    assert seen["cwd"] == str(tmp_path.resolve())
    assert "OPENAI_API_KEY" not in seen["env"]  # Codex reste sur l'abonnement ChatGPT


def test_codex_task_demande_confirmation_et_refuse_les_chemins_douteux(monkeypatch):
    from jarvis.tools import codex_tools

    assert registry.get("codex_task").confirm is not None
    assert registry.get("ask_codex").confirm is None
    monkeypatch.setattr(codex_tools, "find_codex", lambda: "codex")
    assert codex_tools.codex_task("corrige", 'C:\\x" & calc') == "Chemin de dossier invalide."
    assert "introuvable" in codex_tools.codex_task("corrige", "/dossier/qui/n/existe/pas")


def test_voix_openai(monkeypatch):
    import numpy as np

    from jarvis.voice import speak

    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, json=json)
        return SimpleNamespace(status_code=200, content=b"mp3", raise_for_status=lambda: None)

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(speak, "_decode_mp3", lambda data: np.zeros(10, dtype=np.float32))
    audio = speak.synth_openai("Bonjour", "sk", "ash", "gpt-4o-mini-tts", "chaleureux")
    assert len(audio) == 10
    assert sent["url"].endswith("/audio/speech")
    assert sent["json"]["voice"] == "ash" and sent["json"]["instructions"] == "chaleureux"

    def refused(url, headers, json, timeout):
        return SimpleNamespace(status_code=429, json=lambda: {"error": {"message": "quota"}}, text="")

    monkeypatch.setattr(requests, "post", refused)
    with pytest.raises(speak.ElevenLabsQuotaError):
        speak.synth_openai("Bonjour", "sk", "ash", "gpt-4o-mini-tts")


def test_scan_des_micros(monkeypatch):
    import sys
    from types import ModuleType

    import numpy as np

    from jarvis.voice import devices

    fake = [
        {"index": 1, "name": "Sonar - Microphone", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
        {"index": 2, "name": "Micro (G435)", "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
        {"index": 3, "name": "Casque", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    ]
    levels = {1: 9, 2: 800}
    sd = ModuleType("sounddevice")
    sd.rec = lambda frames, samplerate, channels, dtype, device: np.full((frames, 1), levels[device], dtype=np.int16)
    sd.wait = lambda: None
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(devices, "_devices", lambda: fake)
    monkeypatch.setattr(devices, "_default_hostapi", lambda: 0)
    results = devices.scan(seconds=0.01)
    assert [r[0] for r in results] == [2, 1]  # le micro qui capte le plus en premier, les sorties ignorées


# ---------------------------------------------------------------- cerveau hybride Claude + ChatGPT

def _limit_turn(resets_at=None):
    info = real_sdk.RateLimitInfo(status="rejected", resets_at=resets_at)
    return [real_sdk.RateLimitEvent(rate_limit_info=info, uuid="r", session_id="s"),
            _result(is_error=True, result="limit reached")]


def test_relais_codex_quand_claude_est_en_limite(sub_brain, monkeypatch):
    import time as _time
    from jarvis.tools import codex_tools

    prompts = []
    monkeypatch.setattr(codex_tools, "find_codex", lambda: "codex")
    monkeypatch.setattr(codex_tools, "exec_codex",
                        lambda prompt, folder="", write=False, timeout=0: prompts.append((prompt, write)) or "Il fait beau.")
    events, spoken = [], []
    reset = _time.time() + 3600
    brain, sdk = sub_brain([*_stream_text("Bonjour Yannis."), _result()], _limit_turn(reset),
                           [*_stream_text("Me revoilà."), _result()],
                           on_event=lambda kind, data: events.append((kind, data)))
    brain.ask("Salut")
    reply = brain.ask("Quel temps demain ?", on_sentence=spoken.append)
    assert "passe sur ChatGPT" in reply and reply.endswith("Il fait beau.") and spoken[-1] == "Il fait beau."
    assert brain.active_brain == "chatgpt" and ("brain", {"active": "chatgpt", "until": reset}) in events
    prompt, write = prompts[0]
    assert write is False and "Salut" in prompt and "Bonjour Yannis." in prompt and "Quel temps demain ?" in prompt
    # Tant que la limite dure, Claude n'est pas relancé (le 3e tour de la file n'est pas consommé).
    assert brain.ask("Et après-demain ?") == "Il fait beau." and len(sdk.turns) == 1
    # Heure de réinitialisation passée : Claude reprend, avec les échanges de ChatGPT en mémoire.
    brain._relay_until = _time.time() - 1
    assert brain.ask("Merci") == "Me revoilà."
    assert brain.active_brain == "claude" and "relais_chatgpt" in sdk.prompts[-1] and "Il fait beau." in sdk.prompts[-1]
    assert events[-1] == ("brain", {"active": "claude", "until": None})


def test_relais_sans_codex_garde_le_comportement_actuel(sub_brain, monkeypatch):
    from jarvis.brain import BrainError
    from jarvis.tools import codex_tools

    monkeypatch.setattr(codex_tools, "find_codex", lambda: None)
    brain, _ = sub_brain(_limit_turn())
    with pytest.raises(BrainError, match="limite"):
        brain.ask("Salut")
    assert brain.active_brain == "claude"


def test_relais_codex_en_echec_garde_le_message_de_limite(sub_brain, monkeypatch):
    from jarvis.brain import BrainError
    from jarvis.tools import codex_tools

    def boom(*args, **kwargs):
        raise codex_tools.CodexError("Codex a échoué")

    monkeypatch.setattr(codex_tools, "find_codex", lambda: "codex")
    monkeypatch.setattr(codex_tools, "exec_codex", boom)
    brain, _ = sub_brain(_limit_turn())
    with pytest.raises(BrainError, match="limite"):
        brain.ask("Salut")
    assert brain.active_brain == "claude" and brain._relay_until is None


def test_codex_sous_processus_simule_erreur_de_connexion(monkeypatch, tmp_path):
    from jarvis.tools import codex_tools

    monkeypatch.setattr(codex_tools, "find_codex", lambda: "codex")
    monkeypatch.setattr(codex_tools.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="please login"))
    with pytest.raises(codex_tools.CodexError, match="connexion-codex"):
        codex_tools.exec_codex("Salut", str(tmp_path))
    assert "connexion-codex" in codex_tools.ask_codex("Salut", str(tmp_path))  # l'outil, lui, renvoie le texte


def test_delegation_codex_ajuste_la_persona(config, memory, monkeypatch):
    def persona(mode):
        monkeypatch.setenv("JARVIS_CODEX_DELEGATION", mode)
        return Brain(Config(), memory, registry, client=FakeClient()).system[0]["text"]

    assert Config().codex_delegation == "auto"
    monkeypatch.setenv("JARVIS_CODEX_DELEGATION", "n'importe quoi")
    assert Config().codex_delegation == "auto"
    off, auto, maxi = persona("off"), persona("auto"), persona("max")
    assert "ne lui délègue jamais" in off and "ne lui délègue jamais" not in auto
    assert "grosses tâches de code" in auto and "même modestes" in maxi and "même modestes" not in auto
    # Commandes vocales : « demande à ChatGPT / à Codex » et « deuxième avis » sont dans la persona.
    for phrase in ("Demande à ChatGPT / à Codex", "Deuxième avis", "compare", "ask_codex"):
        assert phrase in auto
    desc = registry.get("ask_codex").description
    assert "quota Claude" in desc and "deuxième avis" in desc and "relecture" in desc
    assert "quota Claude" in registry.get("codex_task").description


def test_tableau_de_bord_indique_le_cerveau_actif(config, memory):
    from jarvis.dashboard.server import Dashboard

    core = Core(config, memory=memory, client=FakeClient())
    board = Dashboard(core, port=0)
    assert board.state()["active_brain"] == "claude" and board.state()["codex_delegation"] == "auto"
    core.brain.active_brain = "chatgpt"
    assert board.state()["active_brain"] == "chatgpt"
def test_app_assets_and_flag():
    from pathlib import Path

    static = Path(__file__).parent.parent / "jarvis" / "dashboard" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    for name in ("sphere.js", "simple.css", "app.js"):
        assert name in html and (static / name).exists()
    assert "https://" not in (static / "sphere.js").read_text(encoding="utf-8")
    import jarvis.appwindow  # importable sans pywebview (import paresseux)


def test_api_fenetre_sans_attribut_public_recursif():
    from jarvis.appwindow import WindowApi

    api = WindowApi()
    api._window = object()
    # pywebview expose récursivement les attributs publics : seules des méthodes doivent l'être.
    public = [n for n in vars(api) if not n.startswith("_")]
    assert public == []
