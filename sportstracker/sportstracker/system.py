"""Raspberry Pi system functions for the web backend (status, restart, reboot, shutdown, time zone, host name).

Everything that needs root goes through ``sudo -n`` with exactly the commands
that ``deploy/sportstracker-sudoers`` allows. All external commands run
through an injectable ``runner`` so the tests never touch the real system.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, available_timezones

SERVICES = ("sportstracker-data", "sportstracker-display", "sportstracker-sensor", "sportstracker-web", "sportstracker-safety",
            "sportstracker-mqtt", "sportstracker-wifi")
SERVICE_LABELS = {
    "sportstracker-data": "Datendienst",
    "sportstracker-display": "Anzeigedienst",
    "sportstracker-sensor": "Sensordienst",
    "sportstracker-web": "Webdienst",
    "sportstracker-safety": "Sicherheitsdienst",
    "sportstracker-mqtt": "MQTT-Dienst (Satelliten)",
    "sportstracker-wifi": "WLAN-Dienst (Setup-Hotspot)",
}
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
SYSTEMCTL = "/usr/bin/systemctl"
TIMEDATECTL = "/usr/bin/timedatectl"
HOSTNAMECTL = "/usr/bin/hostnamectl"

Runner = Callable[..., Tuple[int, str]]


def default_runner(cmd: List[str], wait: bool = True, timeout: float = 20.0) -> Tuple[int, str]:
    """Run a command. ``wait=False`` starts it detached (for actions that stop this very process)."""
    try:
        if not wait:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return 0, ""
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)
        return done.returncode, done.stdout or ""
    except FileNotFoundError:
        return 127, "Befehl nicht gefunden: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung bei %s" % " ".join(cmd)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def timezone_names() -> List[str]:
    return sorted(available_timezones())


def valid_timezone(name: str) -> bool:
    if not name or name.startswith("/") or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, OSError
        return False
    return True


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append("%d Tg." % days)
    if days or hours:
        parts.append("%d Std." % hours)
    parts.append("%d Min." % minutes)
    return " ".join(parts)


class SystemControl:
    def __init__(self, runner: Runner = default_runner, data_path: str = "/") -> None:
        self.runner = runner
        self.data_path = data_path

    # -- status -----------------------------------------------------------------

    def info(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {"hostname": socket.gethostname()}
        uptime = _read("/proc/uptime")
        if uptime:
            try:
                info["uptime"] = format_duration(float(uptime.split()[0]))
            except (ValueError, IndexError):
                pass
        try:
            load = os.getloadavg()
            info["load"] = "%.2f  %.2f  %.2f" % load
        except (OSError, AttributeError):
            pass
        meminfo = _read("/proc/meminfo")
        if meminfo:
            values = {}
            for line in meminfo.splitlines():
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0])
            if "MemTotal" in values and "MemAvailable" in values:
                info["memory"] = "%d MB frei von %d MB" % (values["MemAvailable"] // 1024, values["MemTotal"] // 1024)
        temp = _read("/sys/class/thermal/thermal_zone0/temp")
        if temp and temp.strip().lstrip("-").isdigit():
            info["cpu_temp"] = "%.1f °C" % (int(temp.strip()) / 1000.0)
        try:
            usage = shutil.disk_usage(self.data_path)
            info["disk"] = "%.1f GB frei von %.1f GB" % (usage.free / 1e9, usage.total / 1e9)
        except OSError:
            pass
        release = _read("/etc/os-release")
        if release:
            match = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', release, re.M)
            if match:
                info["os"] = match.group(1)
        model = _read("/proc/device-tree/model")
        if model:
            info["model"] = model.strip("\x00\n ")
        code, out = self.runner(["hostname", "-I"])
        if code == 0 and out.strip():
            info["addresses"] = out.split()
        code, out = self.runner(["timedatectl", "show", "-p", "Timezone", "--value"])
        if code == 0 and out.strip():
            info["system_timezone"] = out.strip()
        return info

    def service_states(self) -> Dict[str, str]:
        states = {}
        for name in SERVICES:
            code, out = self.runner(["systemctl", "is-active", name])
            states[name] = out.strip() or ("unbekannt" if code else "active")
        return states

    def logs(self, name: str, lines: int = 120) -> str:
        if name not in SERVICES:
            return ""
        code, out = self.runner(["journalctl", "-u", name, "-n", str(int(lines)), "--no-pager", "-o", "short-iso"])
        if code != 0 and not out:
            return "Protokoll nicht lesbar (Code %d)." % code
        return out[-30000:]

    # -- actions ----------------------------------------------------------------

    def restart_service(self, name: str) -> Tuple[bool, str]:
        if name not in SERVICES:
            return False, "Unbekannter Dienst."
        if name == "sportstracker-web":
            # This restarts the very process that answers the request, so do not wait for it.
            self.runner(["sudo", "-n", SYSTEMCTL, "restart", name], wait=False)
            return True, "Der Webdienst startet neu. Die Seite in einigen Sekunden neu laden."
        code, out = self.runner(["sudo", "-n", SYSTEMCTL, "restart", name])
        return code == 0, "%s neu gestartet." % SERVICE_LABELS[name] if code == 0 else self._failure(out, code)

    def reboot(self) -> Tuple[bool, str]:
        code, out = self.runner(["sudo", "-n", SYSTEMCTL, "reboot"], wait=False)
        return code == 0, "Der Raspberry Pi startet neu." if code == 0 else self._failure(out, code)

    def shutdown(self) -> Tuple[bool, str]:
        code, out = self.runner(["sudo", "-n", SYSTEMCTL, "poweroff"], wait=False)
        return code == 0, "Der Raspberry Pi fährt herunter. Danach kann die Stromversorgung getrennt werden." if code == 0 else self._failure(out, code)

    def set_timezone(self, name: str) -> Tuple[bool, str]:
        if not valid_timezone(name):
            return False, "Unbekannte Zeitzone."
        code, out = self.runner(["sudo", "-n", TIMEDATECTL, "set-timezone", name])
        return code == 0, "Systemzeitzone gesetzt: %s." % name if code == 0 else self._failure(out, code)

    def set_hostname(self, name: str) -> Tuple[bool, str]:
        if not HOSTNAME_RE.match(name):
            return False, "Ungültiger Hostname (erlaubt: Kleinbuchstaben, Ziffern und Bindestrich, höchstens 32 Zeichen)."
        code, out = self.runner(["sudo", "-n", HOSTNAMECTL, "set-hostname", name])
        return code == 0, "Hostname gesetzt: %s (gilt vollständig nach einem Neustart)." % name if code == 0 else self._failure(out, code)

    @staticmethod
    def _failure(out: str, code: int) -> str:
        detail = out.strip().splitlines()[-1] if out.strip() else "Code %d" % code
        return "Fehlgeschlagen: %s" % detail
