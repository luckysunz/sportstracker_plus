"""WLAN control through NetworkManager (``nmcli``) and the status file shared by the WLAN service and the web backend.

The WLAN service (``service_wifi.py``, root) is the only process that talks to ``nmcli``. The web backend only reads
``data/wifi.json`` (status, networks found) and writes ``data/wifi_request.json`` (new network + password), which the
service picks up. That keeps the web user free of any sudo rule for networking.

``Nmcli`` takes a runner ``(argv, timeout) -> (exit code, output)`` so tests need no NetworkManager.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .timeutil import parse_utc

log = logging.getLogger(__name__)

WIFI_VERSION = 1
HOTSPOT_CONNECTION = "SportstrackerSetup"
HOTSPOT_ADDRESS = "10.42.0.1"                 # NetworkManager's address for a shared (hotspot) connection
SETUP_PORT = 8080
FRESH_SECONDS = 90                            # status older than this is not trusted (service stopped)

Runner = Callable[[List[str], float], Tuple[int, str]]


class WifiError(Exception):
    """Something the user can act on (German text)."""


def wifi_path(data_dir: str) -> str:
    return os.path.join(data_dir, "wifi.json")


def request_path(data_dir: str) -> str:
    return os.path.join(data_dir, "wifi_request.json")


def setup_url() -> str:
    return "http://%s:%d/wifi/setup" % (HOTSPOT_ADDRESS, SETUP_PORT)


def qr_escape(value: str) -> str:
    """Escape for the ``WIFI:`` QR format (backslash, semicolon, comma, colon, quote)."""
    return "".join("\\" + ch if ch in '\\;,:"' else ch for ch in value)


def wifi_qr_text(ssid: str, password: str) -> str:
    return "WIFI:T:WPA;S:%s;P:%s;;" % (qr_escape(ssid), qr_escape(password))


def check_credentials(ssid: str, password: str) -> Optional[str]:
    """Problem text for a network entry the user typed, None if it is usable."""
    if not ssid or len(ssid.encode("utf-8")) > 32:
        return "Der Netzwerkname (SSID) muss 1 bis 32 Zeichen lang sein."
    if any(ord(ch) < 32 for ch in ssid):
        return "Der Netzwerkname enthält ungültige Zeichen."
    if password and not 8 <= len(password) <= 63:
        return "Das WLAN-Kennwort muss 8 bis 63 Zeichen lang sein (leer nur für ein offenes Netz)."
    if any(ord(ch) < 32 for ch in password):
        return "Das Kennwort enthält ungültige Zeichen."
    return None


# -- nmcli -------------------------------------------------------------------------------------------------------

def default_runner(argv: List[str], timeout: float) -> Tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "nmcli nicht gefunden (NetworkManager ist nicht installiert)."
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung."
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def split_terse(line: str) -> List[str]:
    """Split one line of ``nmcli -t`` output at unescaped colons and unescape ``\\:`` and ``\\\\``."""
    fields: List[str] = []
    current: List[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


@dataclass
class Network:
    ssid: str
    signal: int
    secured: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"ssid": self.ssid, "signal": self.signal, "secured": self.secured}


@dataclass
class Link:
    device: str = ""
    state: str = ""          # "connected", "disconnected", "connecting", ...
    connection: str = ""     # name of the active profile

    @property
    def connected(self) -> bool:
        return self.state.startswith("connected")


class Nmcli:
    def __init__(self, runner: Runner = default_runner) -> None:
        self.runner = runner

    def _run(self, args: List[str], timeout: float = 20.0) -> Tuple[int, str]:
        code, out = self.runner(["nmcli"] + args, timeout)
        return code, out.strip()

    # status
    def link(self) -> Optional[Link]:
        """State of the first WLAN device, None if there is none (or no NetworkManager)."""
        code, out = self._run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        if code != 0:
            return None
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 4 and fields[1] == "wifi":
                return Link(fields[0], fields[2], fields[3])
        return None

    def ssid(self, device: str) -> str:
        """Name of the network the device is joined to (the profile name can differ, e.g. ``preconfigured``)."""
        code, out = self._run(["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "ifname", device, "--rescan", "no"])
        if code != 0:
            return ""
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 2 and fields[0] == "yes":
                return fields[1]
        return ""

    def ip_address(self, device: str) -> str:
        code, out = self._run(["-t", "-g", "IP4.ADDRESS", "device", "show", device])
        if code != 0 or not out:
            return ""
        return split_terse(out.splitlines()[0])[0].split("/")[0]

    def scan(self, device: str) -> List[Network]:
        code, out = self._run(["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", device,
                               "--rescan", "yes"], 40.0)
        if code != 0:
            return []
        best: Dict[str, Network] = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 3 or not fields[0] or fields[0] == "--":
                continue
            try:
                strength = int(fields[1])
            except ValueError:
                strength = 0
            network = Network(fields[0], strength, bool(fields[2].strip()) and fields[2].strip() != "--")
            if fields[0] not in best or best[fields[0]].signal < strength:
                best[fields[0]] = network
        return sorted(best.values(), key=lambda n: -n.signal)

    def profiles(self) -> Dict[str, str]:
        """Saved WLAN profiles: name -> SSID (the hotspot profile is left out)."""
        code, out = self._run(["-t", "-f", "NAME,TYPE", "connection", "show"])
        if code != 0:
            return {}
        result: Dict[str, str] = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 2 or fields[1] != "802-11-wireless" or fields[0] == HOTSPOT_CONNECTION:
                continue
            code2, ssid = self._run(["-t", "-g", "802-11-wireless.ssid", "connection", "show", "id", fields[0]])
            result[fields[0]] = split_terse(ssid)[0] if code2 == 0 and ssid else fields[0]   # -g output is escaped too
        return result

    # actions
    def start_hotspot(self, device: str, ssid: str, password: str) -> Tuple[bool, str]:
        self._run(["connection", "delete", "id", HOTSPOT_CONNECTION])
        code, out = self._run(["device", "wifi", "hotspot", "ifname", device, "con-name", HOTSPOT_CONNECTION,
                               "ssid", ssid, "password", password], 40.0)
        if code != 0:
            return False, out
        self._run(["connection", "modify", "id", HOTSPOT_CONNECTION, "connection.autoconnect", "no"])
        return True, ""

    def stop_hotspot(self) -> None:
        self._run(["connection", "down", "id", HOTSPOT_CONNECTION])

    def connect(self, device: str, ssid: str, password: str, hidden: bool = False) -> Tuple[bool, str]:
        """Join a network: update the saved profile of that SSID or create one. Returns (ok, message)."""
        problem = check_credentials(ssid, password)
        if problem:
            return False, problem
        existing = [name for name, known in self.profiles().items() if known == ssid]
        if existing:
            name = existing[0]
            if password:
                code, out = self._run(["connection", "modify", "id", name, "wifi-sec.key-mgmt", "wpa-psk",
                                       "wifi-sec.psk", password])
                if code != 0:
                    return False, out
            code, out = self._run(["connection", "up", "id", name, "ifname", device], 60.0)
        else:
            args = ["device", "wifi", "connect", ssid, "ifname", device]
            if password:
                args += ["password", password]
            if hidden:
                args += ["hidden", "yes"]
            code, out = self._run(args, 60.0)
            name = ssid
        if code != 0:
            return False, _friendly(out)
        self._run(["connection", "modify", "id", name, "connection.autoconnect", "yes"])
        return True, ""

    def reconnect(self, name: str, device: str) -> bool:
        code, _out = self._run(["connection", "up", "id", name, "ifname", device], 60.0)
        return code == 0


def _friendly(output: str) -> str:
    text = output.lower()
    if "secrets were required" in text or "wrong password" in text or "802.1x" in text:
        return "Das Kennwort wurde nicht akzeptiert."
    if "no network with ssid" in text or "not found" in text:
        return "Das Netzwerk wurde nicht gefunden."
    if "timeout" in text or "zeitüberschreitung" in text:
        return "Die Verbindung kam nicht rechtzeitig zustande (Kennwort oder Empfang prüfen)."
    return "Die Verbindung ist fehlgeschlagen: %s" % (output.splitlines()[-1] if output else "unbekannter Fehler")


# -- status and request files ------------------------------------------------------------------------------------

def _atomic_write(path: str, data: Dict[str, Any], mode: int) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".wifi-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_status(path: str, status: Dict[str, Any], now: datetime) -> None:
    status = dict(status)
    status["version"] = WIFI_VERSION
    status["updated_at"] = now.astimezone(timezone.utc).isoformat()
    _atomic_write(path, status, 0o644)


def read_status(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != WIFI_VERSION:
        return None
    return data


def is_fresh(status: Optional[Dict[str, Any]], now: datetime) -> bool:
    if not status or not status.get("updated_at"):
        return False
    try:
        age = (now - parse_utc(status["updated_at"])).total_seconds()
    except (ValueError, TypeError):
        return False
    return -5 <= age <= FRESH_SECONDS


def hotspot_active(status: Optional[Dict[str, Any]], now: datetime) -> bool:
    return bool(status) and status.get("mode") == "hotspot" and is_fresh(status, now)


def write_request(path: str, ssid: str, password: str, hidden: bool, now: datetime) -> None:
    """Called by the web backend (user pi). The file is private to that user; the service (root) reads and deletes it."""
    _atomic_write(path, {"ssid": ssid, "password": password, "hidden": bool(hidden),
                         "requested_at": now.astimezone(timezone.utc).isoformat()}, 0o600)


def take_request(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        data = None
    try:
        os.unlink(path)
    except OSError:
        pass
    if not isinstance(data, dict) or not isinstance(data.get("ssid"), str) or not isinstance(data.get("password", ""), str):
        return None
    return data
