import signal

from .logging_utils import log
from .runtime import shutdown, start_supervisor
from .web import serve


def _handle_signal(signum, _frame):
    log(f"Shutdown signal received: {signum}")
    shutdown()


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    log("Agent starting")
    start_supervisor()
    try:
        serve()
    finally:
        shutdown()
        log("Agent stopped")
    return 0
