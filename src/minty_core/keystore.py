import json
import logging
import time
from pathlib import Path

from minty_core._toml import tomli_w, tomllib
from minty_core.fsutil import atomic_write_text


def mask_key(value: str) -> str:
    if len(value) < 12:
        return "…"
    return f"{value[:3]}…{value[-4:]}"


log = logging.getLogger(__name__)


class SecretsDamaged(RuntimeError):
    """secrets.toml cannot be parsed. Changing it would write over keys that may still be recoverable by hand, so nothing is changed."""


class SecretsStore:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "secrets.toml"

    def _read(self, strict: bool = False) -> dict[str, str]:
        """The keys. A file that cannot be parsed reads as empty (with a warning, so one bad line does not stop every request),
        and is never written over: the changing calls pass `strict` and stop instead."""
        if not self.path.exists():
            return {}
        try:
            with self.path.open("rb") as f:
                return dict(tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            if strict:
                raise SecretsDamaged(f"{self.path.name} is damaged ({e}); fix or move it first, the keys in it are untouched") from e
            log.warning("%s is damaged (%s): its keys are not available until it is fixed", self.path.name, e)
            return {}

    def _write(self, data: dict[str, str]) -> None:
        atomic_write_text(self.path, tomli_w.dumps(data), 0o600)

    # ---- when each key was last set (the values are not here, only names and dates) -----------------
    @property
    def _dates_path(self) -> Path:
        return self.path.with_name("secrets.dates.json")

    def _dates(self) -> dict[str, float]:
        try:
            return {str(k): float(v) for k, v in json.loads(self._dates_path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _stamp(self, names, now: float | None = None) -> None:
        dates = self._dates()
        for name in names:
            dates[name] = now if now is not None else time.time()
        atomic_write_text(self._dates_path, json.dumps(dates), 0o600)

    def ages_days(self, now: float | None = None) -> dict[str, int]:
        """Whole days since each stored key was last set; a key set before this was tracked counts from the file's
        own date."""
        now = now if now is not None else time.time()
        dates, out = self._dates(), {}
        try:
            fallback = self.path.stat().st_mtime
        except OSError:
            fallback = now
        for name in self._read():
            out[name] = int((now - dates.get(name, fallback)) // 86400)
        return out

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        data = self._read(strict=True)
        data[name] = value
        self._write(data)
        self._stamp([name])

    def set_many(self, values: dict[str, str]) -> None:
        data = self._read(strict=True)
        data.update(values)
        self._write(data)
        self._stamp(list(values))

    def delete(self, name: str) -> bool:
        data = self._read(strict=True)
        if name not in data:
            return False
        del data[name]
        self._write(data)
        return True

    def names(self) -> list[str]:
        return sorted(self._read())

    def masked(self) -> dict[str, str]:
        return {k: mask_key(v) for k, v in self._read().items()}

    def values(self) -> list[str]:
        return list(self._read().values())
