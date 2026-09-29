"""Keyless public data: weather (Open-Meteo), location (IP), facts (Wikipedia). Set JARVIS_OFFLINE=1 to disable.
None of these need an account or API key; each call has a short timeout and fails quietly when offline."""
import logging
import os

import httpx

log = logging.getLogger("web")
OFFLINE = os.environ.get("JARVIS_OFFLINE", "").lower() in ("1", "true", "yes")
_client = httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3), follow_redirects=True,
                            headers={"User-Agent": "JarvisDesktopAssistant/2.0 (personal desktop assistant; https://example.org/jarvis)"})  # Wikimedia requires a URL/contact
_location = None

WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "heavy showers", 82: "violent showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms"}


async def location(city: str | None = None):
    """(lat, lon, name) for a city name, or for this PC's public IP when city is None."""
    global _location
    if OFFLINE:
        return None
    try:
        if city:
            r = await _client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1})
            res = (r.json().get("results") or [None])[0]
            return (res["latitude"], res["longitude"], res["name"]) if res else None
        if _location is None:
            r = await _client.get("http://ip-api.com/json/?fields=status,city,lat,lon")
            d = r.json()
            if d.get("status") == "success":
                _location = (d["lat"], d["lon"], d["city"])
        return _location
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("location lookup failed: %s", e)
        return None


async def weather(city: str | None = None):
    loc = await location(city)
    if not loc:
        return None
    lat, lon, name = loc
    try:
        r = await _client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code", "forecast_days": 2})
        d = r.json()
        cur, day = d["current"], d["daily"]
        return {
            "place": name,
            "now": f"{round(cur['temperature_2m'])}°C, feels like {round(cur['apparent_temperature'])}°C, {WMO.get(cur['weather_code'], 'mixed')}",
            "humidity": f"{cur['relative_humidity_2m']}%",
            "wind": f"{round(cur['wind_speed_10m'])} km/h",
            "today": f"high {round(day['temperature_2m_max'][0])}°C, low {round(day['temperature_2m_min'][0])}°C, "
                     f"{day['precipitation_probability_max'][0]}% chance of rain",
            "tomorrow": f"{WMO.get(day['weather_code'][1], 'mixed')}, high {round(day['temperature_2m_max'][1])}°C, "
                        f"{day['precipitation_probability_max'][1]}% chance of rain",
        }
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("weather failed: %s", e)
        return None


async def wikipedia(topic: str):
    """First paragraph of the best-matching Wikipedia article."""
    if OFFLINE:
        return None
    try:
        r = await _client.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": topic, "srlimit": 1, "format": "json"})
        hits = r.json()["query"]["search"]
        if not hits:
            return None
        title = hits[0]["title"]
        r = await _client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}")
        d = r.json()
        return {"title": d.get("title", title), "summary": d.get("extract", "")[:1200]}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("wikipedia failed: %s", e)
        return None
