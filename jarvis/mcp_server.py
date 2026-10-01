"""Le Jarvis Python exposé en serveur MCP (transport stdio) : `python -m jarvis.mcp_server`.

Le pont de Jarvis 2 (Node) le charge comme n'importe quel serveur MCP et donne ainsi à Claude la
mémoire long terme, les tâches, les routines, le contrôle de Windows, les fichiers, la météo, Codex
et le suivi des crédits.

Confirmations : un outil sensible (`confirm`) ne s'exécute que si l'argument supplémentaire
`confirme: true` est passé. Sans lui, il renvoie la question à poser à l'utilisateur.
"""

from __future__ import annotations

import asyncio
import copy
import sys
from typing import Any

from .config import Config
from .memory import Memory
from .tools import ToolContext, registry
from .tools.registry import Registry, Tool

CONFIRM_ARG = "confirme"
_CONFIRM_SCHEMA = {
    "type": "boolean",
    "description": (
        "À passer à true UNIQUEMENT après que l'utilisateur a dit oui à la question de confirmation "
        "renvoyée par un premier appel sans cet argument."
    ),
}


def tool_schema(tool: Tool) -> dict[str, Any]:
    """Schéma d'entrée MCP : celui du registre, plus `confirme` pour les outils sensibles."""
    schema = copy.deepcopy(tool.input_schema)
    if tool.confirm is not None:
        schema.setdefault("properties", {})[CONFIRM_ARG] = _CONFIRM_SCHEMA
    return schema


def tool_description(tool: Tool) -> str:
    if tool.confirm is None:
        return tool.description
    return (
        f"{tool.description}\n\nACTION SENSIBLE : le premier appel renvoie une question de confirmation ; "
        f"pose-la à l'utilisateur, puis rappelle l'outil avec {CONFIRM_ARG}=true seulement s'il accepte."
    )


def execute(reg: Registry, ctx: ToolContext, name: str, arguments: dict[str, Any] | None) -> tuple[list[dict[str, Any]], bool]:
    """Exécute un outil. Renvoie (blocs de contenu, est_une_erreur). Blocs : {"type": "text"|"image", ...}."""
    args = dict(arguments or {})
    confirmed = args.pop(CONFIRM_ARG, False) is True
    tool = reg.get(name)
    if tool is None:
        return [{"type": "text", "text": f"Outil inconnu : {name}"}], True
    problem = tool.validate(args)
    if problem:
        return [{"type": "text", "text": f"Appel invalide de {name} : {problem}. Corrige les arguments."}], True
    if tool.confirm is not None and not confirmed:
        question = tool.confirm(args)
        return [{"type": "text", "text": (
            f"CONFIRMATION REQUISE : {question}\nPose cette question à l'utilisateur. S'il accepte, "
            f"rappelle {name} avec les mêmes arguments et {CONFIRM_ARG}=true. Sinon, n'exécute rien."
        )}], False
    try:
        out = tool.run(ctx, args)
    except Exception as exc:  # renvoyée à Claude, qui peut s'adapter
        return [{"type": "text", "text": f"Erreur pendant {name} : {type(exc).__name__}: {exc}"}], True
    if isinstance(out, str):
        return [{"type": "text", "text": out}], False
    return list(out), False


def _to_content(blocks: list[dict[str, Any]]):
    from mcp import types

    result: list[Any] = []
    for b in blocks:
        if b.get("type") == "image" and isinstance(b.get("source"), dict):
            src = b["source"]
            result.append(types.ImageContent(type="image", data=src.get("data", ""),
                                             mimeType=src.get("media_type", "image/png")))
        elif b.get("type") == "image" and "data" in b:
            result.append(types.ImageContent(type="image", data=b["data"], mimeType=b.get("mimeType", "image/png")))
        else:
            result.append(types.TextContent(type="text", text=str(b.get("text", b))))
    return result


def build_server(config: Config | None = None, memory: Memory | None = None, reg: Registry = registry):
    """Construit le serveur MCP (bas niveau, pour garder nos schémas JSON tels quels)."""
    from mcp import types
    from mcp.server.lowlevel import Server

    config = config or Config()
    memory = memory or Memory(config.memory_db)
    from . import usage as usage_mod

    if usage_mod.current is None:
        usage_mod.current = usage_mod.UsageTracker(config.home / "usage.json", config.openai_budget,
                                                   config.gpt_price_in, config.gpt_price_out)
    # stdout est réservé au protocole : les rappels (minuteurs…) partent sur stderr.
    ctx = ToolContext(config=config, memory=memory, notify=lambda msg: print(msg, file=sys.stderr, flush=True))
    server: Server = Server("jarvis_py")

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [
            types.Tool(name=name, description=tool_description(reg.tools[name]),
                       inputSchema=tool_schema(reg.tools[name]))
            for name in sorted(reg.tools)
        ]

    @server.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict[str, Any] | None):
        blocks, is_error = await asyncio.to_thread(execute, reg, ctx, name, arguments)
        content = _to_content(blocks)
        if is_error:
            return types.CallToolResult(content=content, isError=True)
        return content

    return server


async def serve() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError:
        sys.exit("Le paquet « mcp » manque : pip install -e \".[mcp]\"")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
