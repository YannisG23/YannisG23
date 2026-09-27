"""Registre des outils que Claude peut appeler."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

# Un outil renvoie soit du texte, soit une liste de blocs de contenu (ex. une image).
ToolOutput = str | list[dict[str, Any]]


@dataclass
class ToolContext:
    """Ce que les outils peuvent utiliser pour interagir avec l'application."""

    config: Any
    memory: Any
    # Prévient l'utilisateur de façon asynchrone (ex. fin d'un minuteur).
    notify: Callable[[str], None] = print


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., ToolOutput]
    # Action sensible : Jarvis doit demander confirmation avant de l'exécuter.
    confirm: Callable[[dict[str, Any]], str] | None = None
    takes_context: bool = False

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        if self.takes_context:
            return self.func(ctx, **args)
        return self.func(**args)


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(
        self,
        description: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
        confirm: Callable[[dict[str, Any]], str] | None = None,
        name: str | None = None,
    ) -> Callable[[Callable[..., ToolOutput]], Callable[..., ToolOutput]]:
        def decorator(func: Callable[..., ToolOutput]) -> Callable[..., ToolOutput]:
            params = list(inspect.signature(func).parameters)
            tool_name = name or func.__name__
            if tool_name in self.tools:
                raise ValueError(f"Outil déjà enregistré : {tool_name}")
            self.tools[tool_name] = Tool(
                name=tool_name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties or {},
                    "required": required or [],
                },
                func=func,
                confirm=confirm,
                takes_context=bool(params) and params[0] == "ctx",
            )
            return func

        return decorator

    def definitions(self) -> list[dict[str, Any]]:
        # Ordre stable : indispensable pour que le cache de prompt fonctionne.
        return [self.tools[name].definition() for name in sorted(self.tools)]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)


registry = Registry()
