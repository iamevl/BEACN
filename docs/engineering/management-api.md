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
GET    /api/management/status
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
POST   /api/management/sources/<source_id>/test
POST   /api/management/sources/<source_id>/trust
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

## Trust and connectivity

R2D.3 supports explicit, rate-limited SSH and SNMP connectivity checks. These
checks only validate the configured transport and authentication; they do not
run SSH commands, walk attachment tables, create observations, enable
capabilities, or change topology.

The lightweight limiter is held in process memory and keyed by administrator
and source. It is appropriate for the current single-instance deployment. A
shared limiter is required before concurrent API replicas are supported.

SSH connectivity fails closed until an administrator reviews and explicitly
trusts the presented algorithm and SHA-256 fingerprint. Trust re-reads the host
identity from the configured endpoint. A changed identity blocks authentication
and requires another explicit trust action. Address or port changes clear trust.

SNMP checks use only standard system identity OIDs with the source's encrypted
SNMPv2c or SNMPv3 credential. Global SNMP environment credentials are not used
for management-source tests.

The Settings management forms keep secrets write-only. Secret fields are blank
for rotation and cleared after submission. Secret values are not placed in URLs,
storage, data attributes, or logs.
