from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class BridgeError(Exception):
    """An error safe to display without including private message bodies."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def timestamp(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None  # Do not invent a timezone for ambiguous exports.
            return dt.timestamp()
        number = float(value)
        datetime.fromtimestamp(number, timezone.utc)
        return number
    except (ValueError, TypeError, OverflowError, OSError):
        return None

