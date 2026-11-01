"""Translation between the web form and the JSON configuration file.

The configuration file stays the single source of truth. The form only
changes the keys it knows; everything else in the file is preserved.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config import (DEFAULT_SATELLITE_ITEM, DEFAULT_SATELLITES, SATELLITE_MAX, DEFAULT_PICTURE, PICTURE_FITS, PICTURE_TRANSITIONS, SAFETY_MAX_TEMP, SAFETY_MIN_TEMP, DEFAULT_SAFETY, DEFAULT_CAMERA, DEFAULT_MATRIX, DEFAULT_MESSAGE, DEFAULT_SENSOR, IDLE_MODES, ROOM_DESIGNS, SATELLITE_DESIGNS, MESSAGE_ANIMATIONS,
                     MESSAGE_FONTS, MESSAGE_MAX_TEXT, MESSAGE_SPEEDS, SENSOR_GPIOS, Config, parse_config)
from .errors import ConfigError
from .leagues import CUSTOM_ID, find_league, get_league
from .motion import PAUSE_OPTIONS, SENSITIVITY_LEVELS
from .system import valid_timezone

HARDWARE_MAPPINGS = ["adafruit-hat", "adafruit-hat-pwm", "regular", "classic"]

# (config key, label, minimum, maximum)
MATRIX_NUMBERS = [
    ("rows", "Zeilen je Panel", 8, 128),
    ("cols", "Spalten je Panel", 8, 128),
    ("chain_length", "Panels in Reihe", 1, 8),
    ("parallel", "Parallele Ketten", 1, 3),
    ("gpio_slowdown", "GPIO-Slowdown", 0, 4),
    ("brightness", "Helligkeit (%)", 1, 100),
    ("pwm_bits", "PWM-Bits", 1, 11),
]


# Camera resolutions offered on the camera page (the IMX219 sensor modes and a few common sizes).
CAMERA_PHOTO_SIZES = [(640, 480), (1280, 960), (1640, 1232), (1920, 1080), (3280, 2464)]
CAMERA_LIVE_SIZES = [(320, 240), (640, 480), (800, 600), (1280, 720)]
CAMERA_LIVE_FPS = [5, 10, 15, 20, 25]
CAMERA_BRIGHTNESS = [-50, -40, -30, -20, -10, 0, 10, 20, 30, 40, 50]          # percent, 0 = unchanged
CAMERA_SHARPNESS = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]                        # 1 = camera default


def size_text(width: int, height: int) -> str:
    return "%dx%d" % (width, height)


def _parse_size(text: str, allowed: List[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    for width, height in allowed:
        if text == size_text(width, height):
            return width, height
    return None


def apply_camera_form(raw: Dict[str, Any], form: Mapping[str, str], section: str) -> Tuple[Dict[str, Any], List[str]]:
    """Camera page forms. ``section`` is ``photo`` (resolution), ``live`` (resolution, frame rate), ``gallery`` (size limit),
    ``image`` (flip, brightness, sharpness) or ``motion`` (motion detection)."""
    errors: List[str] = []
    new = copy.deepcopy(raw)
    camera = dict(DEFAULT_CAMERA)
    camera.update(raw.get("camera", {}) if isinstance(raw.get("camera"), dict) else {})
    if section == "photo":
        size = _parse_size(form.get("photo_size", ""), CAMERA_PHOTO_SIZES)
        if size is None:
            errors.append("Foto-Auflösung: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["width"], camera["height"] = size
    elif section == "live":
        size = _parse_size(form.get("live_size", ""), CAMERA_LIVE_SIZES)
        if size is None:
            errors.append("Live-Auflösung: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["live_width"], camera["live_height"] = size
        try:
            fps = int(form.get("live_fps", ""))
        except ValueError:
            fps = -1
        if fps not in CAMERA_LIVE_FPS:
            errors.append("Bildrate: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["live_fps"] = fps
    elif section == "gallery":
        limit = _whole(form, "gallery_limit_mb", "Galerie: Speichergrenze (MB)", 10, 4000, errors)
        if limit is not None:
            camera["gallery_limit_mb"] = limit
    elif section == "image":
        camera["hflip"] = form.get("hflip") == "on"
        camera["vflip"] = form.get("vflip") == "on"
        try:
            brightness = int(form.get("brightness", ""))
        except ValueError:
            brightness = None
        if brightness not in CAMERA_BRIGHTNESS:
            errors.append("Helligkeit: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["brightness"] = brightness
        try:
            sharpness = float(form.get("sharpness", ""))
        except ValueError:
            sharpness = None
        if sharpness not in CAMERA_SHARPNESS:
            errors.append("Schärfe: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["sharpness"] = sharpness
    elif section == "motion":
        camera["motion_enabled"] = form.get("motion_enabled") == "on"
        try:
            level = int(form.get("motion_sensitivity", ""))
        except ValueError:
            level = -1
        if level not in SENSITIVITY_LEVELS:
            errors.append("Empfindlichkeit: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["motion_sensitivity"] = level
        try:
            pause = int(form.get("motion_pause", ""))
        except ValueError:
            pause = -1
        if pause not in PAUSE_OPTIONS:
            errors.append("Pause zwischen Fotos: bitte einen Eintrag aus der Liste wählen.")
        else:
            camera["motion_pause"] = pause
    else:
        errors.append("Unbekannter Abschnitt.")
    new["camera"] = camera
    if not errors:
        try:
            parse_config(new)
        except ConfigError as exc:
            errors.append(str(exc))
    return new, errors


def apply_picture_form(raw: Dict[str, Any], form: Mapping[str, str], has_picture: bool) -> Tuple[Dict[str, Any], List[str]]:
    """The options of the "Bild" page. Returns (new raw configuration, error messages)."""
    errors: List[str] = []
    new = copy.deepcopy(raw)
    picture = dict(DEFAULT_PICTURE)
    picture.update(raw.get("picture", {}) if isinstance(raw.get("picture"), dict) else {})
    for key in ("enabled", "permanent"):
        picture[key] = form.get(key, "") in ("on", "true", "1")
    if picture["enabled"] and not has_picture:
        errors.append("Bitte zuerst ein Bild hochladen, bevor die Anzeige aktiviert wird.")
    fit = form.get("fit", "")
    if fit in PICTURE_FITS:
        picture["fit"] = fit
    else:
        errors.append("Anpassung: bitte einen Eintrag aus der Liste wählen.")
    intensity = _whole(form, "intensity", "Helligkeit (%)", 5, 100, errors)
    if intensity is not None:
        picture["intensity"] = intensity
    seconds = _whole(form, "seconds", "Anzeigedauer je Bild (Sekunden)", 3, 120, errors)
    if seconds is not None:
        picture["seconds"] = seconds
    if "transition" in form:
        transition = form.get("transition", "")
        if transition in PICTURE_TRANSITIONS:
            picture["transition"] = transition
        else:
            errors.append("Übergang: bitte einen Eintrag aus der Liste wählen.")
    if "transition_ms" in form:
        duration = _whole(form, "transition_ms", "Dauer des Übergangs (ms)", 100, 3000, errors)
        if duration is not None:
            picture["transition_ms"] = duration
    new["picture"] = picture
    if not errors:
        try:
            parse_config(new)
        except ConfigError as exc:
            errors.append(str(exc))
    return new, errors


def apply_message_form(raw: Dict[str, Any], form: Mapping[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    """The "Nachricht" page. Returns (new raw configuration, error messages)."""
    errors: List[str] = []
    new = copy.deepcopy(raw)
    message = dict(DEFAULT_MESSAGE)
    message.update(raw.get("message", {}) if isinstance(raw.get("message"), dict) else {})
    for key in ("enabled", "permanent", "during_games"):
        message[key] = form.get(key, "") in ("on", "true", "1")
    text = form.get("text", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or (ch >= " " and ch != "\x7f")).strip()
    if len(text) > MESSAGE_MAX_TEXT:
        errors.append("Text: höchstens %d Zeichen." % MESSAGE_MAX_TEXT)
    elif message["enabled"] and not text:
        errors.append("Text: bitte einen Text eingeben, solange die Nachricht aktiv ist.")
    else:
        message["text"] = text
    color = form.get("color", "").strip().upper()
    if len(color) == 7 and color[0] == "#" and all(c in "0123456789ABCDEF" for c in color[1:]):
        message["color"] = color
    else:
        errors.append("Farbe: bitte eine Farbe wählen.")
    for key, label, allowed in (("font", "Schrift", MESSAGE_FONTS), ("animation", "Animation", MESSAGE_ANIMATIONS),
                                ("speed", "Tempo", MESSAGE_SPEEDS)):
        value = form.get(key, "")
        if value in allowed:
            message[key] = value
        else:
            errors.append("%s: bitte einen Eintrag aus der Liste wählen." % label)
    intensity = _whole(form, "intensity", "Intensität (%)", 5, 100, errors)
    if intensity is not None:
        message["intensity"] = intensity
    seconds = _whole(form, "seconds", "Anzeigedauer im Wechsel (Sekunden)", 3, 120, errors)
    if seconds is not None:
        message["seconds"] = seconds
    new["message"] = message
    if not errors:
        try:
            parse_config(new)
        except ConfigError as exc:
            errors.append(str(exc))
    return new, errors


ROOM_DESIGN_LABELS = {
    "c": "C – Klima im Fokus (Temperatur groß, Feuchte-Balken, Komfort-Hinweis)",
    "a": "A – Klassisch (große Digitaluhr, Datum, Symbole)",
    "b": "B – Analoguhr (Zeigeruhr, Werte, Datum)",
}
SATELLITE_DESIGN_LABELS = {
    "b": "B – Halbkreis-Anzeige (Luftindex im Bogen)",
    "a": "A – Liste mit Segment-Balken",
    "c": "C – Luftindex groß",
}
IDLE_MODE_LABELS = {
    "alternate": "Tabelle und Raumansicht im Wechsel",
    "table": "Nur Tabelle",
    "room": "Nur Raumansicht (Uhrzeit, Temperatur, Luftfeuchte)",
}

# (form field, config key, label, minimum, maximum)
ROTATION_NUMBERS = [
    ("rotation_main_seconds", "Hauptspiel: Sekunden je Durchlauf", 3, 600),
    ("rotation_card_seconds", "Ergebniskarte: Sekunden je Karte", 3, 600),
    ("result_window_minutes", "Ergebniskarte nach Spielende: Minuten", 1, 240),
    ("halftime_max_minutes", "Halbzeitkarte: höchstens … Minuten", 1, 90),
    ("idle_table_seconds", "Leerlauf: Sekunden Tabelle", 3, 600),
    ("idle_room_seconds", "Leerlauf: Sekunden Raumansicht", 3, 600),
    ("idle_kickoff_seconds", "Leerlauf: Sekunden Spielankündigung", 3, 600),
]


def load_raw(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError("cannot read %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ConfigError("configuration root must be a JSON object")
    return data


def write_raw(path: str, data: Dict[str, Any]) -> None:
    """Replace the configuration file atomically (the other services notice the new modification time)."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except OSError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mapping_to_text(mapping: Mapping[str, str]) -> str:
    return "\n".join("%s=%s" % (key, value) for key, value in mapping.items())


def text_to_mapping(text: str, label: str, errors: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            errors.append("%s, Zeile %d: erwartet wird Team-ID=Text." % (label, number))
            continue
        result[key] = value
    return result


def config_to_values(cfg: Config) -> Dict[str, Any]:
    """Flat values for the form fields, taken from the current configuration."""
    league = find_league(cfg.provider, cfg.provider_params)
    values: Dict[str, Any] = {
        "league": league.league_id if league else CUSTOM_ID,
        "favorite_team_id": cfg.favorite_team_id,
        "show_team_names": cfg.show_team_names,
        "show_game_clock": cfg.show_game_clock,
        "table_label": cfg.table_label,
        "table_logo_size": str(cfg.table_logo_size),
        "timezone": cfg.timezone,
        "poll_game_seconds": str(cfg.poll_game_seconds),
        "poll_idle_seconds": str(cfg.poll_idle_seconds),
        "hardware_mapping": str(cfg.matrix.get("hardware_mapping", DEFAULT_MATRIX["hardware_mapping"])),
        "team_cities": mapping_to_text(cfg.team_cities),
        "team_labels": mapping_to_text(cfg.team_labels),
    }
    for key, _label, _lo, _hi in MATRIX_NUMBERS:
        values["matrix_" + key] = str(cfg.matrix.get(key, DEFAULT_MATRIX[key]))
    values["show_other_games"] = cfg.show_other_games
    values["gameday_results"] = cfg.gameday_results
    values["idle_mode"] = cfg.idle_mode
    values["room_design"] = cfg.room_design
    values["satellite_design"] = cfg.satellite_design
    values["idle_hide_table"] = cfg.idle_hide_table
    for key, _label, _lo, _hi in ROTATION_NUMBERS:
        values[key] = str(getattr(cfg, key))
    sensor, camera = cfg.sensor, cfg.camera
    values["sensor_enabled"] = bool(sensor["enabled"])
    values["sensor_gpio"] = str(sensor["gpio"])
    values["sensor_interval_seconds"] = str(sensor["interval_seconds"])
    values["sensor_temp_offset"] = format_decimal(sensor["temp_offset"])
    values["sensor_humidity_offset"] = format_decimal(sensor["humidity_offset"])
    values["camera_enabled"] = bool(camera["enabled"])
    values["safety_enabled"] = bool(cfg.safety["enabled"])
    values["safety_shutdown_temp_c"] = str(cfg.safety["shutdown_temp_c"])
    return values


def form_to_values(form: Mapping[str, str]) -> Dict[str, Any]:
    """The same flat values, taken from a submitted form (to show it again after an error)."""
    values: Dict[str, Any] = {key: form.get(key, "") for key in form}
    values["show_team_names"] = form.get("show_team_names") == "on"
    values["show_game_clock"] = form.get("show_game_clock") == "on"
    for key in ("show_other_games", "gameday_results", "idle_hide_table", "sensor_enabled", "camera_enabled", "safety_enabled"):
        values[key] = form.get(key) == "on"
    return values


def format_decimal(value: float) -> str:
    """Decimal comma for the form (German), no trailing zeros: 2.5 -> '2,5', -1.0 -> '-1'."""
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return (text if text not in ("", "-0") else "0").replace(".", ",")


def _decimal(form: Mapping[str, str], name: str, label: str, limit: float, errors: List[str]) -> Optional[float]:
    text = form.get(name, "").strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        errors.append("%s: bitte eine Zahl eingeben (Beispiel: -1,5)." % label)
        return None
    if value != value or abs(value) > limit:
        errors.append("%s: erlaubt sind Werte von -%g bis %g." % (label, limit, limit))
        return None
    return round(value, 2)


def _whole(form: Mapping[str, str], name: str, label: str, low: int, high: int, errors: List[str]) -> Optional[int]:
    text = form.get(name, "").strip()
    try:
        value = int(text)
    except ValueError:
        errors.append("%s: bitte eine ganze Zahl eingeben." % label)
        return None
    if not low <= value <= high:
        errors.append("%s: erlaubt sind Werte von %d bis %d." % (label, low, high))
        return None
    return value


def apply_form(raw: Dict[str, Any], form: Mapping[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (new raw configuration, error messages). With errors the new configuration must not be saved."""
    errors: List[str] = []
    new = copy.deepcopy(raw)

    league_id = form.get("league", "").strip()
    if league_id and league_id != CUSTOM_ID:
        league = get_league(league_id)
        if league is None:
            errors.append("Unbekannte Liga.")
        else:
            current = find_league(str(raw.get("provider", "")), dict(raw.get("provider_params", {})))
            if current is None or current.league_id != league.league_id:
                new["provider"] = league.provider
                new["provider_params"] = dict(league.params)

    favorite = form.get("favorite_team_id", "").strip()
    if not favorite:
        errors.append("Bitte ein Lieblingsteam wählen.")
    new["favorite_team_id"] = favorite

    new["show_team_names"] = form.get("show_team_names") == "on"
    new["show_game_clock"] = form.get("show_game_clock") == "on"

    label = form.get("table_label", "")
    if label not in ("city", "short"):
        errors.append("Tabellenbeschriftung: bitte Stadt oder Kürzel wählen.")
    else:
        new["table_label"] = label
    size = _whole(form, "table_logo_size", "Logogröße in der Tabelle", 0, 16, errors)
    if size is not None:
        new["table_logo_size"] = size

    zone = form.get("timezone", "").strip()
    if not valid_timezone(zone):
        errors.append("Zeitzone: unbekannter Name (Beispiel: Europe/Berlin).")
    else:
        new["timezone"] = zone

    game = _whole(form, "poll_game_seconds", "Abfrage an Spieltagen (Sekunden)", 15, 3600, errors)
    idle = _whole(form, "poll_idle_seconds", "Abfrage sonst (Sekunden)", 60, 86400, errors)
    if game is not None:
        new["poll_game_seconds"] = game
    if idle is not None:
        new["poll_idle_seconds"] = idle

    matrix = dict(DEFAULT_MATRIX)
    matrix.update(raw.get("matrix", {}) if isinstance(raw.get("matrix"), dict) else {})
    for key, text, low, high in MATRIX_NUMBERS:
        value = _whole(form, "matrix_" + key, text, low, high, errors)
        if value is not None:
            matrix[key] = value
    mapping = form.get("hardware_mapping", "")
    if mapping not in HARDWARE_MAPPINGS:
        errors.append("Hardware-Mapping: bitte einen Eintrag aus der Liste wählen.")
    else:
        matrix["hardware_mapping"] = mapping
    new["matrix"] = matrix

    new["show_other_games"] = form.get("show_other_games") == "on"
    new["gameday_results"] = form.get("gameday_results") == "on"
    new["idle_hide_table"] = form.get("idle_hide_table") == "on"
    mode = form.get("idle_mode", "")
    if mode not in IDLE_MODES:
        errors.append("Spielfreie Zeit: bitte einen Eintrag aus der Liste wählen.")
    else:
        new["idle_mode"] = mode
    for key, text, allowed in (("room_design", "Raumansicht", ROOM_DESIGNS), ("satellite_design", "Satellitenseite", SATELLITE_DESIGNS)):
        if key not in form:
            continue                                       # an older form without the field keeps the stored design
        choice = form.get(key, "")
        if choice not in allowed:
            errors.append("%s: bitte ein Design aus der Liste wählen." % text)
        else:
            new[key] = choice
    for key, text, low, high in ROTATION_NUMBERS:
        value = _whole(form, key, text, low, high, errors)
        if value is not None:
            new[key] = value

    sensor = dict(DEFAULT_SENSOR)
    sensor.update(raw.get("sensor", {}) if isinstance(raw.get("sensor"), dict) else {})
    sensor["enabled"] = form.get("sensor_enabled") == "on"
    gpio = _whole(form, "sensor_gpio", "Sensor: GPIO", 2, 27, errors)
    if gpio is not None:
        if gpio not in SENSOR_GPIOS:
            errors.append("Sensor: GPIO%d ist am Bonnet nicht frei. Erlaubt: %s." % (gpio, ", ".join(str(g) for g in SENSOR_GPIOS)))
        else:
            sensor["gpio"] = gpio
    interval = _whole(form, "sensor_interval_seconds", "Sensor: Messabstand (Sekunden)", 5, 3600, errors)
    if interval is not None:
        sensor["interval_seconds"] = interval
    offset = _decimal(form, "sensor_temp_offset", "Sensor: Temperatur-Korrektur (°C)", 20, errors)
    if offset is not None:
        sensor["temp_offset"] = offset
    offset = _decimal(form, "sensor_humidity_offset", "Sensor: Feuchte-Korrektur (%)", 30, errors)
    if offset is not None:
        sensor["humidity_offset"] = offset
    new["sensor"] = sensor

    camera = dict(DEFAULT_CAMERA)
    camera.update(raw.get("camera", {}) if isinstance(raw.get("camera"), dict) else {})
    camera["enabled"] = form.get("camera_enabled") == "on"
    new["camera"] = camera

    # The safety section is only changed when the form contains it (older forms and tests leave it alone).
    if "safety_shutdown_temp_c" in form:
        safety = dict(DEFAULT_SAFETY)
        safety.update(raw.get("safety", {}) if isinstance(raw.get("safety"), dict) else {})
        safety["enabled"] = form.get("safety_enabled") == "on"
        limit = _whole(form, "safety_shutdown_temp_c", "Sicherheit: Abschalttemperatur (°C)", SAFETY_MIN_TEMP, SAFETY_MAX_TEMP, errors)
        if limit is not None:
            safety["shutdown_temp_c"] = limit
        new["safety"] = safety

    new["team_cities"] = text_to_mapping(form.get("team_cities", ""), "Städtenamen", errors)
    new["team_labels"] = text_to_mapping(form.get("team_labels", ""), "Kürzel", errors)

    if not errors:
        try:
            parse_config(new)
        except ConfigError as exc:
            errors.append(str(exc))
    return new, errors


def apply_satellites_form(raw: Dict[str, Any], form: Mapping[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    """The "Satelliten" page: general switch, MQTT broker and up to three satellites (rows ``s1_`` .. ``s3_``)."""
    errors: List[str] = []
    new = copy.deepcopy(raw)
    old = copy.deepcopy(DEFAULT_SATELLITES)
    if isinstance(raw.get("satellites"), dict):
        old.update(raw["satellites"])
    sat = copy.deepcopy(DEFAULT_SATELLITES)
    sat["enabled"] = form.get("enabled", "") in ("on", "true", "1")
    seconds = _whole(form, "seconds", "Anzeigedauer je Satellit (Sekunden)", 3, 120, errors)
    sat["seconds"] = seconds if seconds is not None else old.get("seconds", 8)
    mqtt = dict(DEFAULT_SATELLITES["mqtt"])
    mqtt.update(old.get("mqtt", {}) if isinstance(old.get("mqtt"), dict) else {})
    mqtt["enabled"] = form.get("mqtt_enabled", "") in ("on", "true", "1")
    mqtt["host"] = form.get("mqtt_host", "").strip()
    port = _whole(form, "mqtt_port", "MQTT-Port", 1, 65535, errors)
    if port is not None:
        mqtt["port"] = port
    mqtt["username"] = form.get("mqtt_username", "").strip()
    if form.get("mqtt_password", ""):                      # empty = keep the stored password
        mqtt["password"] = form["mqtt_password"]
    if form.get("mqtt_clear_password", "") in ("on", "true", "1"):
        mqtt["password"] = ""
    mqtt["base_topic"] = form.get("mqtt_base_topic", "").strip() or DEFAULT_SATELLITES["mqtt"]["base_topic"]
    sat["mqtt"] = mqtt
    items: List[Dict[str, Any]] = []
    previous = {entry.get("key"): entry for entry in old.get("items", []) if isinstance(entry, dict)}
    for number in range(1, SATELLITE_MAX + 1):
        prefix = "s%d_" % number
        name = form.get(prefix + "name", "").strip()
        key = form.get(prefix + "key", "").strip().lower()
        if not name and not key:
            continue                                        # empty row
        if not key:
            key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "sat%d" % number
        item = dict(DEFAULT_SATELLITE_ITEM)
        item.update(previous.get(key, {}))
        item.update({"key": key, "name": name or key[:10],
                     "enabled": form.get(prefix + "enabled", "") in ("on", "true", "1"),
                     "token": form.get(prefix + "token", "").strip(),
                     "topic": form.get(prefix + "topic", "").strip()})
        if not item["token"]:
            item["token"] = secrets.token_urlsafe(9)        # 12 characters, shown on the page afterwards
        for field, label in (("gas_clean", "Gaswert saubere Luft"), ("gas_bad", "Gaswert schlechte Luft")):
            if prefix + field not in form:
                continue
            value = _whole(form, prefix + field, "Satellit %d: %s" % (number, label), 0, 65535, errors)
            if value is not None:
                item[field] = value
        items.append(item)
    sat["items"] = items
    new["satellites"] = sat
    if not errors:
        try:
            parse_config(new)
        except ConfigError as exc:
            errors.append(str(exc))
    return new, errors
