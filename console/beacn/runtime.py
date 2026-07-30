import threading

from beacn.config import DB_PATH
from beacn.database import Database
from beacn.inventory import DeviceRepository


database = Database(DB_PATH)
repository = DeviceRepository(database)

scan_lock = threading.Lock()
db_write_lock = threading.RLock()

scan_state = {
    "running": False,
    "last_error": None,
}
