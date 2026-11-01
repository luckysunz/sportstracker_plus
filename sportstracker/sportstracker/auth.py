"""Login data of the web backend: one user, password hash and session secret in ``data/auth.json``."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import time
from typing import Callable, Dict, Optional

from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"
# pbkdf2 with a moderate iteration count: a Pi Zero needs about a second for 60000 rounds.
HASH_METHOD = "pbkdf2:sha256:60000"
MIN_PASSWORD_LENGTH = 8


class AuthStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._data: Dict[str, object] = {}

    # -- storage ----------------------------------------------------------------

    def ensure(self) -> None:
        """Load the file; create it with admin/admin and a fresh secret key if it does not exist (or is broken)."""
        data = self._read()
        if not data or not data.get("password_hash") or not data.get("secret_key"):
            data = {
                "username": DEFAULT_USER,
                "password_hash": generate_password_hash(DEFAULT_PASSWORD, method=HASH_METHOD),
                "secret_key": secrets.token_hex(32),
                "default_password": True,
            }
            self._write(data)
        self._data = data

    def _read(self) -> Optional[Dict[str, object]]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, data: Dict[str, object]) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".auth.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- queries ----------------------------------------------------------------

    @property
    def secret_key(self) -> str:
        return str(self._data["secret_key"])

    @property
    def username(self) -> str:
        return str(self._data["username"])

    @property
    def uses_default_password(self) -> bool:
        return bool(self._data.get("default_password"))

    def check(self, username: str, password: str) -> bool:
        # Always run the hash check, so a wrong user name takes as long as a wrong password.
        password_ok = check_password_hash(str(self._data["password_hash"]), password)
        user_ok = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        return bool(password_ok and user_ok)

    # -- changes ----------------------------------------------------------------

    def set_credentials(self, username: str, password: str) -> None:
        data = dict(self._data)
        data["username"] = username
        data["password_hash"] = generate_password_hash(password, method=HASH_METHOD)
        data["default_password"] = password == DEFAULT_PASSWORD
        self._write(data)
        self._data = data


def password_problem(password: str) -> Optional[str]:
    """None if the new password is acceptable, otherwise the reason (German, shown to the user)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "Das neue Kennwort muss mindestens %d Zeichen lang sein." % MIN_PASSWORD_LENGTH
    if password.lower() in ("admin", "password", "passwort", "12345678"):
        return "Dieses Kennwort ist zu einfach."
    return None


class LoginThrottle:
    """Locks a client address for ``lockout`` seconds after ``max_failures`` failed logins within ``window`` seconds."""

    def __init__(self, max_failures: int = 5, window: float = 300.0, lockout: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._clock = clock
        self._failures: Dict[str, list] = {}
        self._locked_until: Dict[str, float] = {}

    def retry_after(self, client: str) -> float:
        """Seconds until the client may try again (0 = may try now)."""
        until = self._locked_until.get(client, 0.0)
        return max(0.0, until - self._clock())

    def failed(self, client: str) -> None:
        now = self._clock()
        recent = [t for t in self._failures.get(client, []) if now - t < self.window]
        recent.append(now)
        self._failures[client] = recent
        if len(recent) >= self.max_failures:
            self._locked_until[client] = now + self.lockout
            self._failures[client] = []

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
        self._locked_until.pop(client, None)
