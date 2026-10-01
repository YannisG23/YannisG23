from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from jarvis.config import Config
from jarvis.mcp_server import CONFIRM_ARG, build_server
from jarvis.memory import Memory
from jarvis.tools import registry


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    config = Config()
    memory = Memory(config.memory_db)
    yield config, memory
    memory.close()


async def _session(env):
    from mcp.shared.memory import create_connected_server_and_client_session

    return create_connected_server_and_client_session(build_server(*env))


def test_liste_les_outils_du_registre(env):
    async def go():
        async with await _session(env) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == set(registry.tools)
    assert {"remember", "recall", "usage_report"} <= set(tools)
    # Un outil sensible reçoit l'argument `confirme`, pas les autres.
    sensitive = next(n for n, t in registry.tools.items() if t.confirm)
    assert CONFIRM_ARG in tools[sensitive].inputSchema["properties"]
    plain = next(n for n, t in registry.tools.items() if t.confirm is None)
    assert CONFIRM_ARG not in tools[plain].inputSchema["properties"]


def test_memoire_et_confirmation(env, tmp_path):
    target = tmp_path / "note.txt"

    async def go():
        async with await _session(env) as client:
            await client.call_tool("remember", {"fact": "Yannis aime le café noir"})
            rec = await client.call_tool("recall", {"query": "café"})
            ask = await client.call_tool("write_text_file", {"path": str(target), "content": "salut"})
            existed_before = target.exists()
            ok = await client.call_tool("write_text_file", {"path": str(target), "content": "salut", CONFIRM_ARG: True})
            bad = await client.call_tool("inconnu", {})
            return rec, ask, existed_before, ok, bad

    rec, ask, existed_before, ok, bad = asyncio.run(go())
    assert "café" in rec.content[0].text
    assert "CONFIRMATION REQUISE" in ask.content[0].text and not existed_before
    assert target.read_text() == "salut" and not ok.isError
    assert bad.isError
