"""Access-log protection for security-sensitive management URLs."""

from __future__ import annotations

from urllib.parse import unquote_plus

from werkzeug.serving import WSGIRequestHandler

SENSITIVE_QUERY_PARAMETERS = frozenset(
    {
        "_csrf",
        "access_token",
        "api_token",
        "community",
        "csrf",
        "csrf_token",
        "key",
        "passphrase",
        "password",
        "secret",
        "token",
    }
)
REDACTED_QUERY_VALUE = "[REDACTED]"


def sanitize_access_log_target(target: str) -> str:
    """Return a display-only target with management query secrets redacted."""

    path, separator, query = target.partition("?")
    if not separator or not path.startswith("/api/management/"):
        return target

    sanitized = []
    for component in query.split("&"):
        raw_name, equals, _raw_value = component.partition("=")
        name = unquote_plus(raw_name).casefold()
        if name in SENSITIVE_QUERY_PARAMETERS:
            sanitized.append(f"{raw_name}={REDACTED_QUERY_VALUE}")
        else:
            sanitized.append(component if equals else raw_name)
    return f"{path}?{'&'.join(sanitized)}"


class SanitizedAccessLogRequestHandler(WSGIRequestHandler):
    """Use a redacted display target for Werkzeug access logging only."""

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        original_path = getattr(self, "path", None)
        if original_path is None:
            super().log_request(code, size)
            return

        self.path = sanitize_access_log_target(original_path)
        try:
            super().log_request(code, size)
        finally:
            self.path = original_path
