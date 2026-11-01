"""WLAN service: keeps the Pi reachable and offers a setup hotspot when the known WLAN is gone (0.7.0).

    sudo python -m sportstracker.service_wifi --config config/config.json

Runs as root (it controls NetworkManager through ``nmcli``). Every ``--interval`` seconds it
  * reads a request of the web backend (``data/wifi_request.json``: new network and password) and applies it,
  * checks the WLAN: after ``wifi.grace_seconds`` without a connection it starts the hotspot (default
    ``Sportstracker-Setup``) so new credentials can be entered with a phone; the display shows QR codes and the address,
  * leaves the hotspot again when the user entered a network, or - if nobody is connected to it - every
    ``RETRY_SECONDS`` to look whether the old WLAN is back,
  * writes ``data/wifi.json`` (mode, address, networks found before the hotspot went up, result of the last request).

Single radio: the hotspot and the normal WLAN cannot run at the same time on a Pi Zero 2 W.
"""

from __future__ import annotations

import argparse
import copy
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .config import DEFAULT_WIFI, Config, load_config
from .errors import ConfigError
from .runtime import PROJECT_ROOT, resolve
from .wifi import (HOTSPOT_ADDRESS, HOTSPOT_CONNECTION, Link, Nmcli, request_path, setup_url, take_request, wifi_path,
                   write_status)

log = logging.getLogger("sportstracker.wifi")

RETRY_SECONDS = 600            # in hotspot mode: look for the old WLAN again at this interval (if nobody is connected)
SCAN_SECONDS = 600             # refresh the list of networks this often while connected
WAIT_FOR_LINK_SECONDS = 30


class WifiService:
    def __init__(
        self,
        config_path: str,
        nm: Optional[Nmcli] = None,
        root: str = PROJECT_ROOT,
        now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
        clients_func: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.config_path = config_path
        self.root = root
        self.nm = nm or Nmcli()
        self._now = now_func
        self._sleep = sleeper
        self._clients = clients_func or self._count_clients
        self._stop = threading.Event()
        self.mode = "offline"
        self.offline_since: Optional[float] = None
        self.hotspot_since: Optional[float] = None
        self.last_connection = ""
        self.last_result: Optional[Dict[str, Any]] = None
        self.networks: List[Dict[str, Any]] = []
        self.networks_at: Optional[float] = None
        self.device = "wlan0"
        self.hotspot_info: Optional[Dict[str, Any]] = None

    # -- configuration ----------------------------------------------------------

    def settings(self) -> Dict[str, Any]:
        try:
            return load_config(self.config_path).wifi
        except ConfigError as exc:
            log.error("configuration problem: %s", exc)
            return copy.deepcopy(DEFAULT_WIFI)

    def data_dir(self) -> str:
        try:
            return resolve(load_config(self.config_path).data_dir, self.root)
        except ConfigError:
            return resolve(Config().data_dir, self.root)

    # -- helpers ----------------------------------------------------------------

    def _count_clients(self, device: str) -> int:
        """Devices connected to the hotspot, counted from the neighbour table (10.42.0.x)."""
        code, out = self.nm.runner(["ip", "-4", "neigh", "show", "dev", device], 5.0)
        if code != 0:
            return 0
        return sum(1 for line in out.splitlines()
                   if line.startswith("10.42.0.") and not line.rstrip().endswith(("FAILED", "INCOMPLETE")))

    def _result(self, ok: bool, message: str, now: datetime) -> None:
        self.last_result = {"ok": ok, "message": message, "at": now.astimezone(timezone.utc).isoformat()}
        (log.info if ok else log.warning)("WLAN: %s", message)

    def _scan(self, now: float) -> None:
        found = self.nm.scan(self.device)
        if found:
            self.networks = [n.as_dict() for n in found]
            self.networks_at = now

    def _start_hotspot(self, cfg: Dict[str, Any], now: datetime) -> bool:
        self._scan(now.timestamp())                 # the radio cannot scan while it is an access point
        ok, message = self.nm.start_hotspot(self.device, cfg["hotspot_ssid"], cfg["hotspot_password"])
        if not ok:
            self._result(False, "Hotspot konnte nicht gestartet werden: %s" % message, now)
            self.offline_since = now.timestamp()     # try again after another grace period
            return False
        self.mode = "hotspot"
        self.hotspot_since = now.timestamp()
        self.offline_since = None
        self.hotspot_info = {"ssid": cfg["hotspot_ssid"], "password": cfg["hotspot_password"], "url": setup_url(),
                             "address": HOTSPOT_ADDRESS}
        log.info("hotspot '%s' started", cfg["hotspot_ssid"])
        return True

    def _wait_connected(self) -> Optional[Link]:
        for _ in range(WAIT_FOR_LINK_SECONDS):
            link = self.nm.link()
            if link is not None and link.connected and link.connection != HOTSPOT_CONNECTION:
                return link
            self._sleep(1.0)
        return None

    # -- requests ---------------------------------------------------------------

    def handle_request(self, request: Dict[str, Any], link: Link, cfg: Dict[str, Any], now: datetime) -> None:
        ssid = request["ssid"]
        was_hotspot = link.connection == HOTSPOT_CONNECTION
        previous = self.last_connection
        log.info("request: connect to '%s'", ssid)
        if was_hotspot:
            self.nm.stop_hotspot()
            self._sleep(2.0)
            self.hotspot_info = None
        ok, message = self.nm.connect(self.device, ssid, request.get("password", ""), bool(request.get("hidden")))
        if ok:
            self.mode = "client"
            self.offline_since = None
            self.hotspot_since = None
            self.last_connection = ssid
            self._result(True, "Verbunden mit „%s“." % ssid, now)
            return
        self._result(False, "„%s“: %s" % (ssid, message), now)
        if was_hotspot:
            if cfg.get("enabled", True):
                self._start_hotspot(cfg, now)
        elif previous:
            self.nm.reconnect(previous, self.device)

    def _retry_known_networks(self, cfg: Dict[str, Any], now: datetime) -> None:
        log.info("hotspot idle: looking for a known WLAN")
        self.nm.stop_hotspot()
        self.hotspot_info = None
        self._sleep(2.0)
        link = self._wait_connected()
        if link is not None:
            self.mode = "client"
            self.hotspot_since = None
            self.offline_since = None
            self.last_connection = link.connection
            self._result(True, "Das bekannte WLAN „%s“ ist wieder da." % link.connection, now)
        else:
            self._start_hotspot(cfg, self._now())

    # -- one step ---------------------------------------------------------------

    def step(self) -> Dict[str, Any]:
        cfg = self.settings()
        now = self._now()
        stamp = now.timestamp()
        link = self.nm.link()
        status: Dict[str, Any] = {"enabled": bool(cfg["enabled"])}
        if link is None:
            self.mode = "unavailable"
            status.update(mode="unavailable", networks=self.networks, last_result=self.last_result)
            write_status(wifi_path(self.data_dir()), status, now)
            return status
        self.device = link.device
        request = take_request(request_path(self.data_dir()))
        if request is not None:
            self.handle_request(request, link, cfg, now)
            link = self.nm.link() or link
        in_hotspot = link.connection == HOTSPOT_CONNECTION and link.connected
        if in_hotspot:
            self.mode = "hotspot"
            if self.hotspot_since is None:
                self.hotspot_since = stamp
            if self.hotspot_info is None:
                self.hotspot_info = {"ssid": cfg["hotspot_ssid"], "password": cfg["hotspot_password"],
                                     "url": setup_url(), "address": HOTSPOT_ADDRESS}
            if not cfg["enabled"]:
                self.nm.stop_hotspot()
                self.hotspot_info = None
            elif stamp - self.hotspot_since >= RETRY_SECONDS and self._clients(self.device) == 0:
                self._retry_known_networks(cfg, now)
                link = self.nm.link() or link
        elif link.connected:
            self.mode = "client"
            self.offline_since = None
            self.hotspot_since = None
            self.hotspot_info = None
            self.last_connection = link.connection
            if self.networks_at is None or stamp - self.networks_at >= SCAN_SECONDS:
                self._scan(stamp)
        else:
            self.mode = "offline"
            self.hotspot_info = None
            if self.offline_since is None:
                self.offline_since = stamp
            elif cfg["enabled"] and stamp - self.offline_since >= cfg["grace_seconds"]:
                self._start_hotspot(cfg, now)
        status.update(mode=self.mode, device=self.device, connection=link.connection if link.connected else "",
                      ip=self.nm.ip_address(self.device) if link.connected else "", networks=self.networks,
                      last_result=self.last_result)
        if self.mode == "hotspot" and self.hotspot_info is not None:
            status["hotspot"] = self.hotspot_info
        if self.mode == "offline" and self.offline_since is not None:
            status["offline_seconds"] = int(stamp - self.offline_since)
        write_status(wifi_path(self.data_dir()), status, now)
        return status

    # -- loop -------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, interval: float = 10.0) -> int:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 - keep the service alive
                log.exception("unexpected error")
            self._stop.wait(interval)
        return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_wifi")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = WifiService(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())
    return service.run_forever(args.interval)


if __name__ == "__main__":
    sys.exit(main())
