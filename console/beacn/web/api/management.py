"""Authenticated, CSRF-protected management administration API."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from flask import Blueprint, current_app, jsonify, request, session

from beacn.management import (
    ManagementNotFoundError,
    ManagementStorageError,
    ManagementValidationError,
)
from beacn.management.collection import CollectionError
from beacn.management.connectivity import ConnectivityError, HostIdentity
from beacn.security import (
    CredentialCryptoError,
    CredentialKeyUnavailable,
    CredentialValidationError,
)

CSRF_HEADER = "X-CSRF-Token"
MAX_MANAGEMENT_REQUEST_BYTES = 1024 * 1024
SOURCE_CREATE_FIELDS = frozenset(
    {
        "participant_kind",
        "participant_id",
        "adapter_type",
        "management_address",
        "management_port",
        "enabled",
        "credential_id",
        "connection_timeout_seconds",
        "capabilities",
    }
)
SOURCE_UPDATE_FIELDS = frozenset(
    {
        "management_address",
        "management_port",
        "enabled",
        "credential_id",
        "connection_timeout_seconds",
        "capabilities",
    }
)


def _error(code: str, message: str, status: int):
    return jsonify(
        {
            "ok": False,
            "error": {"code": code, "message": message},
        }
    ), status


def _json_object():
    if not request.is_json:
        return None
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def _credential_payload(credential) -> dict[str, object]:
    return credential.to_dict()


def _source_payload(repository, source) -> dict[str, object]:
    payload = source.to_dict()
    credential = (
        repository.get_credential(source.credential_id)
        if source.credential_id
        else None
    )
    payload["credential"] = (
        {
            "id": credential.id,
            "credential_type": credential.credential_type,
            "label": credential.label,
            "configured": credential.configured,
        }
        if credential
        else None
    )
    payload["interface_inventory"] = repository.interface_inventory_status(source.id)
    return payload


def _csrf_valid() -> bool:
    submitted = request.headers.get(CSRF_HEADER, "")
    expected = session.get("auth_csrf_token", "")
    return bool(
        submitted and expected and secrets.compare_digest(str(submitted), str(expected))
    )


def create_management_blueprint(
    *,
    repository: Callable,
    current_user: Callable,
    csrf_token: Callable,
    connectivity_service: Callable | None = None,
    connectivity_limiter=None,
    collection_service: Callable | None = None,
    collection_limiter=None,
):
    blueprint = Blueprint("management_api", __name__)

    def audit(action, object_id=None, outcome="success"):
        user = current_user() or {}
        current_app.logger.info(
            "management_audit action=%s outcome=%s administrator_id=%s object_id=%s",
            action,
            outcome,
            user.get("id"),
            object_id,
        )

    @blueprint.before_request
    def require_management_admin():
        user = current_user()
        if not user:
            return _error("unauthenticated", "Authentication required.", 401)
        if not bool(user.get("is_admin")):
            return _error("forbidden", "Administrator access is required.", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if (
                request.content_length is not None
                and request.content_length > MAX_MANAGEMENT_REQUEST_BYTES
            ):
                audit("mutation", outcome="payload_too_large")
                return _error("payload_too_large", "Request payload is too large.", 413)
            if not _csrf_valid():
                audit("mutation", outcome="csrf_failed")
                return _error("csrf_failed", "Security token is invalid.", 403)
        return None

    def repo():
        return repository()

    def connectivity():
        if connectivity_service is None:
            raise ConnectivityError("Management connectivity is unavailable.")
        return connectivity_service()

    def collection():
        if collection_service is None:
            raise CollectionError(
                "collection_unavailable", "Management collection is unavailable."
            )
        return collection_service()

    def handle_error(exc, action=None, object_id=None):
        if action:
            audit(action, object_id, "failure")
        if isinstance(exc, CredentialKeyUnavailable):
            return _error(
                "encryption_unavailable",
                "Management credential encryption is unavailable.",
                503,
            )
        if isinstance(exc, ManagementNotFoundError):
            return _error("not_found", "Management record was not found.", 404)
        if isinstance(exc, ManagementValidationError | CredentialValidationError):
            return _error("validation_failed", str(exc), 400)
        if isinstance(exc, CredentialCryptoError):
            return _error("credential_unavailable", "Credential operation failed.", 409)
        if isinstance(exc, ManagementStorageError):
            return _error("conflict", str(exc), 409)
        if isinstance(exc, ConnectivityError):
            return _error("connectivity_failed", "Connectivity test failed safely.", 503)
        if isinstance(exc, CollectionError):
            status = 409 if exc.category in {
                "source_disabled",
                "capability_disabled",
                "configuration_invalid",
                "host_identity_changed",
                "authentication_failed",
            } else 503
            return _error(exc.category, str(exc), status)
        current_app.logger.error(
            "management_audit action=%s outcome=internal_failure object_id=%s",
            action or "unknown",
            object_id,
        )
        return _error("internal_error", "Management operation failed.", 500)

    @blueprint.get("/api/management/csrf")
    def management_csrf():
        return jsonify({"ok": True, "csrf_token": csrf_token()})

    @blueprint.get("/api/management/status")
    def management_status():
        return jsonify(
            {
                "ok": True,
                "encryption_available": repo().encryption_available,
                "supported_adapters": ["snmp", "ssh"],
            }
        )

    @blueprint.get("/api/management/credentials")
    def credential_list():
        return jsonify(
            {
                "ok": True,
                "credentials": [
                    _credential_payload(item) for item in repo().list_credentials()
                ],
            }
        )

    @blueprint.post("/api/management/credentials")
    def credential_create():
        payload = _json_object()
        if payload is None:
            return _error("malformed_json", "A JSON object is required.", 400)
        if set(payload) != {"credential_type", "label", "secret"}:
            return _error("validation_failed", "Credential fields are invalid.", 400)
        try:
            item = repo().create_credential(
                payload["credential_type"], payload["label"], payload["secret"]
            )
            audit("credential_create", item.id)
            return jsonify({"ok": True, "credential": _credential_payload(item)}), 201
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "credential_create")

    @blueprint.get("/api/management/credentials/<credential_id>")
    def credential_get(credential_id):
        item = repo().get_credential(credential_id)
        if item is None:
            return _error("not_found", "Management record was not found.", 404)
        return jsonify({"ok": True, "credential": _credential_payload(item)})

    @blueprint.put("/api/management/credentials/<credential_id>")
    def credential_replace(credential_id):
        payload = _json_object()
        if payload is None:
            return _error("malformed_json", "A JSON object is required.", 400)
        if set(payload) != {"secret"}:
            return _error("validation_failed", "Credential fields are invalid.", 400)
        try:
            item = repo().replace_credential(credential_id, payload["secret"])
            audit("credential_replace", credential_id)
            return jsonify({"ok": True, "credential": _credential_payload(item)})
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "credential_replace", credential_id)

    @blueprint.delete("/api/management/credentials/<credential_id>")
    def credential_delete(credential_id):
        try:
            repo().delete_credential(credential_id)
            audit("credential_delete", credential_id)
            return "", 204
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "credential_delete", credential_id)

    @blueprint.get("/api/management/sources")
    def source_list():
        repository_value = repo()
        return jsonify(
            {
                "ok": True,
                "sources": [
                    _source_payload(repository_value, item)
                    for item in repository_value.list_sources()
                ],
            }
        )

    @blueprint.post("/api/management/sources")
    def source_create():
        payload = _json_object()
        if payload is None:
            return _error("malformed_json", "A JSON object is required.", 400)
        if set(payload) - SOURCE_CREATE_FIELDS or not {
            "participant_kind",
            "participant_id",
            "adapter_type",
            "management_address",
        }.issubset(payload):
            return _error(
                "validation_failed", "Management source fields are invalid.", 400
            )
        try:
            item = repo().create_source(**payload)
            audit("source_create", item.id)
            return jsonify({"ok": True, "source": _source_payload(repo(), item)}), 201
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "source_create")

    @blueprint.get("/api/management/sources/<source_id>")
    def source_get(source_id):
        repository_value = repo()
        item = repository_value.get_source(source_id)
        if item is None:
            return _error("not_found", "Management record was not found.", 404)
        return jsonify({"ok": True, "source": _source_payload(repository_value, item)})

    @blueprint.patch("/api/management/sources/<source_id>")
    def source_update(source_id):
        payload = _json_object()
        if payload is None:
            return _error("malformed_json", "A JSON object is required.", 400)
        if not payload or set(payload) - SOURCE_UPDATE_FIELDS:
            return _error(
                "validation_failed", "Management source fields are invalid.", 400
            )
        try:
            repository_value = repo()
            item = repository_value.update_source(source_id, **payload)
            audit("source_update", source_id)
            return jsonify(
                {
                    "ok": True,
                    "source": _source_payload(repository_value, item),
                }
            )
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "source_update", source_id)

    @blueprint.delete("/api/management/sources/<source_id>")
    def source_delete(source_id):
        try:
            repo().delete_source(source_id)
            audit("source_delete", source_id)
            return "", 204
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "source_delete", source_id)

    @blueprint.post("/api/management/sources/<source_id>/test")
    def source_test(source_id):
        payload = _json_object()
        if payload != {}:
            return _error("validation_failed", "Connectivity test fields are invalid.", 400)
        user = current_user()
        if connectivity_limiter is not None and not connectivity_limiter.allow(
            user["id"], source_id
        ):
            audit("source_test", source_id, "rate_limited")
            return _error("rate_limited", "Connectivity test rate limit exceeded.", 429)
        source = repo().get_source(source_id)
        if source is None:
            return _error("not_found", "Management record was not found.", 404)
        try:
            result = connectivity().test(source)
            audit("source_test", source_id, result.category)
            return jsonify({"ok": True, "result": result.to_dict()})
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "source_test", source_id)

    @blueprint.post("/api/management/sources/<source_id>/trust")
    def source_trust(source_id):
        payload = _json_object()
        if payload is None or set(payload) != {"algorithm", "fingerprint"}:
            return _error("validation_failed", "SSH trust fields are invalid.", 400)
        user = current_user()
        if connectivity_limiter is not None and not connectivity_limiter.allow(
            user["id"], source_id
        ):
            audit("source_trust", source_id, "rate_limited")
            return _error("rate_limited", "Connectivity test rate limit exceeded.", 429)
        source = repo().get_source(source_id)
        if source is None:
            return _error("not_found", "Management record was not found.", 404)
        try:
            requested = HostIdentity(
                str(payload["algorithm"] or "").strip(),
                str(payload["fingerprint"] or "").strip(),
            )
            presented = connectivity().candidate_identity(source)
            if requested != presented:
                audit("source_trust", source_id, "host_identity_changed")
                return jsonify(
                    {
                        "ok": True,
                        "result": {
                            "category": "host_identity_changed",
                            "message": "SSH host identity does not match.",
                            "expected": requested.to_dict(),
                            "presented": presented.to_dict(),
                        },
                    }
                ), 409
            updated = repo().set_ssh_trust(
                source_id,
                algorithm=presented.algorithm,
                fingerprint=presented.fingerprint,
                trusted_by=user["id"],
            )
            audit("source_trust", source_id)
            return jsonify({"ok": True, "source": _source_payload(repo(), updated)})
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "source_trust", source_id)

    @blueprint.get("/api/management/sources/<source_id>/interface-inventory")
    def interface_inventory_get(source_id):
        repository_value = repo()
        source = repository_value.get_source(source_id)
        if source is None:
            return _error("not_found", "Management record was not found.", 404)
        return jsonify(
            {
                "ok": True,
                "status": repository_value.interface_inventory_status(source_id),
                "interfaces": [
                    item.to_dict()
                    for item in repository_value.list_interface_inventory(source_id)
                ],
            }
        )

    @blueprint.post("/api/management/sources/<source_id>/collect/interface-inventory")
    def interface_inventory_collect(source_id):
        payload = _json_object()
        if payload != {}:
            return _error("validation_failed", "Collection fields are invalid.", 400)
        user = current_user()
        if collection_limiter is not None and not collection_limiter.allow(
            user["id"], source_id
        ):
            audit("interface_inventory_collect", source_id, "rate_limited")
            return _error("rate_limited", "Collection rate limit exceeded.", 429)
        source = repo().get_source(source_id)
        if source is None:
            return _error("not_found", "Management record was not found.", 404)
        try:
            result = collection().collect_interface_inventory(source)
            audit("interface_inventory_collect", source_id, result.category)
            return jsonify({"ok": True, "result": result.to_dict()})
        except Exception as exc:  # noqa: BLE001 - sanitized API boundary
            return handle_error(exc, "interface_inventory_collect", source_id)

    return blueprint
