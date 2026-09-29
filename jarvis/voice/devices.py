"""Choix du micro et des haut-parleurs (JARVIS_MIC / JARVIS_SPEAKERS dans .env).

Sans réglage, on garde les périphériques par défaut de Windows. Sinon on accepte
un numéro (voir « python -m jarvis --micros ») ou un bout du nom : « Blue Yeti », « casque »…
"""

from __future__ import annotations

from typing import Any


def _devices() -> list[dict[str, Any]]:
    import sounddevice as sd

    return [dict(d, index=i) for i, d in enumerate(sd.query_devices())]


def resolve(spec: str, kind: str) -> int | None:
    """Trouve le périphérique demandé. kind = « input » ou « output ». None = défaut du système."""
    spec = (spec or "").strip()
    if not spec:
        return None
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    candidates = [d for d in _devices() if d[channels] > 0]
    if spec.isdigit():
        index = int(spec)
        if any(d["index"] == index for d in candidates):
            return index
        raise ValueError(f"aucun périphérique {'d’entrée' if kind == 'input' else 'de sortie'} n° {index}")
    wanted = spec.lower()
    matches = [d for d in candidates if wanted in d["name"].lower()]
    if not matches:
        raise ValueError(f"aucun périphérique ne contient « {spec} » dans son nom "
                         "(liste : python -m jarvis --micros)")
    return matches[0]["index"]


def apply(mic: str, speakers: str) -> list[str]:
    """Règle les périphériques par défaut de sounddevice. Renvoie les avertissements éventuels."""
    import sounddevice as sd

    warnings = []
    current = list(sd.default.device)
    for position, (spec, kind) in enumerate(((mic, "input"), (speakers, "output"))):
        try:
            index = resolve(spec, kind)
        except Exception as exc:
            warnings.append(f"{'Micro' if kind == 'input' else 'Haut-parleurs'} : {exc}. J'utilise celui par défaut.")
            continue
        if index is not None:
            current[position] = index
    sd.default.device = current
    return warnings


def describe() -> str:
    """Liste lisible des micros et sorties audio, avec leur numéro."""
    import sounddevice as sd

    default_in, default_out = sd.default.device
    lines = ["Micros :"]
    for d in _devices():
        if d["max_input_channels"] > 0:
            mark = "  ← par défaut" if d["index"] == default_in else ""
            lines.append(f"  {d['index']:>3}  {d['name']}{mark}")
    lines.append("\nHaut-parleurs / casques :")
    for d in _devices():
        if d["max_output_channels"] > 0:
            mark = "  ← par défaut" if d["index"] == default_out else ""
            lines.append(f"  {d['index']:>3}  {d['name']}{mark}")
    lines.append("\nPour en choisir un, ajoute dans .env : JARVIS_MIC=<numéro ou bout du nom> "
                 "et/ou JARVIS_SPEAKERS=<numéro ou bout du nom>.")
    return "\n".join(lines)
