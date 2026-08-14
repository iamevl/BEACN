"""Explicit, non-collecting management transport validation."""

from __future__ import annotations

import base64
import hashlib
import io
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from threading import Lock

from beacn.management import ManagementSource
from beacn.services.snmp import probe_snmp


@dataclass(frozen=True, slots=True)
class HostIdentity:
    algorithm: str
    fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    category: str
    message: str
    candidate: HostIdentity | None = None
    expected: HostIdentity | None = None
    presented: HostIdentity | None = None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        return {key: item for key, item in value.items() if item is not None}


class ConnectivityError(RuntimeError):
    """Stable transport error whose text never contains credentials."""


class ConnectivityRateLimiter:
    def __init__(
        self,
        *,
        limit: int = 5,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._attempts: dict[tuple[int, str], list[float]] = {}
        self._lock = Lock()

    def allow(self, administrator_id: int, source_id: str) -> bool:
        key = (int(administrator_id), str(source_id))
        now = self._clock()
        with self._lock:
            recent = [
                value
                for value in self._attempts.get(key, [])
                if now - value < self._window
            ]
            if len(recent) >= self._limit:
                self._attempts[key] = recent
                return False
            recent.append(now)
            self._attempts[key] = recent
            return True


def _fingerprint(key) -> HostIdentity:
    digest = hashlib.sha256(key.asbytes()).digest()
    value = base64.b64encode(digest).decode("ascii").rstrip("=")
    return HostIdentity(key.get_name(), f"SHA256:{value}")


class SSHTransport:
    """Paramiko transport validation with no channel or command execution."""

    @staticmethod
    def _paramiko():
        import paramiko

        return paramiko

    def _open(self, address: str, port: int, timeout: int):
        paramiko = self._paramiko()
        connection = socket.create_connection((address, port), timeout=timeout)
        connection.settimeout(timeout)
        transport = paramiko.Transport(connection)
        try:
            transport.banner_timeout = timeout
            transport.auth_timeout = timeout
            transport.start_client(timeout=timeout)
            key = transport.get_remote_server_key()
            return connection, transport, key
        except Exception:
            transport.close()
            connection.close()
            raise

    def identity(self, address: str, port: int, timeout: int) -> HostIdentity:
        connection = transport = None
        try:
            connection, transport, key = self._open(address, port, timeout)
            return _fingerprint(key)
        finally:
            if transport is not None:
                transport.close()
            if connection is not None:
                connection.close()

    @staticmethod
    def _private_key(paramiko, value: str, passphrase: str | None):
        for key_class in (
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.RSAKey,
        ):
            try:
                return key_class.from_private_key(
                    io.StringIO(value), password=passphrase or None
                )
            except (paramiko.SSHException, ValueError):
                continue
        raise ConnectivityError("SSH private key is invalid.")

    def authenticate(
        self,
        source: ManagementSource,
        secrets: Mapping[str, str],
        credential_type: str,
    ) -> ConnectivityResult:
        paramiko = self._paramiko()
        port = source.management_port or 22
        connection = transport = None
        try:
            connection, transport, key = self._open(
                source.management_address,
                port,
                source.connection_timeout_seconds,
            )
            presented = _fingerprint(key)
            expected = HostIdentity(
                source.ssh_host_key_algorithm or "",
                source.ssh_host_key_fingerprint or "",
            )
            if not source.ssh_trusted:
                return ConnectivityResult(
                    "host_identity_untrusted",
                    "SSH host identity requires explicit trust.",
                    candidate=presented,
                )
            if presented != expected:
                return ConnectivityResult(
                    "host_identity_changed",
                    "SSH host identity has changed.",
                    expected=expected,
                    presented=presented,
                )

            username = secrets.get("username", "")
            if credential_type == "username_password":
                transport.auth_password(username, secrets.get("password", ""))
            elif credential_type == "ssh_private_key":
                key_value = self._private_key(
                    paramiko,
                    secrets.get("private_key", ""),
                    secrets.get("passphrase"),
                )
                transport.auth_publickey(username, key_value)
            else:
                return ConnectivityResult(
                    "configuration_invalid",
                    "Credential type is not valid for SSH.",
                )
            if not transport.is_authenticated():
                return ConnectivityResult(
                    "authentication_failed", "Management authentication failed."
                )
            return ConnectivityResult("reachable", "Management transport is reachable.")
        except paramiko.AuthenticationException:
            return ConnectivityResult(
                "authentication_failed", "Management authentication failed."
            )
        except TimeoutError:
            return ConnectivityResult("timeout", "Management connection timed out.")
        except ConnectionRefusedError:
            return ConnectivityResult(
                "connection_refused", "Management connection was refused."
            )
        except (paramiko.SSHException, OSError, ConnectivityError):
            return ConnectivityResult(
                "internal_failure", "Management connection failed safely."
            )
        finally:
            if transport is not None:
                transport.close()
            if connection is not None:
                connection.close()


class ManagementConnectivityService:
    def __init__(self, repository, *, ssh_transport=None, snmp_probe=probe_snmp):
        self._repository = repository
        self._ssh = ssh_transport or SSHTransport()
        self._snmp_probe = snmp_probe

    def test(self, source: ManagementSource) -> ConnectivityResult:
        if source.orphaned or not source.enabled:
            return ConnectivityResult(
                "configuration_invalid", "Management source is not eligible."
            )
        if source.adapter_type == "ssh":
            if not source.credential_id:
                return ConnectivityResult(
                    "configuration_invalid", "SSH credential is required."
                )
            try:
                presented = self._ssh.identity(
                    source.management_address,
                    source.management_port or 22,
                    source.connection_timeout_seconds,
                )
            except TimeoutError:
                return ConnectivityResult(
                    "timeout", "Management connection timed out."
                )
            except ConnectionRefusedError:
                return ConnectivityResult(
                    "connection_refused", "Management connection was refused."
                )
            except Exception:  # noqa: BLE001 - stable transport result boundary
                return ConnectivityResult(
                    "internal_failure", "Management connection failed safely."
                )
            if not source.ssh_trusted:
                return ConnectivityResult(
                    "host_identity_untrusted",
                    "SSH host identity requires explicit trust.",
                    candidate=presented,
                )
            expected = HostIdentity(
                source.ssh_host_key_algorithm or "",
                source.ssh_host_key_fingerprint or "",
            )
            if presented != expected:
                return ConnectivityResult(
                    "host_identity_changed",
                    "SSH host identity has changed.",
                    expected=expected,
                    presented=presented,
                )
            credential = self._repository.get_credential(source.credential_id)
            secrets = self._repository.decrypt_credential(source.credential_id)
            return self._ssh.authenticate(source, secrets, credential.credential_type)
        if source.adapter_type == "snmp":
            return self._test_snmp(source)
        return ConnectivityResult(
            "unsupported_adapter", "Management adapter is unsupported."
        )

    def candidate_identity(self, source: ManagementSource) -> HostIdentity:
        if source.adapter_type != "ssh":
            raise ConnectivityError("SSH host identity is not available.")
        try:
            return self._ssh.identity(
                source.management_address,
                source.management_port or 22,
                source.connection_timeout_seconds,
            )
        except Exception as exc:
            if isinstance(exc, ConnectivityError):
                raise
            raise ConnectivityError("SSH host identity could not be obtained.") from None

    def _test_snmp(self, source: ManagementSource) -> ConnectivityResult:
        if not source.credential_id:
            return ConnectivityResult(
                "configuration_invalid", "SNMP credential is required."
            )
        credential = self._repository.get_credential(source.credential_id)
        secrets = self._repository.decrypt_credential(source.credential_id)
        if credential.credential_type == "snmp_v2_community":
            kwargs = {"version": "2c", "community": secrets["community"]}
        elif credential.credential_type == "snmp_v3":
            kwargs = {
                "version": "3",
                "username": secrets["username"],
                "auth_password": secrets["auth_password"],
                "priv_password": secrets["priv_password"],
            }
        else:
            return ConnectivityResult(
                "configuration_invalid", "Credential type is not valid for SNMP."
            )
        try:
            result = self._snmp_probe(
                source.management_address,
                port=source.management_port or 161,
                timeout=float(source.connection_timeout_seconds),
                retries=0,
                **kwargs,
            )
        except TimeoutError:
            return ConnectivityResult("timeout", "Management connection timed out.")
        except Exception:  # noqa: BLE001 - stable transport result boundary
            return ConnectivityResult(
                "internal_failure", "Management connection failed safely."
            )
        if result.get("available"):
            return ConnectivityResult("reachable", "Management transport is reachable.")
        error = str(result.get("error", "")).casefold()
        if "timeout" in error or "no snmp response" in error:
            return ConnectivityResult("timeout", "Management connection timed out.")
        return ConnectivityResult(
            "authentication_failed", "Management authentication failed."
        )
