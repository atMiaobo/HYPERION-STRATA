"""Open-Meteo forecast API adapter and deterministic scene model.

https://open-meteo.com/en/docs — coordinates are explicitly configured, never inferred.
"""
from dataclasses import dataclass
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from zoneinfo import ZoneInfo

FIELDS = ("weather_code", "precipitation", "rain", "snowfall", "cloud_cover",
          "wind_speed_10m", "wind_direction_10m", "is_day")
CODES = {0,1,2,3,45,48,51,53,55,56,57,61,63,65,66,67,71,73,75,77,80,81,82,85,86,95,96,99}


def finite(value, lo, hi):
    value = float(value)
    if not math.isfinite(value) or not lo <= value <= hi: raise ValueError("weather value outside expected bounds")
    return value


@dataclass(frozen=True)
class Weather:
    time: float
    code: int
    precipitation: float
    rain: float
    snow: float
    cloud: float
    wind: float
    direction: float
    is_day: bool
    sunrise: float
    sunset: float
    timezone: str


def parse(data):
    c, units, daily = data["current"], data["current_units"], data["daily"]
    if units.get("wind_speed_10m") != "m/s" or units.get("rain") != "mm" or units.get("snowfall") != "cm":
        raise ValueError("unexpected Open-Meteo units")
    code = finite(c["weather_code"], 0, 99)
    if code not in CODES: raise ValueError("unknown WMO code")
    day = finite(c["is_day"], 0, 1)
    if day not in (0, 1): raise ValueError("is_day must be boolean")
    zone = data["timezone"]
    ZoneInfo(zone)
    now = finite(c["time"], 0, 10_000_000_000)
    rises, sets = daily["sunrise"], daily["sunset"]
    if not rises or len(rises) != len(sets): raise ValueError("missing sunrise/sunset")
    pairs = [(finite(r, 0, 10_000_000_000), finite(s, 0, 10_000_000_000)) for r, s in zip(rises, sets)]
    rise, setting = min(pairs, key=lambda pair: abs((pair[0]+pair[1])/2-now))
    if not rise < setting or setting-rise > 86400: raise ValueError("invalid solar interval")
    return Weather(now, int(code), finite(c["precipitation"], 0, 500), finite(c["rain"], 0, 500),
                   finite(c["snowfall"], 0, 200), finite(c["cloud_cover"], 0, 100),
                   finite(c["wind_speed_10m"], 0, 150), finite(c["wind_direction_10m"], 0, 360),
                   bool(day), rise, setting, zone)


def fetch(latitude, longitude, timezone="auto", opener=urlopen):
    lat, lon = finite(latitude, -90, 90), finite(longitude, -180, 180)
    if timezone != "auto": ZoneInfo(timezone)
    query = urlencode(dict(latitude=lat, longitude=lon, current=",".join(FIELDS), daily="sunrise,sunset",
                           timezone=timezone, timeformat="unixtime", wind_speed_unit="ms", forecast_days=2))
    request = Request("https://api.open-meteo.com/v1/forecast?"+query,
                      headers={"User-Agent": "Hyperion-Cadere/0.3"})
    with opener(request, timeout=8) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000: raise ValueError("weather response too large")
    data = json.loads(body)
    parse(data)
    return data


def scene(weather, now=None):
    now = time.time() if now is None else now
    w = weather
    daytime = w.sunrise <= now <= w.sunset
    golden = daytime and min(abs(now-w.sunrise), abs(now-w.sunset)) < 3600
    if w.code >= 95: condition = "stormy"
    elif w.snow > 0 or w.code in {71,73,75,77,85,86}: condition = "snowy"
    elif w.rain > .05 or w.precipitation > .1 or w.code in {51,53,55,56,57,61,63,65,66,67,80,81,82}: condition = "rainy"
    elif not daytime: condition = "nighttime"
    elif w.cloud >= 65: condition = "overcast"
    elif golden: condition = "golden-hour"
    elif now < w.sunrise+3*3600: condition = "clear-morning"
    else: condition = "sunny-daytime"
    sun = max(0, math.sin(math.pi*max(0,min(1,(now-w.sunrise)/max(w.sunset-w.sunrise,1)))))
    light = sun*(1-w.cloud/100)**2 if daytime else 0
    wind = max(-1,min(1,-math.sin(math.radians(w.direction))*w.wind/15))
    wet = condition in ("rainy", "stormy")
    snowy = condition == "snowy"
    intensity = min(2, max(.25, math.sqrt(w.rain+.05)*.7)) if wet else 0
    progress = max(0,min(1,(now-w.sunrise)/max(w.sunset-w.sunrise,1)))
    return condition, {
        "auto-rain.opacity": 1.0 if wet else 0.0,
        "auto-rain.uRain": intensity, "auto-rain.uWind": wind*.5,
        "auto-rain.uLightning": .45 if condition == "stormy" else 0.0,
        "auto-snow.opacity": 1.0 if snowy else 0.0,
        "auto-snow.uSnow": min(2,max(.3,w.snow*.8)) if snowy else 0,
        "auto-snow.uWind": wind*.5,
        "auto-sunbeams.opacity": light*.7,
        "auto-sunbeams.uOriginX": progress,
        "auto-flare.opacity": light*.35,
        "auto-flare.uOriginX": progress,
        "screen.temperature": .45 if golden else (-.25 if not daytime else .05),
        "screen.saturation": .9 if not daytime else 1,
        "screen.exposure": -.08 if w.cloud > 80 else 0,
    }


class Transition:
    def __init__(self, values=None):
        self.start = dict(values or {})
        self.target = dict(self.start)
        self.at = 0
        self.duration = 0

    def value(self, now):
        p = 1 if not self.duration else max(0, min(1, (now-self.at)/self.duration))
        p = p*p*(3-2*p)
        return {k: self.start.get(k, v)+(v-self.start.get(k, v))*p for k,v in self.target.items()}

    def retarget(self, values, now, duration):
        self.start = self.value(now)
        # Newly introduced effect opacity fades up from zero.
        for k,v in values.items(): self.start.setdefault(k, 0 if k.endswith(".opacity") else v)
        self.target = dict(values)
        self.at, self.duration = now, max(0, duration)

    def active(self, now):
        return self.duration > 0 and now < self.at+self.duration


def priorities(manual, automatic, overrides, mode):
    result = dict(manual)
    if mode in ("automatic", "hybrid"): result.update(automatic)
    if mode == "hybrid": result.update(overrides)
    return result
