"""Web backend (Flask): login, dashboard with live preview, settings, system functions.

    python -m sportstracker.service_web --config config/config.json

The backend only edits the JSON configuration file and calls a few system
commands; the data and display services pick up configuration changes by
themselves (they watch the file's modification time).
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for

from . import __version__
from .auth import AuthStore, LoginThrottle, password_problem
from .banner import ANIMATIONS, FONT_CHOICES, SPEED_LABELS, BannerRenderer, preview_gif
from .camera import CameraControl, CameraError, ImageOptions
from .motion import PAUSE_OPTIONS, SENSITIVITY_LABELS, MotionSettings, pause_label
from .config import (DEFAULT_SATELLITE_ITEM, MESSAGE_MAX_TEXT, SAFETY_MAX_TEMP, SAFETY_MIN_TEMP, SATELLITE_MAX,
                     SENSOR_GPIOS, Config, load_config)
from .errors import ConfigError
from .gallery import MB, Gallery, GalleryError, valid_name
from .http import HttpClient
from .leagues import CUSTOM_ID, LEAGUES, find_league, get_league
from .models import Team
from .picture import (FIT_LABELS, MAX_IMAGES, MAX_UPLOAD_BYTES, SECONDS_MAX, SECONDS_MIN, TRANSITION_LABELS,
                      TRANSITION_MS_OPTIONS, PictureError, PictureRenderer, PictureStore)
from .picture import valid_id as valid_picture_id
from .preview import upscale
from .providers import create_provider
from .runtime import PROJECT_ROOT, build_logo_cache, build_renderer, matrix_size, resolve, state_path
from .safety import read_safety, safety_path
from .satellites import (MAX_AGE_SECONDS as SAT_MAX_AGE, PayloadError, air_quality, parse_payload, read_satellites,
                         satellites_path, store_reading)
from .sensor import read_sensor, sensor_path
from .settings import (CAMERA_BRIGHTNESS, CAMERA_LIVE_FPS, CAMERA_LIVE_SIZES, CAMERA_PHOTO_SIZES, CAMERA_SHARPNESS, HARDWARE_MAPPINGS, IDLE_MODE_LABELS,
                       MATRIX_NUMBERS, ROTATION_NUMBERS, apply_camera_form, apply_form, apply_message_form, apply_picture_form,
                       apply_satellites_form,
                       config_to_values,
                       form_to_values, load_raw, size_text, write_raw)
from .state import read_state
from .system import SERVICE_LABELS, SERVICES, SystemControl, timezone_names, valid_timezone
from .views import plan as build_plan, render_view, view_for_item

log = logging.getLogger("sportstracker.web")

FORM_LIMIT = 64 * 1024            # ordinary forms; the picture upload may carry several files (MAX_REQUEST_BYTES)
MAX_REQUEST_BYTES = 50 * 1024 * 1024
TEAMS_TTL = 3600.0
PREVIEW_WIDTH = 384

TeamsLoader = Callable[[str, Dict[str, Any], Any], List[Team]]


def default_teams_loader(provider: str, params: Dict[str, Any], tz: Any) -> List[Team]:
    return create_provider(provider, params, HttpClient(), tz).list_teams()


class Preview:
    """Renders what the matrix shows right now, enlarged. Cached until config, state or the picture kind changes."""

    def __init__(self, config_path: str, root: str, now_func: Callable[[], datetime]) -> None:
        self.config_path = config_path
        self.root = root
        self._now = now_func
        self._renderer_key: Any = None
        self._renderer: Any = None
        self._fonts: Any = None
        self._png_key: Any = None
        self._png = b""

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def png(self) -> bytes:
        cfg = load_config(self.config_path)
        tz = ZoneInfo(cfg.timezone)
        cfg_mtime = self._mtime(self.config_path)
        if self._renderer is None or self._renderer_key != cfg_mtime:
            from .render import load_fonts  # the fonts take a second on a Pi Zero, so keep them

            if self._fonts is None:
                self._fonts = load_fonts(resolve(cfg.fonts_dir, self.root))
            logos = build_logo_cache(cfg, http=None, read_only=True, root=self.root)
            self._renderer = build_renderer(cfg, logos, tz, root=self.root, fonts=self._fonts)
            self._renderer_key = cfg_mtime
        path = state_path(cfg, self.root)
        state_mtime = self._mtime(path)
        spath = sensor_path(resolve(cfg.data_dir, self.root))
        sensor_mtime = self._mtime(spath)
        now = self._now()
        state = read_state(path)
        current = build_plan(state, now, cfg, tz, 2 * cfg.poll_idle_seconds + 60,
                             picture=self._renderer.picture.available(),
                             picture_seconds=self._renderer.picture.total_seconds(cfg.picture))
        item, elapsed = current.locate(now)
        sat_file = satellites_path(resolve(cfg.data_dir, self.root))
        sat_mtime = self._mtime(sat_file)
        view = view_for_item(item, state, now, current.stale, read_sensor(spath), cfg, elapsed,
                             read_satellites(sat_file))
        identity = (item.kind, item.game.game_id if item.game is not None else None, item.role, item.satellite)
        minute = int(now.timestamp() // 60) if item.kind in ("room", "satellite") else None
        if item.kind == "banner":
            minute = int(now.timestamp() * 4)          # an animated message changes all the time
        elif item.kind == "picture":
            minute = self._renderer.picture.state_key(cfg.picture, elapsed)
        key = (cfg_mtime, state_mtime, sensor_mtime, sat_mtime, now.astimezone(tz).date(), identity, view.stale, minute)
        if key != self._png_key:
            image = render_view(self._renderer, view, cfg.favorite_team_id)
            factor = max(2, PREVIEW_WIDTH // max(image.size))
            out = io.BytesIO()
            upscale(image, factor).save(out, format="PNG")
            self._png, self._png_key = out.getvalue(), key
        return self._png


def create_app(
    config_path: str,
    root: str = PROJECT_ROOT,
    system: Optional[SystemControl] = None,
    teams_loader: TeamsLoader = default_teams_loader,
    now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    throttle: Optional[LoginThrottle] = None,
    camera: Optional[CameraControl] = None,
) -> Flask:
    cfg0 = load_config(config_path)  # fail early with a clear message
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
    auth = AuthStore(os.path.join(resolve(cfg0.data_dir, root), "auth.json"))
    auth.ensure()
    app.secret_key = auth.secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="sportstracker_session",
        PERMANENT_SESSION_LIFETIME=12 * 3600,
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES + 256 * 1024,      # only the picture upload may be this large (see protect)
    )
    system = system or SystemControl(data_path=root)
    throttle = throttle or LoginThrottle()
    preview = Preview(config_path, root, now_func)
    camera = camera or CameraControl(os.path.join(resolve(cfg0.cache_dir, root), "camera"), runner=system.runner)
    gallery = Gallery(os.path.join(resolve(cfg0.data_dir, root), "gallery"))
    teams_cache: Dict[str, Tuple[float, List[Team]]] = {}
    app.extensions["sportstracker_auth"] = auth

    # -- helpers ----------------------------------------------------------------

    def current_config() -> Config:
        return load_config(config_path)

    def load_teams(provider: str, params: Dict[str, Any], tz_name: str) -> List[Team]:
        key = json.dumps([provider, params], sort_keys=True)
        cached = teams_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < TEAMS_TTL:
            return cached[1]
        teams = sorted(teams_loader(provider, params, ZoneInfo(tz_name)), key=lambda t: t.name.lower())
        teams_cache[key] = (time.monotonic(), teams)
        return teams

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]

    def logged_in() -> bool:
        return bool(session.get("user")) and session.get("user") == auth.username

    def fmt_time(value: Optional[datetime], tz_name: str) -> str:
        if value is None:
            return "–"
        return value.astimezone(ZoneInfo(tz_name)).strftime("%d.%m.%Y %H:%M:%S")

    def client_address() -> str:
        return request.remote_addr or "?"

    def camera_enabled() -> bool:
        try:
            return bool(current_config().camera["enabled"])
        except ConfigError:
            return False

    app.jinja_env.globals.update(csrf_token=csrf_token, version=__version__, logged_in=logged_in,
                                 SERVICE_LABELS=SERVICE_LABELS, camera_enabled=camera_enabled)

    @app.before_request
    def protect() -> Any:
        if request.endpoint is None or request.endpoint == "static":
            return None
        if request.endpoint == "satellite_push":
            if (request.content_length or 0) > 2048:
                abort(413)
            return None                                    # no login and no CSRF token: the satellite's token counts
        if request.method == "POST":
            if request.endpoint != "picture_upload" and (request.content_length or 0) > FORM_LIMIT:
                abort(413)
            sent = request.form.get("csrf_token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Ungültiges Formular-Token. Bitte die Seite neu laden und noch einmal versuchen.")
        if request.endpoint not in ("login",) and not logged_in():
            return redirect(url_for("login"))
        return None

    @app.after_request
    def headers(response: Any) -> Any:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def too_large(error: Any) -> Any:
        return render_template("error.html", message="Die Anfrage ist zu groß."), 413

    @app.errorhandler(400)
    def bad_request(error: Any) -> Any:
        return render_template("error.html", message=getattr(error, "description", "Ungültige Anfrage.")), 400

    # -- login ------------------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if logged_in():
            return redirect(url_for("dashboard"))
        csrf_token()
        if request.method == "POST":
            wait = throttle.retry_after(client_address())
            if wait > 0:
                flash("Zu viele Fehlversuche. Bitte in %d Sekunden noch einmal versuchen." % int(wait + 1), "error")
                return render_template("login.html"), 429
            if auth.check(request.form.get("username", ""), request.form.get("password", "")):
                throttle.succeeded(client_address())
                session.clear()
                session["user"] = auth.username
                session["csrf"] = secrets.token_urlsafe(24)
                session.permanent = True
                return redirect(url_for("dashboard"))
            throttle.failed(client_address())
            flash("Benutzername oder Kennwort falsch.", "error")
            return render_template("login.html"), 401
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout() -> Any:
        session.clear()
        return redirect(url_for("login"))

    # -- dashboard --------------------------------------------------------------

    def safety_notice(cfg: Config, now: Any) -> Optional[str]:
        """Warning text if the safety service powered the Pi off recently (the cause of an unexpected restart)."""
        record = read_safety(safety_path(resolve(cfg.data_dir, root)))
        if record is None or not record.is_recent(now):
            return None
        return "Der Sicherheitsdienst hat den Raspberry Pi am %s bei %s °C abgeschaltet (Grenze %s °C). Für bessere Kühlung sorgen, siehe Seite System." % (
            fmt_time(record.shutdown_at, cfg.timezone),
            ("%.1f" % record.temperature_c).replace(".", ",") if record.temperature_c is not None else "?",
            record.limit_c if record.limit_c is not None else "?")

    @app.route("/")
    def dashboard() -> Any:
        cfg = current_config()
        tz = ZoneInfo(cfg.timezone)
        state = read_state(state_path(cfg, root))
        now = now_func()
        pictures = PictureStore(resolve(cfg.data_dir, root))
        current = build_plan(state, now, cfg, tz, 2 * cfg.poll_idle_seconds + 60, picture=pictures.count() > 0,
                             picture_seconds=picture_renderer(cfg).total_seconds(cfg.picture))
        item, elapsed = current.locate(now)
        reading = read_sensor(sensor_path(resolve(cfg.data_dir, root)))
        league = find_league(cfg.provider, cfg.provider_params)
        favorite = cfg.favorite_team_id
        if state is not None:
            for row in state.table or []:
                if row.team.team_id == favorite:
                    favorite = row.team.name
                    break
        def describe(game: Any) -> str:
            score = "%s:%s" % (game.home_score, game.away_score) if game.home_score is not None else "–:–"
            return "%s – %s  %s  (%s)" % (game.home.name, game.away.name, score, game.status.value)

        kind_labels = {"game": "Spiel", "table": "Tabelle", "room": "Raumansicht", "message": "Meldung",
                       "banner": "Nachricht", "picture": "Bild"}
        role_labels = {"main": "Hauptspiel", "card": "Ergebniskarte", "idle": ""}
        plan_rows = []
        for entry in current.items:
            text = kind_labels.get(entry.kind, entry.kind)
            if entry.game is not None:
                text += ": " + describe(entry.game)
            plan_rows.append({
                "text": text, "role": role_labels.get(entry.role, ""), "seconds": entry.seconds,
                "current": entry is item and len(current.items) > 1,
            })
        game_text = describe(item.game) if item.game is not None else None
        view = view_for_item(item, state, now, current.stale, reading, cfg, elapsed)
        climate = None
        if cfg.sensor.get("enabled"):
            climate = {
                "fresh": reading is not None and reading.is_fresh(now),
                "temperature": None if reading is None or reading.temperature_c is None else ("%.1f" % reading.temperature_c).replace(".", ","),
                "humidity": None if reading is None or reading.humidity_pct is None else ("%.0f" % reading.humidity_pct),
                "updated": fmt_time(reading.updated_at if reading else None, cfg.timezone),
                "error": reading.error if reading else "Noch keine Messung (Sensordienst läuft?)",
            }
        return render_template(
            "dashboard.html",
            safety_notice=safety_notice(cfg, now),
            cfg=cfg,
            league_label=league.label if league else "Benutzerdefiniert (%s)" % cfg.provider,
            favorite=favorite,
            view=view,
            game_text=game_text,
            plan_rows=plan_rows,
            plan_reason=current.reason,
            climate=climate,
            state=state,
            updated=fmt_time(state.updated_at if state else None, cfg.timezone),
            next_poll=fmt_time(state.next_poll_at if state else None, cfg.timezone),
            error_since=fmt_time(state.error_since if state else None, cfg.timezone),
            services=system.service_states(),
            default_password=auth.uses_default_password,
        )

    @app.route("/preview.png")
    def preview_png() -> Any:
        try:
            data = preview.png()
        except Exception:  # noqa: BLE001 - show a clear status instead of a broken image
            log.exception("preview failed")
            abort(500)
        return send_file(io.BytesIO(data), mimetype="image/png")

    # -- settings ---------------------------------------------------------------

    def settings_context(cfg: Config, values: Dict[str, Any], league_id: str) -> Dict[str, Any]:
        league = get_league(league_id) if league_id != CUSTOM_ID else None
        provider = league.provider if league else cfg.provider
        params = dict(league.params) if league else dict(cfg.provider_params)
        teams: Optional[List[Team]] = None
        try:
            teams = load_teams(provider, params, cfg.timezone)
        except Exception as exc:  # noqa: BLE001 - any provider/network problem: fall back to a text field
            log.warning("could not load teams: %s", exc)
            flash("Die Teamliste konnte nicht geladen werden (%s). Die Team-ID kann von Hand eingetragen werden." % exc, "warning")
        return dict(
            cfg=cfg, v=values, league_id=league_id, leagues=LEAGUES, teams=teams, mappings=HARDWARE_MAPPINGS,
            matrix_numbers=MATRIX_NUMBERS, zones=timezone_names(), rotation_numbers=ROTATION_NUMBERS,
            idle_modes=IDLE_MODE_LABELS, sensor_gpios=SENSOR_GPIOS, safety_min=SAFETY_MIN_TEMP, safety_max=SAFETY_MAX_TEMP,
            saved_league=config_to_values(cfg)["league"],
        )

    @app.route("/settings", methods=["GET"])
    def settings() -> Any:
        cfg = current_config()
        values = config_to_values(cfg)
        league_id = request.args.get("league") or values["league"]
        if league_id != CUSTOM_ID and get_league(league_id) is None:
            league_id = values["league"]
        values["league"] = league_id
        if league_id != config_to_values(cfg)["league"]:
            values["favorite_team_id"] = ""
        return render_template("settings.html", **settings_context(cfg, values, league_id))

    @app.route("/settings", methods=["POST"])
    def settings_save() -> Any:
        cfg = current_config()
        raw = load_raw(config_path)
        new_raw, errors = apply_form(raw, request.form)
        league_id = request.form.get("league", "") or CUSTOM_ID
        if not errors:
            league = get_league(league_id) if league_id != CUSTOM_ID else None
            provider = league.provider if league else cfg.provider
            params = dict(league.params) if league else dict(cfg.provider_params)
            try:
                known = {t.team_id for t in load_teams(provider, params, cfg.timezone)}
            except Exception:  # noqa: BLE001 - cannot verify, trust the input
                known = set()
            if known and new_raw["favorite_team_id"] not in known:
                errors.append("Das gewählte Team gehört nicht zu dieser Liga.")
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("settings.html", **settings_context(cfg, form_to_values(request.form), league_id)), 400
        write_raw(config_path, new_raw)
        notes = ["Einstellungen gespeichert. Die Dienste übernehmen sie innerhalb weniger Sekunden."]
        if new_raw["matrix"] != raw.get("matrix", cfg.matrix):
            notes.append("Die Matrix-Einstellungen führen zu einem kurzen Neustart der Anzeige.")
        if new_raw["timezone"] != cfg.timezone:
            ok, message = system.set_timezone(new_raw["timezone"])
            notes.append(message if ok else "Systemzeitzone nicht geändert: " + message)
        for note in notes:
            flash(note, "ok")
        return redirect(url_for("settings"))

    # -- message ("Nachricht") ----------------------------------------------------

    banner_cache: Dict[str, Any] = {}

    def banner_renderer(cfg: Config) -> BannerRenderer:
        key = (resolve(cfg.fonts_dir, root), matrix_size(cfg))
        if banner_cache.get("key") != key:
            banner_cache["key"] = key
            banner_cache["renderer"] = BannerRenderer(key[0], key[1][0], key[1][1])
        return banner_cache["renderer"]

    def preview_args_for(message: Dict[str, Any]) -> Dict[str, str]:
        return {"text": message["text"], "color": message["color"], "font": message["font"],
                "intensity": str(message["intensity"]), "animation": message["animation"], "speed": message["speed"]}

    def message_page(cfg: Config, message: Dict[str, Any], preview_args: Dict[str, str], status: int = 200) -> Any:
        return render_template(
            "message.html", cfg=cfg, m=message, fonts=FONT_CHOICES, animations=ANIMATIONS, speeds=SPEED_LABELS,
            preview_args=preview_args, max_text=MESSAGE_MAX_TEXT,
        ), status

    def message_from_form(form: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
        """The values of a rejected form, as far as they can be shown again."""
        message = dict(fallback)
        message.update({
            "enabled": form.get("enabled") == "on", "permanent": form.get("permanent") == "on",
            "during_games": form.get("during_games") == "on", "text": form.get("text", "")[:MESSAGE_MAX_TEXT],
            "color": form.get("color", fallback["color"]), "font": form.get("font", fallback["font"]),
            "animation": form.get("animation", fallback["animation"]), "speed": form.get("speed", fallback["speed"]),
            "intensity": form.get("intensity", fallback["intensity"]), "seconds": form.get("seconds", fallback["seconds"]),
        })
        return message

    @app.route("/message", methods=["GET"])
    def message_form() -> Any:
        cfg = current_config()
        return message_page(cfg, cfg.message, {})

    @app.route("/message", methods=["POST"])
    def message_save() -> Any:
        cfg = current_config()
        new_raw, errors = apply_message_form(load_raw(config_path), request.form)
        if errors:
            for text in errors:
                flash(text, "error")
            return message_page(cfg, message_from_form(request.form, cfg.message), {}, 400)
        if request.form.get("action") == "preview":
            return message_page(cfg, new_raw["message"], preview_args_for(new_raw["message"]))
        write_raw(config_path, new_raw)
        flash("Nachricht gespeichert. Die Anzeige übernimmt sie innerhalb weniger Sekunden.", "ok")
        return redirect(url_for("message_form"))

    @app.route("/message/preview.gif")
    def message_preview() -> Any:
        cfg = current_config()
        message = cfg.message
        if "text" in request.args:
            form = request.args.to_dict()
            form.setdefault("seconds", "10")
            new_raw, errors = apply_message_form({}, form)
            if errors:
                abort(400)
            message = new_raw["message"]
        try:
            data = preview_gif(banner_renderer(cfg), message)
        except Exception:  # noqa: BLE001
            log.exception("message preview failed")
            abort(500)
        return send_file(io.BytesIO(data), mimetype="image/gif")

    # -- picture ("Bild"): gallery ----------------------------------------------------

    def picture_dir(cfg: Config) -> str:
        return resolve(cfg.data_dir, root)

    def picture_store(cfg: Config) -> PictureStore:
        return PictureStore(picture_dir(cfg))

    def picture_renderer(cfg: Config) -> PictureRenderer:
        width, height = matrix_size(cfg)
        return PictureRenderer(picture_dir(cfg), width, height)

    def picture_page(cfg: Config, picture: Dict[str, Any], status: int = 200,
                     typed_seconds: Optional[Dict[str, str]] = None) -> Any:
        store = picture_store(cfg)
        store.migrate()
        renderer = picture_renderer(cfg)
        items = store.items()
        args = {"fit": picture["fit"], "intensity": str(picture["intensity"]), "seconds": str(picture["seconds"]),
                "transition": picture["transition"], "transition_ms": str(picture["transition_ms"]),
                "v": str(int(renderer.mtime() * 1000))}
        total = renderer.total_seconds(picture) if items else 0
        return render_template(
            "picture.html", cfg=cfg, p=picture, items=items, count=len(items), max_images=MAX_IMAGES,
            fits=FIT_LABELS, transitions=TRANSITION_LABELS, transition_options=TRANSITION_MS_OPTIONS,
            preview_args=args, loop_seconds=renderer.loop_seconds(), total_seconds=total,
            typed=typed_seconds or {}, max_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        ), status

    @app.route("/picture", methods=["GET"])
    def picture_form() -> Any:
        cfg = current_config()
        return picture_page(cfg, cfg.picture)

    @app.route("/picture", methods=["POST"])
    def picture_save() -> Any:
        cfg = current_config()
        store = picture_store(cfg)
        items = store.items()
        new_raw, errors = apply_picture_form(load_raw(config_path), request.form, bool(items))
        times: Dict[str, Optional[int]] = {}
        typed: Dict[str, str] = {}
        for item in items:
            text = request.form.get("seconds_" + item["id"], "").strip()
            typed[item["id"]] = text
            if not text:
                times[item["id"]] = None
                continue
            try:
                value = int(text)
            except ValueError:
                value = -1
            if not SECONDS_MIN <= value <= SECONDS_MAX:
                errors.append("Anzeigedauer eines Bildes: bitte eine ganze Zahl von %d bis %d oder leer lassen." % (
                    SECONDS_MIN, SECONDS_MAX))
                break
            times[item["id"]] = value
        if errors:
            for text in errors:
                flash(text, "error")
            shown = dict(cfg.picture)
            for key in ("fit", "intensity", "seconds", "transition", "transition_ms"):
                shown[key] = request.form.get(key, shown[key])
            shown.update({"enabled": request.form.get("enabled") == "on",
                          "permanent": request.form.get("permanent") == "on"})
            return picture_page(cfg, shown, 400, typed)
        if request.form.get("action") == "preview":
            return picture_page(cfg, new_raw["picture"], 200, typed)
        store.set_seconds(times)
        write_raw(config_path, new_raw)
        flash("Bild-Einstellungen gespeichert. Die Anzeige übernimmt sie innerhalb weniger Sekunden.", "ok")
        return redirect(url_for("picture_form"))

    @app.route("/picture/upload", methods=["POST"])
    def picture_upload() -> Any:
        cfg = current_config()
        uploads = [u for u in request.files.getlist("image") if u and u.filename]
        if not uploads:
            flash("Bitte mindestens eine Bilddatei auswählen.", "error")
            return redirect(url_for("picture_form"))
        store = picture_store(cfg)
        store.migrate()
        stored = 0
        for upload in uploads:
            try:
                _, note = store.add(upload.stream.read(MAX_UPLOAD_BYTES + 1), upload.filename)
            except PictureError as exc:
                flash("%s: %s" % (os.path.basename(upload.filename), exc), "error")
                continue
            except OSError as exc:
                log.exception("could not store the picture")
                flash("%s konnte nicht gespeichert werden: %s" % (os.path.basename(upload.filename), exc), "error")
                continue
            stored += 1
            if note:
                flash("%s: %s" % (os.path.basename(upload.filename), note), "warning")
        if stored:
            hint = "" if cfg.picture["enabled"] else " Zum Anzeigen unten „Bilder aktiv“ ankreuzen und speichern."
            flash("%d Bild%s gespeichert.%s" % (stored, "" if stored == 1 else "er", hint), "ok")
        return redirect(url_for("picture_form"))

    @app.route("/picture/item/<image_id>/delete", methods=["POST"])
    def picture_item_delete(image_id: str) -> Any:
        if valid_picture_id(image_id) and picture_store(current_config()).remove(image_id):
            flash("Bild entfernt.", "ok")
        return redirect(url_for("picture_form"))

    @app.route("/picture/item/<image_id>/move", methods=["POST"])
    def picture_item_move(image_id: str) -> Any:
        if valid_picture_id(image_id):
            picture_store(current_config()).move(image_id, -1 if request.form.get("direction") == "up" else 1)
        return redirect(url_for("picture_form"))

    @app.route("/picture/item/<image_id>.png")
    def picture_item_file(image_id: str) -> Any:
        path = picture_store(current_config()).path(image_id)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/png")

    @app.route("/picture/delete", methods=["POST"])
    def picture_delete() -> Any:
        cfg = current_config()
        if request.form.get("confirm") != "yes":
            flash("Bitte die Sicherheitsabfrage ankreuzen.", "error")
        elif picture_store(cfg).clear():
            flash("Alle Bilder entfernt.", "ok")
        return redirect(url_for("picture_form"))

    @app.route("/picture/preview.gif")
    def picture_preview() -> Any:
        cfg = current_config()
        settings = dict(cfg.picture)
        if "fit" in request.args:
            new_raw, errors = apply_picture_form({}, dict({"seconds": "10"}, **request.args.to_dict()), True)
            if errors:
                abort(400)
            settings = new_raw["picture"]
        try:
            data = picture_renderer(cfg).preview_gif(settings)
        except PictureError:
            abort(404)
        except Exception:  # noqa: BLE001
            log.exception("picture preview failed")
            abort(500)
        return send_file(io.BytesIO(data), mimetype="image/gif")

    # -- satellites ---------------------------------------------------------------

    def satellites_file(cfg: Config) -> str:
        return satellites_path(resolve(cfg.data_dir, root))

    def satellites_page(cfg: Config, sat: Dict[str, Any], status: int = 200) -> Any:
        now = now_func()
        readings = read_satellites(satellites_file(cfg))
        rows = []
        items = list(sat["items"])
        for number in range(SATELLITE_MAX):
            item = items[number] if number < len(items) else dict(DEFAULT_SATELLITE_ITEM, enabled=True)
            value = readings.get(item.get("key", ""))
            fresh = value if value is not None and value.is_fresh(now, SAT_MAX_AGE) else None
            age = None if value is None or value.updated_at is None else int((now - value.updated_at).total_seconds())
            quality = air_quality(fresh, item) if fresh is not None else None
            rows.append(dict(number=number + 1, item=item, reading=value, fresh=fresh is not None, age=age,
                             quality=quality))
        return render_template("satellites.html", cfg=cfg, sat=sat, rows=rows, host=request.host,
                               default_topic=lambda key: "%s/%s/state" % (sat["mqtt"]["base_topic"], key or "<key>")), status

    @app.route("/satellites", methods=["GET"])
    def satellites_form() -> Any:
        cfg = current_config()
        return satellites_page(cfg, cfg.satellites)

    @app.route("/satellites", methods=["POST"])
    def satellites_save() -> Any:
        cfg = current_config()
        new_raw, errors = apply_satellites_form(load_raw(config_path), request.form)
        if errors:
            for text in errors:
                flash(text, "error")
            return satellites_page(cfg, cfg.satellites, 400)
        write_raw(config_path, new_raw)
        flash("Satelliten gespeichert. Anzeige und MQTT-Dienst übernehmen die Einstellungen innerhalb weniger Sekunden.", "ok")
        return redirect(url_for("satellites_form"))

    @app.route("/api/satellite/<key>", methods=["POST"])
    def satellite_push(key: str) -> Any:
        cfg = current_config()
        sat = cfg.satellites
        entry = next((item for item in sat["items"] if item["key"] == key), None)
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("X-Token", "")
        if (not sat["enabled"] or entry is None or not entry["enabled"] or not entry["token"]
                or not hmac.compare_digest(token.encode("utf-8"), entry["token"].encode("utf-8"))):
            return jsonify(ok=False, error="Nicht erlaubt."), 401
        try:
            reading = parse_payload(request.get_json(silent=True, force=True), now_func())
        except PayloadError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        store_reading(satellites_file(cfg), key, reading, keep={item["key"] for item in sat["items"]})
        return jsonify(ok=True)

    # -- camera -----------------------------------------------------------------

    PER_PAGE = 24

    def mb_text(size: int) -> str:
        return ("%.1f" % (size / MB)).replace(".", ",")

    def camera_page_context(cfg: Config, section: str) -> Dict[str, Any]:
        photos = gallery.list()
        return dict(
            cfg=cfg, section=section, photo_count=len(photos),
            usage=mb_text(sum(p.size for p in photos)), limit=cfg.camera["gallery_limit_mb"],
        )

    def image_options(cfg: Config) -> ImageOptions:
        c = cfg.camera
        return ImageOptions(hflip=c["hflip"], vflip=c["vflip"], brightness=c["brightness"] / 100.0,
                            sharpness=float(c["sharpness"]))

    def image_context(cfg: Config) -> Dict[str, Any]:
        c = cfg.camera
        return dict(
            hflip=c["hflip"], vflip=c["vflip"],
            brightness_options=[(v, ("%+d %%" % v) if v else "0 % (Standard)") for v in CAMERA_BRIGHTNESS],
            brightness=c["brightness"],
            sharpness_options=[("%g" % v, "%g%s" % (v, " (Standard)" if v == 1.0 else " (aus)" if v == 0.0 else ""))
                               for v in CAMERA_SHARPNESS],
            sharpness="%g" % c["sharpness"])

    def motion_photo(data: bytes) -> str:
        """Called by the live view when it sees movement: store the frame in the gallery."""
        cfg = current_config()
        when = now_func().astimezone(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
        return gallery.save(data, when, cfg.camera["gallery_limit_mb"])

    def camera_off() -> Any:
        return render_template("camera_off.html")

    def camera_enabled_or_404() -> Config:
        cfg = current_config()
        if not cfg.camera["enabled"]:
            abort(404)
        return cfg

    def save_camera_form(section: str) -> bool:
        """Store a camera page form in the configuration. False (with messages) if the input was invalid."""
        new_raw, errors = apply_camera_form(load_raw(config_path), request.form, section)
        if errors:
            for message in errors:
                flash(message, "error")
            return False
        write_raw(config_path, new_raw)
        return True

    @app.route("/camera")
    def camera_page() -> Any:
        cfg = current_config()
        if not cfg.camera["enabled"]:
            return camera_off()
        found, text = camera.status()
        latest = gallery.list()[:1]
        return render_template(
            "camera.html", found=found, status_text=text,
            sizes=[(size_text(w, h), "%d × %d" % (w, h)) for w, h in CAMERA_PHOTO_SIZES],
            size=size_text(cfg.camera["width"], cfg.camera["height"]),
            latest=latest[0] if latest else None, image_back="photo", image_run=False,
            **image_context(cfg), **camera_page_context(cfg, "photo"))

    @app.route("/camera/photo", methods=["POST"])
    def camera_photo() -> Any:
        cfg = camera_enabled_or_404()
        source = request.form.get("source", "still")
        target = "camera_live_page" if source == "live" else "camera_page"
        extra = {"run": "1"} if source == "live" else {}
        if source == "live":
            if not camera.live_active:
                flash("Die Live-Ansicht läuft nicht. Bitte erst starten.", "error")
                return redirect(url_for(target))
        elif "photo_size" in request.form:
            if not save_camera_form("photo"):
                return redirect(url_for(target))
            cfg = current_config()
        try:
            data = camera.capture(cfg.camera["width"], cfg.camera["height"], options=image_options(cfg))
            name = gallery.save(data, now_func().astimezone(ZoneInfo(cfg.timezone)).replace(tzinfo=None),
                                cfg.camera["gallery_limit_mb"])
        except (CameraError, GalleryError) as exc:
            log.warning("photo failed: %s", exc)
            flash(str(exc), "error")
            return redirect(url_for(target, **extra))
        flash("Foto gespeichert: %s" % name, "ok")
        return redirect(url_for(target, **extra))

    @app.route("/camera/live")
    def camera_live_page() -> Any:
        cfg = current_config()
        if not cfg.camera["enabled"]:
            return camera_off()
        running = request.args.get("run") == "1"
        return render_template(
            "camera_live.html", running=running,
            sizes=[(size_text(w, h), "%d × %d" % (w, h)) for w, h in CAMERA_LIVE_SIZES],
            size=size_text(cfg.camera["live_width"], cfg.camera["live_height"]), fps_options=CAMERA_LIVE_FPS,
            fps=cfg.camera["live_fps"], image_back="live", image_run=running,
            motion_enabled=cfg.camera["motion_enabled"], motion_level=cfg.camera["motion_sensitivity"],
            motion_levels=sorted(SENSITIVITY_LABELS.items()), motion_pause=cfg.camera["motion_pause"],
            motion_pauses=[(v, pause_label(v)) for v in PAUSE_OPTIONS],
            **image_context(cfg), **camera_page_context(cfg, "live"))

    @app.route("/camera/image/settings", methods=["POST"])
    def camera_image_settings() -> Any:
        camera_enabled_or_404()
        if save_camera_form("image"):
            flash("Bild-Einstellungen gespeichert.", "ok")
        if request.form.get("back") == "live":
            return redirect(url_for("camera_live_page", run="1" if request.form.get("run") == "1" else None))
        return redirect(url_for("camera_page"))

    @app.route("/camera/live/motion/settings", methods=["POST"])
    def camera_motion_settings() -> Any:
        camera_enabled_or_404()
        if save_camera_form("motion"):
            flash("Bewegungsmelder gespeichert.", "ok")
        return redirect(url_for("camera_live_page", run="1" if request.form.get("run") == "1" else None))

    @app.route("/camera/live/motion")
    def camera_motion_status() -> Any:
        cfg = camera_enabled_or_404()
        status = camera.motion_status()
        return render_template("camera_motion.html", status=status, enabled=cfg.camera["motion_enabled"],
                               pause=pause_label(cfg.camera["motion_pause"]),
                               last_age=None if status.last_age is None else int(status.last_age),
                               changed=("%.1f" % status.changed).replace(".", ","),
                               threshold=("%.1f" % status.threshold).replace(".", ","), **camera_page_context(cfg, "live"))

    @app.route("/camera/live/settings", methods=["POST"])
    def camera_live_settings() -> Any:
        camera_enabled_or_404()
        if save_camera_form("live"):
            flash("Live-Einstellungen gespeichert.", "ok")
        return redirect(url_for("camera_live_page", run="1" if request.form.get("run") == "1" else None))

    @app.route("/camera/live.mjpg")
    def camera_live_stream() -> Any:
        cfg = camera_enabled_or_404()
        try:
            motion = (MotionSettings(cfg.camera["motion_sensitivity"], cfg.camera["motion_pause"])
                      if cfg.camera["motion_enabled"] else None)
            frames = camera.live_frames(cfg.camera["live_width"], cfg.camera["live_height"], cfg.camera["live_fps"],
                                        options=image_options(cfg), motion=motion, on_motion=motion_photo)
        except CameraError as exc:
            log.warning("live view failed: %s", exc)
            return str(exc), 503, {"Content-Type": "text/plain; charset=utf-8"}

        def generate() -> Any:
            try:
                for frame in frames:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame)
                           + frame + b"\r\n")
            finally:
                close = getattr(frames, "close", None)
                if close:
                    close()

        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/camera/gallery")
    def camera_gallery_page() -> Any:
        cfg = current_config()
        if not cfg.camera["enabled"]:
            return camera_off()
        photos = gallery.list()
        pages = max(1, (len(photos) + PER_PAGE - 1) // PER_PAGE)
        try:
            number = min(max(1, int(request.args.get("page", "1"))), pages)
        except ValueError:
            number = 1
        shown = [
            {"name": p.name, "taken": p.taken.strftime("%d.%m.%Y %H:%M:%S"), "size": mb_text(p.size)}
            for p in photos[(number - 1) * PER_PAGE:number * PER_PAGE]
        ]
        return render_template("camera_gallery.html", photos=shown, page=number, pages=pages,
                               **camera_page_context(cfg, "gallery"))

    @app.route("/camera/gallery/limit", methods=["POST"])
    def camera_gallery_limit() -> Any:
        camera_enabled_or_404()
        if save_camera_form("gallery"):
            deleted = gallery.enforce_limit(current_config().camera["gallery_limit_mb"])
            flash("Speichergrenze gespeichert." + (" %d älteste Fotos gelöscht." % len(deleted) if deleted else ""), "ok")
        return redirect(url_for("camera_gallery_page"))

    @app.route("/camera/gallery/clear", methods=["POST"])
    def camera_gallery_clear() -> Any:
        camera_enabled_or_404()
        if request.form.get("confirm") != "yes":
            flash("Bitte die Sicherheitsabfrage ankreuzen.", "error")
        else:
            flash("%d Fotos gelöscht." % gallery.clear(), "ok")
        return redirect(url_for("camera_gallery_page"))

    @app.route("/camera/view/<name>")
    def camera_view_page(name: str) -> Any:
        cfg = camera_enabled_or_404()
        if gallery.path(name) is None:
            abort(404)
        names = [p.name for p in gallery.list()]          # newest first
        index = names.index(name)
        taken = next(p for p in gallery.list() if p.name == name)
        return render_template(
            "camera_view.html", name=name, taken=taken.taken.strftime("%d.%m.%Y %H:%M:%S"), size=mb_text(taken.size),
            newer=names[index - 1] if index > 0 else None,
            older=names[index + 1] if index + 1 < len(names) else None,
            **camera_page_context(cfg, "gallery"))

    @app.route("/camera/photo/<name>")
    def camera_photo_file(name: str) -> Any:
        camera_enabled_or_404()
        path = gallery.path(name) if valid_name(name) else None
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", as_attachment=request.args.get("dl") == "1", download_name=name)

    @app.route("/camera/thumb/<name>")
    def camera_thumb(name: str) -> Any:
        camera_enabled_or_404()
        try:
            data = gallery.thumbnail(name)
        except GalleryError:
            abort(404)
        if data is None:
            abort(404)
        return send_file(io.BytesIO(data), mimetype="image/jpeg")

    @app.route("/camera/photo/<name>/delete", methods=["POST"])
    def camera_photo_delete(name: str) -> Any:
        camera_enabled_or_404()
        if request.form.get("confirm") != "yes":
            flash("Bitte die Sicherheitsabfrage ankreuzen.", "error")
            return redirect(url_for("camera_view_page", name=name))
        if not gallery.delete(name):
            abort(404)
        flash("Foto gelöscht.", "ok")
        return redirect(url_for("camera_gallery_page"))

    # -- password ---------------------------------------------------------------

    @app.route("/password", methods=["GET", "POST"])
    def password() -> Any:
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            old = request.form.get("old_password", "")
            new = request.form.get("new_password", "")
            again = request.form.get("new_password2", "")
            problem = None
            if not auth.check(auth.username, old):
                problem = "Das aktuelle Kennwort ist falsch."
            elif not username or len(username) > 64:
                problem = "Bitte einen Benutzernamen (höchstens 64 Zeichen) eingeben."
            elif new != again:
                problem = "Die beiden neuen Kennwörter stimmen nicht überein."
            else:
                problem = password_problem(new)
            if problem:
                flash(problem, "error")
                return render_template("password.html", username=username or auth.username), 400
            auth.set_credentials(username, new)
            session["user"] = username
            flash("Zugangsdaten geändert.", "ok")
            return redirect(url_for("dashboard"))
        return render_template("password.html", username=auth.username)

    # -- system -----------------------------------------------------------------

    @app.route("/system")
    def system_page() -> Any:
        cfg = current_config()
        record = read_safety(safety_path(resolve(cfg.data_dir, root)))
        protection = {
            "enabled": bool(cfg.safety["enabled"]),
            "limit": cfg.safety["shutdown_temp_c"],
            "last": None if record is None or record.shutdown_at is None else {
                "when": fmt_time(record.shutdown_at, cfg.timezone),
                "temperature": ("%.1f" % record.temperature_c).replace(".", ",") if record.temperature_c is not None else "?",
                "limit": record.limit_c,
            },
        }
        return render_template(
            "system.html", info=system.info(), services=system.service_states(), services_order=SERVICES,
            cfg=cfg, protection=protection,
        )

    @app.route("/system/restart/<name>", methods=["POST"])
    def system_restart(name: str) -> Any:
        if name not in SERVICES:
            abort(404)
        ok, message = system.restart_service(name)
        flash(message, "ok" if ok else "error")
        return redirect(url_for("system_page"))

    def confirmed() -> bool:
        if request.form.get("confirm") != "yes":
            flash("Bitte die Sicherheitsabfrage ankreuzen.", "error")
            return False
        return True

    @app.route("/system/reboot", methods=["POST"])
    def system_reboot() -> Any:
        if confirmed():
            ok, message = system.reboot()
            flash(message, "ok" if ok else "error")
        return redirect(url_for("system_page"))

    @app.route("/system/shutdown", methods=["POST"])
    def system_shutdown() -> Any:
        if confirmed():
            ok, message = system.shutdown()
            flash(message, "ok" if ok else "error")
        return redirect(url_for("system_page"))

    @app.route("/system/hostname", methods=["POST"])
    def system_hostname() -> Any:
        ok, message = system.set_hostname(request.form.get("hostname", "").strip().lower())
        flash(message, "ok" if ok else "error")
        return redirect(url_for("system_page"))

    @app.route("/logs/<name>")
    def logs(name: str) -> Any:
        if name not in SERVICES:
            abort(404)
        return render_template("logs.html", name=name, text=system.logs(name))

    return app
