import asyncio

from beacn.services import snmp


class Value:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return str(self.value)

    def __int__(self):
        return int(self.value)


class Octets(Value):
    def asOctets(self):
        return bytes(self.value)


class Engine:
    def close_dispatcher(self):
        return None


def oid(base, suffix):
    return f"{base}.{'.'.join(str(item) for item in suffix)}"


def install_bridge_fakes(monkeypatch, *, raw=None, columns=None):
    raw = raw or {}
    columns = columns or {}
    captured = {}

    monkeypatch.setattr(snmp, "SnmpEngine", Engine)
    monkeypatch.setattr(
        snmp,
        "_auth_data",
        lambda **kwargs: ("3", object()),
    )

    async def create(target, timeout, retries):
        captured.update({
            "target": target,
            "timeout": timeout,
            "retries": retries,
        })
        return object()

    async def walk_raw(engine, auth, transport, requested_oid):
        return raw.get(requested_oid, ([], None))

    async def walk_column(engine, auth, transport, requested_oid):
        return columns.get(requested_oid, ({}, None))

    monkeypatch.setattr(snmp.UdpTransportTarget, "create", create)
    monkeypatch.setattr(snmp, "_walk_raw_column", walk_raw)
    monkeypatch.setattr(snmp, "_walk_column", walk_column)
    return captured


def test_snmp_auth_construction(monkeypatch):
    version, v3 = snmp._auth_data(
        version="3",
        username="synthetic-user",
        auth_password="synthetic-auth",
        priv_password="synthetic-privacy",
    )
    assert version == "3"
    assert type(v3).__name__ == "UsmUserData"

    version, v2 = snmp._auth_data(
        version="2c",
        community="synthetic-community",
    )
    assert version == "2c"
    assert type(v2).__name__ == "CommunityData"


def test_successful_empty_bridge_table_and_timeout_contract(monkeypatch):
    captured = install_bridge_fakes(monkeypatch)
    result = asyncio.run(snmp._bridge_async(
        "192.0.2.10",
        timeout=2.25,
        retries=1,
    ))

    assert result["available"] is True
    assert result["usable"] is True
    assert result["entry_count"] == 0
    assert result["failed_essential_walks"] == []
    assert captured == {
        "target": ("192.0.2.10", 161),
        "timeout": 2.25,
        "retries": 1,
    }


def populated_walks(status=3):
    suffix = (0, 17, 34, 51, 68, 85)
    raw = {
        snmp.BRIDGE_OIDS["base_port_ifindex"]: ([
            (oid(snmp.BRIDGE_OIDS["base_port_ifindex"], (4,)), Value(9)),
        ], None),
        snmp.BRIDGE_OIDS["fdb_address"]: ([
            (oid(snmp.BRIDGE_OIDS["fdb_address"], suffix), Octets(bytes(suffix))),
        ], None),
        snmp.BRIDGE_OIDS["fdb_port"]: ([
            (oid(snmp.BRIDGE_OIDS["fdb_port"], suffix), Value(4)),
        ], None),
        snmp.BRIDGE_OIDS["fdb_status"]: ([
            (oid(snmp.BRIDGE_OIDS["fdb_status"], suffix), Value(status)),
        ], None),
    }
    columns = {
        snmp.INTERFACE_OIDS["name"]: ({9: Value("ethernet9")}, None),
        snmp.INTERFACE_OIDS["description"]: ({}, None),
    }
    return raw, columns


def test_populated_bridge_table_maps_port_interface_and_learned_status(monkeypatch):
    raw, columns = populated_walks()
    install_bridge_fakes(monkeypatch, raw=raw, columns=columns)
    result = asyncio.run(snmp._bridge_async("192.0.2.11"))

    assert result["available"] is True
    assert result["usable"] is True
    assert result["entry_count"] == 1
    assert result["forwarding_database"] == [{
        "mac": "00:11:22:33:44:55",
        "bridge_port": 4,
        "if_index": 9,
        "interface": "ethernet9",
        "status": {"code": 3, "state": "learned"},
        "attachment_eligible": True,
        "ineligible_reason": None,
    }]


def test_failed_and_partial_essential_walks_are_unusable(monkeypatch):
    for failures in (
        set(snmp.BRIDGE_ESSENTIAL_WALKS),
        {"fdb_port"},
        {"base_port_ifindex", "interface_names"},
    ):
        raw, columns = populated_walks()
        for name in failures:
            if name in snmp.BRIDGE_OIDS:
                raw[snmp.BRIDGE_OIDS[name]] = ([], f"{name} failed")
            elif name == "interface_names":
                columns[snmp.INTERFACE_OIDS["name"]] = ({}, "name failed")
        install_bridge_fakes(monkeypatch, raw=raw, columns=columns)
        result = asyncio.run(snmp._bridge_async("192.0.2.12"))
        assert result["available"] is False
        assert result["usable"] is False
        assert result["forwarding_database"] == []
        assert result["failed_essential_walks"] == sorted(failures)
        assert set(result["walk_errors"]) == failures


def test_non_learned_statuses_are_protocol_facts_not_attachment_evidence(monkeypatch):
    for status, state in ((1, "other"), (2, "invalid"), (4, "self"), (5, "management")):
        raw, columns = populated_walks(status)
        install_bridge_fakes(monkeypatch, raw=raw, columns=columns)
        result = asyncio.run(snmp._bridge_async("192.0.2.13"))
        entry = result["forwarding_database"][0]
        assert entry["status"]["state"] == state
        assert entry["attachment_eligible"] is False
        assert entry["ineligible_reason"] == f"fdb_status_{state}"


def test_malformed_and_duplicate_rows_are_rejected():
    entries, diagnostics = snmp._normalise_bridge_entries(
        addresses={
            (1,): "not-a-mac",
            (2,): "00:11:22:33:44:55",
            (3,): "00:11:22:33:44:55",
        },
        ports={(1,): 1, (2,): 4, (3,): 4},
        statuses={(1,): 3, (2,): 3, (3,): 3},
        bridge_ports={1: 8, 4: 9},
        interface_names={8: Value("ethernet8"), 9: Value("ethernet9")},
        interface_descriptions={},
    )
    assert len(entries) == 1
    assert {item["code"] for item in diagnostics} == {
        "malformed_fdb_row",
        "duplicate_fdb_row",
    }


def test_binary_and_oid_suffix_mac_normalization():
    assert snmp._format_mac(Octets(b"\xaa\xbb\xcc\xdd\xee\xff")) == (
        "aa:bb:cc:dd:ee:ff"
    )
    assert snmp._mac_from_oid_suffix((170, 187, 204, 221, 238, 255)) == (
        "aa:bb:cc:dd:ee:ff"
    )
    assert snmp._mac_from_oid_suffix((1, 2, 3)) is None


def test_snmp_diagnostics_redact_credentials(monkeypatch):
    secrets = {
        "community": "synthetic-community",
        "username": "synthetic-user",
        "auth_password": "synthetic-auth",
        "priv_password": "synthetic-privacy",
    }
    monkeypatch.setattr(snmp, "SnmpEngine", Engine)
    monkeypatch.setattr(
        snmp,
        "_auth_data",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic-user synthetic-auth synthetic-privacy synthetic-community")
        ),
    )
    result = asyncio.run(snmp._bridge_async("192.0.2.14", **secrets))
    serialized = str(result)
    assert result["available"] is False
    assert "[redacted]" in serialized
    assert all(value not in serialized for value in secrets.values())
