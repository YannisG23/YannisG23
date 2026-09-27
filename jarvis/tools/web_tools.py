"""Infos en ligne sans clé d'API : météo (Open-Meteo)."""

from __future__ import annotations

import requests

from .registry import ToolContext, registry

_WEATHER_CODES = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "bruine forte",
    61: "pluie légère", 63: "pluie", 65: "forte pluie", 66: "pluie verglaçante", 67: "forte pluie verglaçante",
    71: "neige légère", 73: "neige", 75: "forte neige", 77: "grains de neige",
    80: "averses légères", 81: "averses", 82: "violentes averses", 85: "averses de neige", 86: "fortes averses de neige",
    95: "orage", 96: "orage avec grêle", 99: "violent orage avec grêle",
}


def describe_weather_code(code: int) -> str:
    return _WEATHER_CODES.get(int(code), f"code météo {code}")


@registry.tool(
    "Donne la météo actuelle et les prévisions sur quelques jours pour une ville.",
    properties={
        "city": {"type": "string", "description": "Ville. Défaut : la ville de l'utilisateur."},
        "days": {"type": "integer", "description": "Jours de prévision (1 à 7). Défaut 3."},
    },
)
def get_weather(ctx: ToolContext, city: str = "", days: int = 3) -> str:
    city = city or ctx.config.city
    if not city:
        return "Je ne connais pas ta ville : précise-la, ou règle JARVIS_CITY."
    found = fetch_weather(city, ctx.config.language, days)
    if found is None:
        return f"Ville introuvable : {city}"
    return format_weather(*found)


def fetch_weather(city: str, language: str = "fr", days: int = 3) -> tuple[str, dict] | None:
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": language},
        timeout=10,
    ).json()
    if not geo.get("results"):
        return None
    place = geo["results"][0]
    forecast = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": max(1, min(int(days), 7)),
            "timezone": "auto",
        },
        timeout=10,
    ).json()
    return f"{place['name']}, {place.get('country', '')}", forecast


def format_weather(place: str, data: dict) -> str:
    cur = data["current"]
    lines = [
        f"Météo à {place} : {describe_weather_code(cur['weather_code'])}, "
        f"{cur['temperature_2m']:.0f}°C (ressenti {cur['apparent_temperature']:.0f}°C), "
        f"humidité {cur['relative_humidity_2m']}%, vent {cur['wind_speed_10m']:.0f} km/h."
    ]
    daily = data["daily"]
    for i, day in enumerate(daily["time"]):
        lines.append(
            f"{day} : {describe_weather_code(daily['weather_code'][i])}, "
            f"{daily['temperature_2m_min'][i]:.0f} à {daily['temperature_2m_max'][i]:.0f}°C, "
            f"pluie {daily['precipitation_probability_max'][i]}%"
        )
    return "\n".join(lines)
