"""Configuration loading.

The configuration lives in one JSON file that the web backend edits and the
data/display services read. Only the settings needed so far are modelled;
unknown keys are preserved in ``extra`` so newer files do not break old code.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .errors import ConfigError

DEFAULT_MATRIX: Dict[str, Any] = {
    "rows": 64,
    "cols": 64,
    "chain_length": 1,
    "parallel": 1,
    "hardware_mapping": "adafruit-hat",
    "gpio_slowdown": 1,
    "brightness": 60,
    "pwm_bits": 11,
}


DEFAULT_SENSOR: Dict[str, Any] = {
    "enabled": False,
    "type": "dht22",
    "gpio": 25,
    "interval_seconds": 30,
    "temp_offset": 0.0,
    "humidity_offset": 0.0,
}

DEFAULT_CAMERA: Dict[str, Any] = {
    "enabled": True,
    "width": 1280,             # photo
    "height": 960,
    "live_width": 640,         # live view (MJPEG)
    "live_height": 480,
    "live_fps": 10,
    "gallery_limit_mb": 200,   # oldest photos are deleted when the gallery gets bigger
    "hflip": True,             # picture settings for photo and live view (both on = turned by 180 degrees)
    "vflip": True,
    "brightness": 0,           # -50 .. 50 (percent, 0 = unchanged)
    "sharpness": 1.0,          # 0 .. 4 (1 = camera default)
    "motion_enabled": False,   # motion detection in the live view, saves a photo on movement
    "motion_sensitivity": 3,   # 1 (low) .. 5 (high)
    "motion_pause": 60,        # seconds between two motion photos
}

# Satellite sensors (0.6.0): up to three ESP32 boards (DHT22 + MQ-135) that report over HTTP or MQTT (see satellites.py).
SATELLITE_MAX = 3
DEFAULT_SATELLITES: Dict[str, Any] = {
    "enabled": False,          # show one page per satellite in the idle rotation (after the room view)
    "seconds": 8,              # display time of each satellite page
    "mqtt": {
        "enabled": False,
        "host": "",            # broker (Mosquitto on the Home Assistant Raspberry Pi)
        "port": 1883,
        "username": "",
        "password": "",
        "base_topic": "sportstracker",     # default state topic of a satellite: <base_topic>/<key>/state
    },
    "items": [],
}
DEFAULT_SATELLITE_ITEM: Dict[str, Any] = {
    "enabled": True,
    "key": "",                 # short id: letters, digits, - and _ (topic and HTTP address)
    "name": "",                # shown on the matrix (up to 10 characters)
    "token": "",               # HTTP push: the satellite sends it as "Authorization: Bearer <token>"
    "topic": "",               # MQTT state topic, empty = <base_topic>/<key>/state
    "gas_clean": 800,          # raw MQ-135 value in clean air (index 100) ...
    "gas_bad": 2500,           # ... and in bad air (index 0)
}

DEFAULT_MESSAGE: Dict[str, Any] = {
    "enabled": False,
    "text": "Hallo!",
    "color": "#FFB000",
    "font": "6x13B",
    "intensity": 100,          # percent of the chosen colour
    "animation": "none",
    "speed": "normal",
    "permanent": False,        # True: only the message is shown, nothing else
    "during_games": False,     # False: the message runs only in the idle rotation, not while games are shown
    "seconds": 10,             # time in the rotation (a scrolling text always gets at least one full pass)
}

# Picture mode (0.5.5, gallery since 0.6.0): up to 10 uploaded photos / animated GIFs in data/pictures/ (see picture.py).
PICTURE_FITS = ("fill", "fit", "stretch")      # crop to fill / whole picture with black bars / distort to fill
PICTURE_TRANSITIONS = ("none", "fade", "slide", "wipe", "random")
DEFAULT_PICTURE: Dict[str, Any] = {
    "enabled": False,
    "fit": "fill",
    "intensity": 100,          # percent brightness of the pictures
    "permanent": False,        # True: only the pictures are shown (a permanent message still wins)
    "seconds": 10,             # display time of each picture (a picture can have its own time)
    "transition": "fade",      # effect between two pictures
    "transition_ms": 800,      # duration of the effect
}

# Safety shutdown (0.5.4): the Pi powers off when the CPU stays at or above this temperature. The Pi itself throttles
# at 80 C and limits hard at 85 C, so the limit must stay below 85.
SAFETY_MIN_TEMP = 60
SAFETY_MAX_TEMP = 85
DEFAULT_SAFETY: Dict[str, Any] = {
    "enabled": True,
    "shutdown_temp_c": 84,
}

# Must match banner.FONT_KEYS / banner.ANIMATIONS (a test checks it); kept here so the services that only read the
# configuration do not have to import the picture code.
MESSAGE_FONTS = ("tom-thumb", "4x6", "5x7", "5x8", "6x10", "6x13", "6x13B", "7x13", "7x13B", "8x13B",
                 "9x15", "9x15B", "9x18B", "10x20")
MESSAGE_ANIMATIONS = ("none", "scroll", "blink", "pulse", "rainbow", "wave", "typewriter")
MESSAGE_SPEEDS = ("slow", "normal", "fast")
MESSAGE_MAX_TEXT = 200

# GPIO numbers the sensor may use with the Adafruit RGB Matrix Bonnet (free pins; GPIO3 is the power button).
SENSOR_GPIOS = [25, 19, 14, 15, 2, 7, 8, 9, 10, 11]

IDLE_MODES = ("alternate", "table", "room")


@dataclass
class Config:
    timezone: str = "Europe/Berlin"
    provider: str = "openligadb"
    provider_params: Dict[str, Any] = field(default_factory=dict)
    favorite_team_id: str = ""
    show_team_names: bool = True
    show_game_clock: bool = True
    cache_dir: str = "cache"
    fonts_dir: str = "fonts"
    logo_override_dir: str = "logos/override"
    team_labels: Dict[str, str] = field(default_factory=dict)
    logo_backing: str = "auto"
    logo_backing_teams: Dict[str, str] = field(default_factory=dict)
    table_logo_size: int = 6
    table_label: str = "city"
    poll_game_seconds: int = 60
    poll_idle_seconds: int = 900
    data_dir: str = "data"
    team_cities: Dict[str, str] = field(default_factory=dict)
    matrix: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_MATRIX))
    # Display logic (0.5.0)
    gameday_results: bool = True           # finished games of today stay in the rotation until midnight, with the table
    show_other_games: bool = True          # other league games: main game while live, cards at half-time / after the end
    rotation_main_seconds: int = 10
    rotation_card_seconds: int = 10
    result_window_minutes: int = 30
    halftime_max_minutes: int = 15
    idle_hide_table: bool = False          # nothing live: never show the table (room view instead)
    idle_mode: str = "alternate"           # nothing live: table and room view in turns / only one of them
    idle_table_seconds: int = 10
    idle_room_seconds: int = 10
    idle_kickoff_seconds: int = 10
    sensor: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SENSOR))
    camera: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CAMERA))
    message: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_MESSAGE))
    safety: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SAFETY))
    picture: Dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_PICTURE))
    satellites: Dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_SATELLITES))
    extra: Dict[str, Any] = field(default_factory=dict)


_KNOWN = {
    "timezone",
    "provider",
    "provider_params",
    "favorite_team_id",
    "show_team_names",
    "show_game_clock",
    "cache_dir",
    "fonts_dir",
    "logo_override_dir",
    "team_labels",
    "logo_backing",
    "logo_backing_teams",
    "table_logo_size",
    "table_label",
    "poll_game_seconds",
    "poll_idle_seconds",
    "data_dir",
    "team_cities",
    "matrix",
    "show_other_games",
    "gameday_results",
    "rotation_main_seconds",
    "rotation_card_seconds",
    "result_window_minutes",
    "halftime_max_minutes",
    "idle_mode",
    "idle_hide_table",
    "idle_table_seconds",
    "idle_room_seconds",
    "idle_kickoff_seconds",
    "sensor",
    "camera",
    "message",
    "safety",
    "picture",
    "satellites",
}

# key -> (minimum, maximum)
_WHOLE_NUMBERS = {
    "rotation_main_seconds": (3, 600),
    "rotation_card_seconds": (3, 600),
    "result_window_minutes": (1, 240),
    "halftime_max_minutes": (1, 90),
    "idle_table_seconds": (3, 600),
    "idle_room_seconds": (3, 600),
    "idle_kickoff_seconds": (3, 600),
}


def parse_config(data: Dict[str, Any]) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("configuration root must be a JSON object")
    cfg = Config()
    if "timezone" in data:
        cfg.timezone = _as_str(data, "timezone")
    if "provider" in data:
        cfg.provider = _as_str(data, "provider")
    if "provider_params" in data:
        params = data["provider_params"]
        if not isinstance(params, dict):
            raise ConfigError("'provider_params' must be an object")
        cfg.provider_params = dict(params)
    if "favorite_team_id" in data:
        # Accept numbers as well, but always store as string.
        cfg.favorite_team_id = str(data["favorite_team_id"])
    for key in ("show_team_names", "show_game_clock"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ConfigError("'%s' must be true or false" % key)
            setattr(cfg, key, data[key])
    for key in ("poll_game_seconds", "poll_idle_seconds"):
        if key in data:
            value = data[key]
            low = 15 if key == "poll_game_seconds" else 60
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= 86400:
                raise ConfigError("'%s' must be a whole number of seconds from %d to 86400" % (key, low))
            setattr(cfg, key, value)
    for key in ("cache_dir", "fonts_dir", "logo_override_dir", "data_dir"):
        if key in data:
            setattr(cfg, key, _as_str(data, key))
    if "team_labels" in data:
        labels = data["team_labels"]
        if not isinstance(labels, dict):
            raise ConfigError("'team_labels' must be an object")
        cfg.team_labels = {str(k): str(v) for k, v in labels.items()}
    if "logo_backing" in data:
        if data["logo_backing"] not in ("auto", "off", "always"):
            raise ConfigError("'logo_backing' must be auto, off or always")
        cfg.logo_backing = data["logo_backing"]
    if "logo_backing_teams" in data:
        teams = data["logo_backing_teams"]
        if not isinstance(teams, dict) or any(v not in ("auto", "off", "always") for v in teams.values()):
            raise ConfigError("'logo_backing_teams' must map team ids to auto, off or always")
        cfg.logo_backing_teams = {str(k): v for k, v in teams.items()}
    if "table_logo_size" in data:
        size = data["table_logo_size"]
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= 16:
            raise ConfigError("'table_logo_size' must be a whole number from 0 (off) to 16")
        cfg.table_logo_size = size
    if "table_label" in data:
        if data["table_label"] not in ("city", "short"):
            raise ConfigError("'table_label' must be city or short")
        cfg.table_label = data["table_label"]
    if "team_cities" in data:
        cities = data["team_cities"]
        if not isinstance(cities, dict):
            raise ConfigError("'team_cities' must be an object")
        cfg.team_cities = {str(k): str(v) for k, v in cities.items()}
    if "matrix" in data:
        matrix = data["matrix"]
        if not isinstance(matrix, dict):
            raise ConfigError("'matrix' must be an object")
        merged = dict(DEFAULT_MATRIX)
        merged.update(matrix)
        cfg.matrix = merged
    if "gameday_results" in data:
        if not isinstance(data["gameday_results"], bool):
            raise ConfigError("'gameday_results' must be true or false")
        cfg.gameday_results = data["gameday_results"]
    if "show_other_games" in data:
        if not isinstance(data["show_other_games"], bool):
            raise ConfigError("'show_other_games' must be true or false")
        cfg.show_other_games = data["show_other_games"]
    for key, (low, high) in _WHOLE_NUMBERS.items():
        if key in data:
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ConfigError("'%s' must be a whole number from %d to %d" % (key, low, high))
            setattr(cfg, key, value)
    if "idle_hide_table" in data:
        if not isinstance(data["idle_hide_table"], bool):
            raise ConfigError("'idle_hide_table' must be true or false")
        cfg.idle_hide_table = data["idle_hide_table"]
    if "idle_mode" in data:
        if data["idle_mode"] not in IDLE_MODES:
            raise ConfigError("'idle_mode' must be one of %s" % ", ".join(IDLE_MODES))
        cfg.idle_mode = data["idle_mode"]
    if "sensor" in data:
        cfg.sensor = _parse_sensor(data["sensor"])
    if "camera" in data:
        cfg.camera = _parse_camera(data["camera"])
    if "message" in data:
        cfg.message = _parse_message(data["message"])
    if "safety" in data:
        cfg.safety = _parse_safety(data["safety"])
    if "picture" in data:
        cfg.picture = _parse_picture(data["picture"])
    if "satellites" in data:
        cfg.satellites = _parse_satellites(data["satellites"])
    cfg.extra = {k: v for k, v in data.items() if k not in _KNOWN}
    return cfg


_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")


def _plain_text(value: Any, label: str, limit: int, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or value != value.strip() or "\n" in value:
        raise ConfigError("'%s' must be text of up to %d characters without surrounding spaces" % (label, limit))
    if not allow_empty and not value:
        raise ConfigError("'%s' must not be empty" % label)
    return value


def _topic_text(value: Any, label: str, limit: int) -> str:
    text = _plain_text(value, label, limit)
    if any(char in text for char in "#+ ") or text.startswith("/"):
        raise ConfigError("'%s' must not contain #, + or spaces and must not start with /" % label)
    return text


def _parse_satellites(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'satellites' must be an object")
    sat = copy.deepcopy(DEFAULT_SATELLITES)
    sat.update({k: v for k, v in data.items() if k not in ("mqtt", "items")})
    for key in ("enabled",):
        if not isinstance(sat[key], bool):
            raise ConfigError("'satellites.%s' must be true or false" % key)
    seconds = sat["seconds"]
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 3 <= seconds <= 120:
        raise ConfigError("'satellites.seconds' must be a whole number from 3 to 120")
    mqtt = dict(DEFAULT_SATELLITES["mqtt"])
    if "mqtt" in data:
        if not isinstance(data["mqtt"], dict):
            raise ConfigError("'satellites.mqtt' must be an object")
        mqtt.update(data["mqtt"])
    if not isinstance(mqtt["enabled"], bool):
        raise ConfigError("'satellites.mqtt.enabled' must be true or false")
    mqtt["host"] = _plain_text(mqtt["host"], "satellites.mqtt.host", 100)
    if " " in mqtt["host"]:
        raise ConfigError("'satellites.mqtt.host' must not contain spaces")
    port = mqtt["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("'satellites.mqtt.port' must be a whole number from 1 to 65535")
    mqtt["username"] = _plain_text(mqtt["username"], "satellites.mqtt.username", 64)
    if not isinstance(mqtt["password"], str) or len(mqtt["password"]) > 128:
        raise ConfigError("'satellites.mqtt.password' must be text of up to 128 characters")
    mqtt["base_topic"] = _topic_text(mqtt["base_topic"], "satellites.mqtt.base_topic", 40)
    if not mqtt["base_topic"]:
        raise ConfigError("'satellites.mqtt.base_topic' must not be empty")
    sat["mqtt"] = mqtt
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list) or len(raw_items) > SATELLITE_MAX:
        raise ConfigError("'satellites.items' must be a list of at most %d satellites" % SATELLITE_MAX)
    items: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ConfigError("each entry of 'satellites.items' must be an object")
        item = dict(DEFAULT_SATELLITE_ITEM)
        item.update(raw)
        if not isinstance(item["enabled"], bool):
            raise ConfigError("'satellites.items.enabled' must be true or false")
        if not isinstance(item["key"], str) or not _KEY_RE.match(item["key"]):
            raise ConfigError("'satellites.items.key' must be 1 to 20 characters: a-z, 0-9, - and _")
        if item["key"] in seen:
            raise ConfigError("satellite key '%s' is used twice" % item["key"])
        seen.add(item["key"])
        item["name"] = _plain_text(item["name"], "satellites.items.name", 10, allow_empty=False)
        token = _plain_text(item["token"], "satellites.items.token", 64)
        if token and (len(token) < 8 or " " in token):
            raise ConfigError("'satellites.items.token' must be empty or 8 to 64 characters without spaces")
        item["token"] = token
        item["topic"] = _topic_text(item["topic"], "satellites.items.topic", 100)
        for name in ("gas_clean", "gas_bad"):
            value = item[name]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 65535:
                raise ConfigError("'satellites.items.%s' must be a whole number from 0 to 65535" % name)
        if item["gas_clean"] >= item["gas_bad"]:
            raise ConfigError("'satellites.items.gas_clean' must be lower than 'gas_bad'")
        items.append(item)
    sat["items"] = items
    return sat


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_sensor(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'sensor' must be an object")
    sensor = dict(DEFAULT_SENSOR)
    sensor.update(data)
    if not isinstance(sensor["enabled"], bool):
        raise ConfigError("'sensor.enabled' must be true or false")
    if sensor["type"] != "dht22":
        raise ConfigError("'sensor.type' must be dht22")
    gpio = sensor["gpio"]
    if not isinstance(gpio, int) or isinstance(gpio, bool) or not 2 <= gpio <= 27:
        raise ConfigError("'sensor.gpio' must be a GPIO number from 2 to 27")
    interval = sensor["interval_seconds"]
    if not isinstance(interval, int) or isinstance(interval, bool) or not 5 <= interval <= 3600:
        raise ConfigError("'sensor.interval_seconds' must be a whole number from 5 to 3600")
    for key, limit in (("temp_offset", 20.0), ("humidity_offset", 30.0)):
        if not _number(sensor[key]) or abs(sensor[key]) > limit:
            raise ConfigError("'sensor.%s' must be a number between -%g and %g" % (key, limit, limit))
        sensor[key] = float(sensor[key])
    return sensor


def _parse_camera(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'camera' must be an object")
    camera = dict(DEFAULT_CAMERA)
    camera.update(data)
    for key in ("enabled", "hflip", "vflip", "motion_enabled"):
        if not isinstance(camera[key], bool):
            raise ConfigError("'camera.%s' must be true or false" % key)
    if not _number(camera["sharpness"]) or not 0 <= camera["sharpness"] <= 4:
        raise ConfigError("'camera.sharpness' must be a number from 0 to 4")
    camera["sharpness"] = float(camera["sharpness"])
    for key, low, high in (("width", 160, 3280), ("height", 120, 2464), ("live_width", 160, 1280),
                           ("live_height", 120, 960), ("live_fps", 1, 30), ("gallery_limit_mb", 10, 4000),
                           ("brightness", -50, 50), ("motion_sensitivity", 1, 5), ("motion_pause", 5, 3600)):
        value = camera[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ConfigError("'camera.%s' must be a whole number from %d to %d" % (key, low, high))
    return camera


def _parse_message(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'message' must be an object")
    message = dict(DEFAULT_MESSAGE)
    message.update(data)
    for key in ("enabled", "permanent", "during_games"):
        if not isinstance(message[key], bool):
            raise ConfigError("'message.%s' must be true or false" % key)
    text = message["text"]
    if not isinstance(text, str) or len(text) > MESSAGE_MAX_TEXT:
        raise ConfigError("'message.text' must be a text of at most %d characters" % MESSAGE_MAX_TEXT)
    if any(ord(ch) < 32 and ch != "\n" for ch in text):
        raise ConfigError("'message.text' must not contain control characters")
    color = message["color"]
    if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
        raise ConfigError("'message.color' must look like #RRGGBB")
    for key, allowed in (("font", MESSAGE_FONTS), ("animation", MESSAGE_ANIMATIONS), ("speed", MESSAGE_SPEEDS)):
        if message[key] not in allowed:
            raise ConfigError("'message.%s' must be one of %s" % (key, ", ".join(allowed)))
    for key, low, high in (("intensity", 5, 100), ("seconds", 3, 120)):
        value = message[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ConfigError("'message.%s' must be a whole number from %d to %d" % (key, low, high))
    return message


def _parse_safety(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'safety' must be an object")
    safety = dict(DEFAULT_SAFETY)
    safety.update(data)
    if not isinstance(safety["enabled"], bool):
        raise ConfigError("'safety.enabled' must be true or false")
    value = safety["shutdown_temp_c"]
    if not isinstance(value, int) or isinstance(value, bool) or not SAFETY_MIN_TEMP <= value <= SAFETY_MAX_TEMP:
        raise ConfigError("'safety.shutdown_temp_c' must be a whole number from %d to %d" % (SAFETY_MIN_TEMP, SAFETY_MAX_TEMP))
    return safety


def _parse_picture(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("'picture' must be an object")
    picture = dict(DEFAULT_PICTURE)
    picture.update(data)
    for key in ("enabled", "permanent"):
        if not isinstance(picture[key], bool):
            raise ConfigError("'picture.%s' must be true or false" % key)
    if picture["fit"] not in PICTURE_FITS:
        raise ConfigError("'picture.fit' must be one of %s" % ", ".join(PICTURE_FITS))
    if picture["transition"] not in PICTURE_TRANSITIONS:
        raise ConfigError("'picture.transition' must be one of %s" % ", ".join(PICTURE_TRANSITIONS))
    for key, low, high in (("intensity", 5, 100), ("seconds", 3, 120), ("transition_ms", 100, 3000)):
        value = picture[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ConfigError("'picture.%s' must be a whole number from %d to %d" % (key, low, high))
    return picture


def load_config(path: str) -> Config:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ConfigError("cannot read configuration %s: %s" % (path, exc))
    except ValueError as exc:
        raise ConfigError("invalid JSON in %s: %s" % (path, exc))
    return parse_config(data)


def _as_str(data: Dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ConfigError("'%s' must be a non-empty string" % key)
    return value
