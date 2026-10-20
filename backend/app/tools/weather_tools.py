"""
Weather via Open-Meteo (no API key, no account; free for non-commercial
use, data under CC BY 4.0). Geocoding and forecast are two GETs to fixed
public hosts, so no SSRF concern - the LLM only chooses the place name.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

import httpx
from pydantic import Field

from app.config import get_settings
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> short German text.
WMO_DE = {
    0: "klar", 1: "überwiegend klar", 2: "teilweise bewölkt", 3: "bedeckt",
    45: "Nebel", 48: "Nebel mit Reif",
    51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen",
    56: "gefrierender Nieselregen", 57: "starker gefrierender Nieselregen",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    66: "gefrierender Regen", 67: "starker gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel",
    80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    85: "leichte Schneeschauer", 86: "starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "schweres Gewitter mit Hagel",
}

# Tests inject httpx.MockTransport.
_transport: Optional[httpx.AsyncBaseTransport] = None


class WeatherArgs(ArgsModel):
    location: Optional[str] = Field(None, description="Ort, z. B. 'Zürich'. Leer = Standardort aus der Konfiguration.")
    day: Literal["today", "tomorrow"] = Field("today", description="today oder tomorrow")


@register_tool
class GetWeatherTool(Tool):
    name = "get_weather"
    description = "Aktuelles Wetter und Vorhersage für heute oder morgen an einem Ort."
    Args = WeatherArgs
    risk = RiskTier.READ_ONLY
    exposed_by_default = True
    timeout_seconds = 15.0
    spoken_progress = "Ich schaue nach."

    async def execute(self, db=None, location: Optional[str] = None, day: str = "today", **_) -> ToolResult:
        place = (location or get_settings().WEATHER_DEFAULT_LOCATION or "").strip()
        if not place:
            return ToolResult(success=False, error="Kein Ort angegeben und kein WEATHER_DEFAULT_LOCATION konfiguriert. Frag den Nutzer nach dem Ort.")
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=_transport) as client:
                geo = await client.get(GEOCODE_URL, params={"name": place, "count": 1, "language": "de", "format": "json"})
                geo.raise_for_status()
                hits = geo.json().get("results") or []
                if not hits:
                    return ToolResult(success=False, error=f"Ort '{place}' nicht gefunden.")
                hit = hits[0]
                forecast = await client.get(
                    FORECAST_URL,
                    params={
                        "latitude": hit["latitude"],
                        "longitude": hit["longitude"],
                        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "timezone": "auto",
                        "forecast_days": 2,
                    },
                )
                forecast.raise_for_status()
                data = forecast.json()
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Wetterdienst nicht erreichbar ({exc.__class__.__name__}).")

        index = 0 if day == "today" else 1
        daily = data.get("daily", {})
        result: Dict[str, Any] = {
            "location": hit.get("name"),
            "region": hit.get("admin1"),
            "country": hit.get("country_code"),
            "day": day,
            "forecast": {
                "condition": WMO_DE.get(int(daily["weather_code"][index]), "unbekannt"),
                "temp_max_c": daily["temperature_2m_max"][index],
                "temp_min_c": daily["temperature_2m_min"][index],
                "precipitation_probability_percent": daily.get("precipitation_probability_max", [None, None])[index],
            },
            "source": "Open-Meteo",
        }
        if day == "today" and data.get("current"):
            cur = data["current"]
            result["now"] = {
                "condition": WMO_DE.get(int(cur["weather_code"]), "unbekannt"),
                "temp_c": cur["temperature_2m"],
                "feels_like_c": cur.get("apparent_temperature"),
                "wind_kmh": cur.get("wind_speed_10m"),
            }
        return ToolResult(success=True, data=result)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Wetter {'morgen' if args.get('day') == 'tomorrow' else 'heute'} in {args.get('location') or 'Standardort'}"
