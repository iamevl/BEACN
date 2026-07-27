from datetime import datetime, timezone

from .config import LOG_PATH


def log(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass
