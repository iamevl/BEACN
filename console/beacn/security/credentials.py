"""Authenticated encryption for write-only management credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTION_FORMAT = "fernet-v1"
PAYLOAD_VERSION = 1
LEGACY_KEYS_ENV = "BEACN_ENCRYPTION_LEGACY_KEYS"


class CredentialSecurityError(RuntimeError):
    """Base class whose messages are safe for logs and user interfaces."""


class CredentialKeyUnavailable(CredentialSecurityError):
    pass


class CredentialCryptoError(CredentialSecurityError):
    pass


class CredentialValidationError(CredentialSecurityError, ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    encrypted_payload: str
    encryption_format: str
    key_id: str


@dataclass(frozen=True, slots=True)
class FernetKeyRing:
    active_key_id: str
    _fernets: Mapping[str, Fernet]

    @property
    def available(self) -> bool:
        return bool(self.active_key_id and self._fernets)

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(self._fernets)

    def encrypt(self, value: bytes) -> tuple[str, str]:
        if not self.available:
            raise CredentialKeyUnavailable(
                "Management credential encryption is unavailable."
            )
        token = self._fernets[self.active_key_id].encrypt(value)
        return token.decode("ascii"), self.active_key_id

    def decrypt(self, token: str, key_id: str) -> bytes:
        if not self.available:
            raise CredentialKeyUnavailable(
                "Management credential encryption is unavailable."
            )
        fernet = self._fernets.get(str(key_id or ""))
        if fernet is None:
            raise CredentialCryptoError("Management credential key is unavailable.")
        try:
            return fernet.decrypt(str(token).encode("ascii"))
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise CredentialCryptoError(
                "Management credential could not be decrypted."
            ) from exc


def _parse_key(value: str) -> tuple[str, Fernet]:
    candidate = str(value or "").strip()
    try:
        raw = base64.urlsafe_b64decode(candidate.encode("ascii"))
        if len(raw) != 32:
            raise ValueError
        fernet = Fernet(candidate.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CredentialCryptoError(
            "Management credential encryption key is invalid."
        ) from exc
    key_id = hashlib.sha256(raw).hexdigest()[:16]
    return key_id, fernet


def _key_values_from_file(path_value: str) -> list[str]:
    try:
        content = Path(path_value).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CredentialCryptoError(
            "Management credential encryption key file is unavailable."
        ) from exc
    return [line.strip() for line in content.splitlines() if line.strip()]


def load_credential_key_ring(
    environment: Mapping[str, str] | None = None,
) -> FernetKeyRing | None:
    """Load active and legacy Fernet keys without generating key material.

    A key file may contain one key per non-empty line; its first key is active.
    With an environment active key, comma-separated legacy keys may be supplied
    through BEACN_ENCRYPTION_LEGACY_KEYS.
    """

    env = os.environ if environment is None else environment
    key_file = str(env.get("BEACN_ENCRYPTION_KEY_FILE", "") or "").strip()
    active_environment_key = str(env.get("BEACN_ENCRYPTION_KEY", "") or "").strip()

    if key_file:
        values = _key_values_from_file(key_file)
    elif active_environment_key:
        values = [active_environment_key]
        values.extend(
            item.strip()
            for item in str(env.get(LEGACY_KEYS_ENV, "") or "").split(",")
            if item.strip()
        )
    else:
        return None

    if not values:
        raise CredentialCryptoError("Management credential encryption key is invalid.")

    parsed = [_parse_key(value) for value in values]
    fernets: dict[str, Fernet] = {}
    for key_id, fernet in parsed:
        if key_id in fernets:
            raise CredentialCryptoError(
                "Management credential encryption keys must be unique."
            )
        fernets[key_id] = fernet

    return FernetKeyRing(parsed[0][0], fernets)


_CREDENTIAL_FIELDS = {
    "username_password": ({"username", "password"}, set()),
    "ssh_private_key": ({"username", "private_key"}, {"passphrase"}),
    "snmp_v2_community": ({"community"}, set()),
    "snmp_v3": (
        {"username", "auth_password", "priv_password"},
        set(),
    ),
    "api_token": ({"token"}, set()),
}


def _validated_payload(
    credential_type: str,
    secret_fields: Mapping[str, str],
) -> dict[str, object]:
    required_optional = _CREDENTIAL_FIELDS.get(credential_type)
    if required_optional is None:
        raise CredentialValidationError("Unsupported credential type.")

    if not isinstance(secret_fields, Mapping):
        raise CredentialValidationError("Credential payload is invalid.")

    required, optional = required_optional
    supplied = set(secret_fields)
    if supplied - required - optional:
        raise CredentialValidationError("Credential payload contains unknown fields.")
    if not required.issubset(supplied):
        raise CredentialValidationError(
            "Credential payload is missing required fields."
        )

    normalized: dict[str, str] = {}
    for name in sorted(supplied):
        value = secret_fields[name]
        if not isinstance(value, str):
            raise CredentialValidationError("Credential fields must be text.")
        if name in required and not value:
            raise CredentialValidationError("Credential fields must not be empty.")
        normalized[name] = value

    return {
        "version": PAYLOAD_VERSION,
        "credential_type": credential_type,
        "secrets": normalized,
    }


class CredentialCipher:
    def __init__(self, key_ring: FernetKeyRing | None):
        self._key_ring = key_ring

    @property
    def available(self) -> bool:
        return self._key_ring is not None and self._key_ring.available

    def encrypt(
        self,
        credential_type: str,
        secret_fields: Mapping[str, str],
    ) -> EncryptedCredential:
        if not self.available:
            raise CredentialKeyUnavailable(
                "Management credential encryption is unavailable."
            )
        payload = _validated_payload(credential_type, secret_fields)
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        token, key_id = self._key_ring.encrypt(serialized)
        return EncryptedCredential(token, ENCRYPTION_FORMAT, key_id)

    def decrypt(
        self,
        *,
        credential_type: str,
        encrypted_payload: str,
        encryption_format: str,
        key_id: str,
    ) -> dict[str, str]:
        if encryption_format != ENCRYPTION_FORMAT:
            raise CredentialCryptoError(
                "Management credential encryption format is unsupported."
            )
        if not self.available:
            raise CredentialKeyUnavailable(
                "Management credential encryption is unavailable."
            )

        plaintext = self._key_ring.decrypt(encrypted_payload, key_id)
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, TypeError):
            raise CredentialCryptoError(
                "Management credential payload is invalid."
            ) from None

        if not isinstance(payload, dict) or payload.get("version") != PAYLOAD_VERSION:
            raise CredentialCryptoError(
                "Management credential payload version is unsupported."
            )
        if payload.get("credential_type") != credential_type:
            raise CredentialCryptoError(
                "Management credential type does not match its payload."
            )
        try:
            validated = _validated_payload(credential_type, payload.get("secrets"))
        except CredentialValidationError:
            raise CredentialCryptoError(
                "Management credential payload is invalid."
            ) from None
        return dict(validated["secrets"])


def credential_cipher_from_environment(
    environment: Mapping[str, str] | None = None,
) -> CredentialCipher:
    """Return a locked cipher for absent or invalid optional configuration."""

    try:
        return CredentialCipher(load_credential_key_ring(environment))
    except CredentialCryptoError:
        return CredentialCipher(None)
