import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Callable

from minty_core._toml import tomli_w, tomllib
from minty_core.fsutil import atomic_write_text

log = logging.getLogger(__name__)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, digest = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, hash_hex)


PIN_MIN, PIN_MAX = 4, 8


def is_weak_pin(pin: str) -> bool:
    """Every digit the same (0000), or each digit one more or one less than the one before (1234, 9876)."""
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    return len(set(pin)) == 1 or steps == {1} or steps == {-1}


class PasswordStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "panel_auth.toml"

    def is_set(self) -> bool:
        return self.path.exists()

    def set(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        salt, digest = hash_password(password)
        atomic_write_text(self.path, tomli_w.dumps({"salt": salt, "hash": digest}), 0o600)

    def set_pin(self, pin: str, allow_easy: bool = False) -> None:
        """The panel's PIN: four to eight digits, and not one that is easy to guess (all the same digit, or in a row)."""
        if not (pin.isascii() and pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX):
            raise ValueError(f"a PIN is {PIN_MIN} to {PIN_MAX} digits")
        if is_weak_pin(pin) and not allow_easy:
            raise ValueError("that PIN is too easy to guess (the same digit, or digits in a row): choose another")
        salt, digest = hash_password(pin)
        atomic_write_text(self.path, tomli_w.dumps({"salt": salt, "hash": digest}), 0o600)

    def verify(self, password: str) -> bool:
        if not self.is_set():
            return False
        try:
            with self.path.open("rb") as f:
                data = tomllib.load(f)
            return verify_password(password, data["salt"], data["hash"])
        except (OSError, KeyError, TypeError, ValueError) as e:  # TOMLDecodeError is a ValueError
            log.warning("panel_auth.toml unreadable or corrupt: %s", e)
            return False


class SessionStore:
    def __init__(self, ttl: float = 12 * 3600, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._expiry: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._expiry[token] = self._clock() + self._ttl
        return token

    def valid(self, token: str) -> bool:
        expiry = self._expiry.get(token or "")
        if expiry is None:
            return False
        if expiry <= self._clock():
            del self._expiry[token]
            return False
        return True

    def destroy(self, token: str) -> None:
        self._expiry.pop(token, None)


class LoginThrottle:
    def __init__(self, max_failures: int = 5, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._max, self._window, self._clock = max_failures, window, clock
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - self._window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self._max

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._failures.setdefault(key, []).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
