"""Allowlisted SSH interface inventory collection."""

from __future__ import annotations

import time

from beacn.management.collection import CollectionError
from beacn.management.connectivity import HostIdentity, SSHTransport, _fingerprint

INTERFACE_COMMANDS = (
    "ip -o link show",
    "ip -o address show",
)
MAX_COMMAND_OUTPUT_BYTES = 256 * 1024


class SSHInterfaceInventoryCollector:
    def __init__(self, *, transport=None, clock=time.monotonic):
        self._transport = transport or SSHTransport()
        self._clock = clock

    def identity(self, source) -> HostIdentity:
        return self._transport.identity(
            source.management_address,
            source.management_port or 22,
            source.connection_timeout_seconds,
        )

    def _read_channel(self, channel, timeout: int) -> tuple[str, str]:
        stdout = bytearray()
        stderr = bytearray()
        deadline = self._clock() + timeout
        while True:
            while channel.recv_ready():
                stdout.extend(channel.recv(32768))
                if len(stdout) + len(stderr) > MAX_COMMAND_OUTPUT_BYTES:
                    raise CollectionError(
                        "output_too_large", "Interface inventory output exceeded its limit."
                    )
            while channel.recv_stderr_ready():
                stderr.extend(channel.recv_stderr(32768))
                if len(stdout) + len(stderr) > MAX_COMMAND_OUTPUT_BYTES:
                    raise CollectionError(
                        "output_too_large", "Interface inventory output exceeded its limit."
                    )
            if channel.exit_status_ready() and not (
                channel.recv_ready() or channel.recv_stderr_ready()
            ):
                break
            if self._clock() >= deadline:
                raise CollectionError(
                    "timeout", "Interface inventory collection timed out."
                )
            time.sleep(0.01)
        if channel.recv_exit_status() != 0:
            raise CollectionError("command_failed", "Interface inventory collection failed.")
        try:
            return stdout.decode("utf-8"), stderr.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CollectionError(
                "malformed_output", "Interface inventory output is invalid."
            ) from exc

    def collect(self, source, secrets, credential_type: str) -> tuple[str, str]:
        paramiko = self._transport._paramiko()
        connection = transport = None
        try:
            connection, transport, key = self._transport._open(
                source.management_address,
                source.management_port or 22,
                source.connection_timeout_seconds,
            )
            expected = HostIdentity(
                source.ssh_host_key_algorithm or "",
                source.ssh_host_key_fingerprint or "",
            )
            if not source.ssh_trusted or _fingerprint(key) != expected:
                raise CollectionError(
                    "host_identity_changed", "SSH host identity verification failed."
                )
            username = secrets.get("username", "")
            if credential_type == "username_password":
                transport.auth_password(username, secrets.get("password", ""))
            elif credential_type == "ssh_private_key":
                private_key = self._transport._private_key(
                    paramiko,
                    secrets.get("private_key", ""),
                    secrets.get("passphrase"),
                )
                transport.auth_publickey(username, private_key)
            else:
                raise CollectionError(
                    "configuration_invalid", "SSH credential type is not supported."
                )
            if not transport.is_authenticated():
                raise CollectionError(
                    "authentication_failed", "Management authentication failed."
                )

            outputs = []
            for command in INTERFACE_COMMANDS:
                channel = transport.open_session(
                    timeout=source.connection_timeout_seconds
                )
                try:
                    channel.settimeout(source.connection_timeout_seconds)
                    channel.exec_command(command)
                    stdout, _stderr = self._read_channel(
                        channel,
                        source.connection_timeout_seconds,
                    )
                    outputs.append(stdout)
                finally:
                    channel.close()
            return outputs[0], outputs[1]
        except CollectionError:
            raise
        except paramiko.AuthenticationException as exc:
            raise CollectionError(
                "authentication_failed", "Management authentication failed."
            ) from exc
        except TimeoutError as exc:
            raise CollectionError(
                "timeout", "Interface inventory collection timed out."
            ) from exc
        except (paramiko.SSHException, OSError) as exc:
            raise CollectionError(
                "collection_failed", "Interface inventory collection failed safely."
            ) from exc
        finally:
            if transport is not None:
                transport.close()
            if connection is not None:
                connection.close()
