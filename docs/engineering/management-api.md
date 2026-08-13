# Management Administration API

R2D.2 provides an administration-only JSON API for management sources and
write-only credentials. It does not connect to management endpoints, obtain
SSH host keys, run commands, collect observations, or activate collectors.

All routes under `/api/management/` require an authenticated, enabled BEACN
administrator session. Read routes use the existing session authentication.
Mutation routes additionally require the session-bound CSRF token in the
`X-CSRF-Token` header. An authenticated UI can obtain that token from:

```text
GET /api/management/csrf
```

Tokens in query strings or request bodies are not accepted. Logging out or
invalidating the administrator session makes its token unusable.

The built-in Werkzeug access logger redacts values for security-sensitive
query parameter names on management API paths. This is a log-only safeguard:
Flask still receives the original request target, and query-string tokens
remain invalid. Non-sensitive query parameters remain visible for diagnostics.

Credential secrets are accepted only by credential create and replacement
requests. Responses expose safe metadata and never return plaintext secrets,
encrypted payloads, or encryption key identifiers. Credential creation and
replacement return `503 encryption_unavailable` until a valid
`BEACN_ENCRYPTION_KEY_FILE` or `BEACN_ENCRYPTION_KEY` is configured. There is
no plaintext fallback and no key is generated automatically.

Available routes are:

```text
GET    /api/management/csrf
GET    /api/management/sources
POST   /api/management/sources
GET    /api/management/sources/<source_id>
PATCH  /api/management/sources/<source_id>
DELETE /api/management/sources/<source_id>
GET    /api/management/credentials
POST   /api/management/credentials
GET    /api/management/credentials/<credential_id>
PUT    /api/management/credentials/<credential_id>
DELETE /api/management/credentials/<credential_id>
```

Creating or enabling a management source records administrator intent only.
It does not test connectivity or enable evidence collection. Credential
replacement preserves the credential UUID and existing source references.
Referenced credentials cannot be deleted. Source deletion removes its
capability rows but retains its credential.

Successful mutations, repository failures, CSRF failures, and oversized request
rejections emit sanitized application audit records containing the action,
outcome, administrator ID, and affected object ID where known. Request bodies,
credentials, ciphertext, and encryption keys are never audit fields. No separate
rate-limit dependency is used because R2D.2 performs no remote authentication;
rate limiting is mandatory before future connectivity or authentication tests.

BEACN currently uses its existing administrator flag as the authorization
boundary. A future multi-user model must preserve explicit administrator-only
authorization for these routes.
