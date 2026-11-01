"""MQTT service: receives the values of the satellite sensors and writes them to ``data/satellites.json``.

    python -m sportstracker.service_mqtt --config config/config.json

Subscribes (as a client of the broker, e.g. Mosquitto on the Home Assistant Pi) to the state topic of every enabled
satellite: ``satellites.items[].topic`` or ``<base_topic>/<key>/state``. The payload is JSON, see ``satellites.py``.
Configuration changes (broker, topics) are picked up without a restart. paho-mqtt (1.x or 2.x) is only imported
when a connection is made, so the other services do not need it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .config import Config, load_config
from .errors import ConfigError
from .runtime import PROJECT_ROOT, resolve
from .satellites import PayloadError, parse_payload, satellites_path, store_reading

log = logging.getLogger("sportstracker.mqtt")


def default_client_factory() -> Any:
    import paho.mqtt.client as mqtt

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)     # paho-mqtt 2.x
    except AttributeError:
        return mqtt.Client()                                      # paho-mqtt 1.x (Debian Bookworm)


def topic_map(cfg: Config) -> Dict[str, str]:
    """``{state topic: satellite key}`` of all enabled satellites."""
    sat = cfg.satellites
    base = sat["mqtt"]["base_topic"]
    return {(item["topic"] or "%s/%s/state" % (base, item["key"])): item["key"]
            for item in sat["items"] if item["enabled"]}


class MqttBridge:
    def __init__(self, config_path: str, root: str = PROJECT_ROOT,
                 client_factory: Callable[[], Any] = default_client_factory,
                 now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.config_path = config_path
        self.root = root
        self._factory = client_factory
        self._now = now_func
        self._stop = threading.Event()
        self._client: Any = None
        self._signature: Any = None
        self._topics: Dict[str, str] = {}
        self._path = ""
        self.received = 0
        self.last_error = ""
        self._cfg: Optional[Config] = None

    # -- messages ---------------------------------------------------------------

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Store the values of one message. True if they were accepted."""
        key = self._topics.get(topic)
        if key is None or self._cfg is None:
            return False
        try:
            reading = parse_payload(json.loads(payload.decode("utf-8")), self._now())
        except (ValueError, UnicodeDecodeError, PayloadError) as exc:
            self.last_error = "%s: %s" % (topic, exc)
            log.warning("message on %s ignored: %s", topic, exc)
            return False
        keep = {item["key"] for item in self._cfg.satellites["items"]}
        store_reading(self._path, key, reading, keep=keep)
        self.received += 1
        return True

    # -- connection -------------------------------------------------------------

    def _wanted(self, cfg: Config) -> Optional[Tuple[Any, ...]]:
        mqtt = cfg.satellites["mqtt"]
        if not (mqtt["enabled"] and mqtt["host"] and topic_map(cfg)):
            return None
        return (mqtt["host"], mqtt["port"], mqtt["username"], mqtt["password"], tuple(sorted(topic_map(cfg).items())))

    def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001 - already gone
                pass

    def _on_connect(self, client: Any, *args: Any) -> None:
        log.info("connected to the broker, subscribing to %d topic(s)", len(self._topics))
        for topic in self._topics:
            client.subscribe(topic)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            self.handle_message(message.topic, bytes(message.payload))
        except Exception:  # noqa: BLE001 - never kill the network thread
            log.exception("could not store a message")

    def _connect(self, cfg: Config) -> None:
        mqtt = cfg.satellites["mqtt"]
        client = self._factory()
        if mqtt["username"]:
            client.username_pw_set(mqtt["username"], mqtt["password"] or None)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        if hasattr(client, "reconnect_delay_set"):
            client.reconnect_delay_set(min_delay=2, max_delay=60)
        client.connect_async(mqtt["host"], mqtt["port"], keepalive=60)
        client.loop_start()
        self._client = client
        log.info("connecting to %s:%s", mqtt["host"], mqtt["port"])

    def step(self) -> None:
        cfg = load_config(self.config_path)
        self._cfg = cfg
        self._path = satellites_path(resolve(cfg.data_dir, self.root))
        signature = self._wanted(cfg)
        if signature == self._signature and (signature is None or self._client is not None):
            return
        self._disconnect()
        self._signature = signature
        self._topics = topic_map(cfg) if signature is not None else {}
        if signature is not None:
            self._connect(cfg)
        else:
            log.info("MQTT is switched off or incomplete (waiting for the configuration)")

    # -- loop -------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, interval: float = 5.0) -> int:
        while not self._stop.is_set():
            try:
                self.step()
            except ConfigError as exc:
                log.error("configuration problem: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("unexpected error")
                self._signature = None
            self._stop.wait(interval)
        self._disconnect()
        return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_mqtt")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bridge = MqttBridge(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: bridge.stop())
    return bridge.run_forever()


if __name__ == "__main__":
    sys.exit(main())
