import threading
import time

from . import __version__


class AgentState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.iperf = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.version = __version__
        self.hardware_lock = threading.Lock()
        self.hardware_payload = None
        self.hardware_updated_at = 0.0

    def iperf_running(self) -> bool:
        with self.lock:
            return self.iperf is not None and self.iperf.poll() is None


STATE = AgentState()
