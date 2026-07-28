from pathlib import Path
import tempfile

from beacn.core import Device, Observation
from beacn.database import Database, initialise_schema
from beacn.inventory import DeviceRepository


def test_inventory_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(Path(tmp) / "beacn.db")
        with database.connect() as conn:
            initialise_schema(conn)

        repository = DeviceRepository(database)
        device = Device(hostname="test-node", primary_ip="192.168.1.10")
        saved = repository.save(device)
        assert saved.id == device.id
        assert saved.hostname == "test-node"

        repository.add_observation(Observation(
            device_id=device.id,
            source="test",
            field="hostname",
            value="test-node",
            confidence=1.0,
        ))
        observations = list(repository.observations(device.id))
        assert observations[0]["value"] == "test-node"
