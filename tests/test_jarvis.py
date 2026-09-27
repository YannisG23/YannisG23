from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from jarvis.app import is_yes
from jarvis.brain import Brain, RefusalError
from jarvis.config import Config
from jarvis.memory import Memory
from jarvis.tools import registry
from jarvis.tools.google_tools import extract_body
from jarvis.tools.registry import Registry
from jarvis.tools.web_tools import format_weather
from jarvis.voice.speak import clean_for_speech


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_USER_NAME", "Yannis")
    return Config()


@pytest.fixture
def memory(config):
    mem = Memory(config.memory_db)
    yield mem
    mem.close()


# ---------------------------------------------------------------- fake Claude

def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def response(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Copie superficielle : l'historique continue d'être modifié après l'appel.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


# ---------------------------------------------------------------- memory

def test_memory_remember_search_forget(memory):
    a = memory.remember("Yannis adore le jazz et Miles Davis", "preference")
    memory.remember("La sœur de Yannis s'appelle Léa", "proches")
    assert [f.id for f in memory.search("musique jazz")] == [a.id]
    assert "Léa" in memory.search("sœur")[0].content
    assert memory.forget(a.id)
    assert not memory.forget(a.id)
    assert len(memory.all_facts()) == 1


def test_memory_rejects_empty(memory):
    with pytest.raises(ValueError):
        memory.remember("   ")


# ---------------------------------------------------------------- registry

def test_registry_definitions_are_sorted_and_valid():
    defs = registry.definitions()
    names = [d["name"] for d in defs]
    assert names == sorted(names)
    for d in defs:
        assert d["input_schema"]["type"] == "object"
        assert set(d["input_schema"]["required"]) <= set(d["input_schema"]["properties"])
    for expected in ["remember", "recall", "open_application", "gmail_list", "calendar_create", "get_weather"]:
        assert expected in names


def test_sensitive_tools_require_confirmation():
    for name in ["run_command", "gmail_send", "calendar_create", "calendar_delete", "write_text_file"]:
        assert registry.get(name).confirm is not None, name


# ---------------------------------------------------------------- brain

def test_brain_simple_answer(config, memory):
    client = FakeClient(response("end_turn", text("Bonjour Yannis.")))
    brain = Brain(config, memory, registry, client=client)
    assert brain.ask("Salut") == "Bonjour Yannis."
    call = client.calls[0]
    assert call["model"] == config.model
    assert call["fallbacks"] == "default"
    assert call["messages"][0]["content"].endswith("] Salut")
    assert any(t.get("type") == "web_search_20260209" for t in call["tools"])
    assert [role for role, *_ in memory.recent_log()] == ["user", "assistant"]


def test_brain_runs_tools_and_speaks_interim_text(config, memory):
    client = FakeClient(
        response("tool_use", text("Je le note."), tool_use("t1", "remember", {"fact": "Aime le café noir"})),
        response("end_turn", text("C'est retenu.")),
    )
    spoken = []
    brain = Brain(config, memory, registry, client=client)
    assert brain.ask("Retiens que j'aime le café noir", on_text=spoken.append) == "C'est retenu."
    assert spoken == ["Je le note."]
    assert memory.search("café")[0].content == "Aime le café noir"
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and "Mémorisé" in tool_result["content"]


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
    assert brain.ask("vas-y") == "D'accord, j'annule."
    assert asked == ["faire un truc risqué"] and not ran
    assert "refusé" in client.calls[1]["messages"][-1]["content"][0]["content"]


def test_brain_tool_errors_are_reported_to_claude(config, memory):
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


def test_brain_pause_turn_continues(config, memory):
    client = FakeClient(response("pause_turn", text("")), response("end_turn", text("Voilà les infos.")))
    brain = Brain(config, memory, registry, client=client)
    assert brain.ask("actus ?") == "Voilà les infos."
    assert len(client.calls) == 2


def test_system_prompt_contains_memory_and_is_stable(config, memory):
    memory.remember("Travaille comme développeur", "travail")
    client = FakeClient(response("end_turn", text("a")), response("end_turn", text("b")))
    brain = Brain(config, memory, registry, client=client)
    brain.ask("1")
    brain.ask("2")
    assert "développeur" in client.calls[0]["system"][1]["text"]
    assert client.calls[0]["system"] == client.calls[1]["system"]
    assert client.calls[0]["tools"] == client.calls[1]["tools"]


# ---------------------------------------------------------------- helpers

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
