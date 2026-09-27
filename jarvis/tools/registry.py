"""Registre des outils que Claude peut appeler."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

# Un outil renvoie soit du texte, soit une liste de blocs de contenu (ex. une image).
ToolOutput = str | list[dict[str, Any]]


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


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
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Les réponses sont diffusées en continu : les arguments arrivent sans tampon
            # côté API, donc on les valide nous-mêmes (validate) avant d'exécuter l'outil.
            "eager_input_streaming": True,
        }

    def validate(self, args: Any) -> str | None:
        """Renvoie un message d'erreur si les arguments ne respectent pas le schéma, sinon None."""
        if not isinstance(args, dict):
            return "les arguments doivent être un objet JSON"
        props = self.input_schema.get("properties", {})
        missing = [k for k in self.input_schema.get("required", []) if k not in args]
        if missing:
            return f"arguments manquants : {', '.join(missing)}"
        unknown = [k for k in args if k not in props]
        if unknown:
            return f"arguments inconnus : {', '.join(unknown)}"
        for key, value in args.items():
            expected = props[key].get("type")
            if expected and not _TYPE_CHECKS[expected](value):
                return f"« {key} » doit être de type {expected}"
            allowed = props[key].get("enum")
            if allowed and value not in allowed:
                return f"« {key} » doit valoir l'un de : {', '.join(map(str, allowed))}"
        return None

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
