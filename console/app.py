import ipaddress
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import hashlib
import secrets
import smtplib
import ssl
import urllib.error
import urllib.request
from contextlib import nullcontext
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from beacn.database import apply_migrations, initialise_schema
from beacn.database.schema import (
    initialise_auth_schema,
    initialise_password_recovery_schema,
    initialise_security_settings_schema,
)
from beacn.services.health import get_health_summary
from beacn.relationships.manager import RelationshipManager
from beacn.relationships.providers.generic import GenericProvider
from beacn.relationships.providers.infrastructure import InfrastructureProvider
from beacn.relationships.providers.manual import ManualProvider
from beacn.services.snmp import get_snmp_snapshot

from beacn.config import (
    AGENT_PORT,
    AGENT_TIMEOUT,
    APP_NAME,
    APP_PORT,
    APP_STAGE,
    APP_VERSION,
    COMMAND_TIMEOUT,
    DATA_DIR,
    IPERF_PORT,
    METRICS_INTERVAL_SECONDS,
    NETWORK_SUBNET,
    SCAN_TIMEOUT,
    TELEMETRY_MAX_POINTS,
    TELEMETRY_RETENTION_DAYS,
)

from beacn.services.scanner import (
    scan_network,
    collect_agent_metrics,
)

from beacn.services.commands import (
    normalize_target,
    run_command,
    valid_target,
)

from beacn.services.agent import (
    fetch_agent_json,
    fetch_agent_status,
)

from beacn.services.discovery import (
    parse_nmap_discovery,
    reverse_dns,
)

from beacn.services.telemetry import (
    prune_telemetry,
    save_telemetry,
    update_device_from_agent,
)
from beacn.services.docker_monitor import (
    docker_snapshot,
)
from beacn.web.api.monitoring import (
    create_monitoring_blueprint,
)
from beacn.web.api.operations import operations_blueprint

from beacn.runtime import (
    database,
    repository,
    scan_lock,
    db_write_lock,
    scan_state,
)

from beacn.common import (
    db,
    normalise_windows_name,
    utc_now,
)

def _load_or_create_secret_key():
    """
    Persist the Flask signing key under DATA_DIR so sessions
    survive container rebuilds without placing a secret in Git.
    """

    configured = str(
        os.environ.get(
            "BEACN_SECRET_KEY",
            "",
        )
    ).strip()

    if configured:
        return configured

    secret_path = (
        Path(DATA_DIR)
        / "beacn-secret-key"
    )

    secret_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if secret_path.exists():
        value = secret_path.read_text(
            encoding="utf-8"
        ).strip()

        if value:
            return value

    value = secrets.token_urlsafe(48)

    secret_path.write_text(
        value + "\n",
        encoding="utf-8",
    )

    try:
        secret_path.chmod(0o600)
    except OSError:
        pass

    return value


app = Flask(__name__)

app.secret_key = _load_or_create_secret_key()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        str(
            os.environ.get(
                "BEACN_SESSION_COOKIE_SECURE",
                "false",
            )
        ).strip().lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    ),
    SESSION_REFRESH_EACH_REQUEST=True,
)

app.permanent_session_lifetime = timedelta(
    hours=8
)

app.register_blueprint(
    create_monitoring_blueprint(
        get_health_summary=lambda: get_health_summary(),
        docker_snapshot=lambda: docker_snapshot(),
        fetch_agent_json=lambda target, path: fetch_agent_json(
            target,
            path,
        ),
        db=lambda: db(),
    )
)
app.register_blueprint(operations_blueprint)

def _auth_user_count():
    with db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM auth_users
            WHERE is_enabled = 1
        """).fetchone()

    return int(row["count"] or 0)


def _get_setting(
    key,
    default=None,
):
    with db() as conn:
        row = conn.execute("""
            SELECT value
            FROM app_settings
            WHERE key = ?
        """, (key,)).fetchone()

    if not row:
        return default

    return row["value"]


def _set_setting(
    key,
    value,
):
    now = datetime.now(
        timezone.utc
    ).isoformat()

    with db_write_lock:
        with db() as conn:
            conn.execute("""
                INSERT INTO app_settings (
                    key,
                    value,
                    updated_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(key)
                DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (
                key,
                str(value),
                now,
            ))

            conn.commit()


def _smtp_settings():
    return {
        "host": str(
            _get_setting(
                "smtp.host",
                "",
            )
            or ""
        ).strip(),

        "port": int(
            _get_setting(
                "smtp.port",
                "587",
            )
            or 587
        ),

        "security": str(
            _get_setting(
                "smtp.security",
                "starttls",
            )
            or "starttls"
        ).strip().lower(),

        "username": str(
            _get_setting(
                "smtp.username",
                "",
            )
            or ""
        ).strip(),

        "password": str(
            _get_setting(
                "smtp.password",
                "",
            )
            or ""
        ),

        "from_address": str(
            _get_setting(
                "smtp.from_address",
                "",
            )
            or ""
        ).strip(),

        "from_name": str(
            _get_setting(
                "smtp.from_name",
                "BEACN",
            )
            or "BEACN"
        ).strip(),

        "base_url": str(
            _get_setting(
                "smtp.base_url",
                "",
            )
            or ""
        ).strip().rstrip("/"),
    }


def _smtp_configured():
    settings = _smtp_settings()

    return bool(
        settings["host"]
        and settings["from_address"]
        and settings["base_url"]
    )


def _send_email(
    recipient,
    subject,
    body,
):
    settings = _smtp_settings()

    if not _smtp_configured():
        raise RuntimeError(
            "SMTP is not configured."
        )

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = (
        f'{settings["from_name"]} '
        f'<{settings["from_address"]}>'
    )
    message["To"] = recipient

    message.set_content(body)

    context = ssl.create_default_context()

    if settings["security"] == "ssl":
        smtp_class = smtplib.SMTP_SSL

        with smtp_class(
            settings["host"],
            settings["port"],
            timeout=15,
            context=context,
        ) as smtp:
            if settings["username"]:
                smtp.login(
                    settings["username"],
                    settings["password"],
                )

            smtp.send_message(message)

    else:
        with smtplib.SMTP(
            settings["host"],
            settings["port"],
            timeout=15,
        ) as smtp:

            if settings["security"] == "starttls":
                smtp.starttls(
                    context=context
                )

            if settings["username"]:
                smtp.login(
                    settings["username"],
                    settings["password"],
                )

            smtp.send_message(message)


def _hash_reset_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _create_password_reset(
    user_id,
):
    token = secrets.token_urlsafe(48)

    token_hash = _hash_reset_token(
        token
    )

    now_dt = datetime.now(
        timezone.utc
    )

    expires_dt = (
        now_dt
        + timedelta(minutes=30)
    )

    with db_write_lock:
        with db() as conn:

            conn.execute("""
                UPDATE auth_password_resets
                SET used_at = ?
                WHERE
                    user_id = ?
                    AND used_at IS NULL
            """, (
                now_dt.isoformat(),
                user_id,
            ))

            conn.execute("""
                INSERT INTO auth_password_resets (
                    user_id,
                    token_hash,
                    created_at,
                    expires_at,
                    remote_addr
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                token_hash,
                now_dt.isoformat(),
                expires_dt.isoformat(),
                _client_address(),
            ))

            conn.commit()

    return token


def _claim_password_reset_request(email):
    """Atomically reserve capacity for a password-reset request."""

    now_dt = datetime.now(
        timezone.utc
    )

    remote_cutoff = (
        now_dt
        - timedelta(minutes=15)
    ).isoformat()

    email_cutoff = (
        now_dt
        - timedelta(hours=1)
    ).isoformat()

    email_hash = hashlib.sha256(
        email.encode("utf-8")
    ).hexdigest()

    with db_write_lock:
        with db() as conn:
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            remote_count = conn.execute("""
                SELECT COUNT(*) AS count
                FROM auth_password_reset_requests
                WHERE
                    remote_addr = ?
                    AND created_at >= ?
            """, (
                _client_address(),
                remote_cutoff,
            )).fetchone()

            email_count = conn.execute("""
                SELECT COUNT(*) AS count
                FROM auth_password_reset_requests
                WHERE
                    email_hash = ?
                    AND created_at >= ?
            """, (
                email_hash,
                email_cutoff,
            )).fetchone()

            if (
                int(remote_count["count"] or 0) >= 3
                or int(email_count["count"] or 0) >= 3
            ):
                conn.rollback()
                return False

            conn.execute("""
                INSERT INTO auth_password_reset_requests (
                    email_hash,
                    remote_addr,
                    created_at
                )
                VALUES (?, ?, ?)
            """, (
                email_hash,
                _client_address(),
                now_dt.isoformat(),
            ))

            conn.commit()

    return True


def _session_timeout_hours():
    try:
        value = int(
            _get_setting(
                "security.session_timeout_hours",
                "8",
            )
        )
    except (TypeError, ValueError):
        value = 8

    return min(
        168,
        max(1, value),
    )


def _current_auth_user():
    user_id = session.get(
        "auth_user_id"
    )

    if not user_id:
        return None

    with db() as conn:
        row = conn.execute("""
            SELECT
                id,
                username,
                email,
                is_admin,
                is_enabled,
                session_version,
                created_at,
                updated_at,
                last_login_at
            FROM auth_users
            WHERE id = ?
        """, (user_id,)).fetchone()

    if not row:
        return None

    user = dict(row)

    if not user.get("is_enabled"):
        return None

    session_version = int(
        session.get(
            "auth_session_version",
            0,
        )
    )

    if session_version != int(
        user.get(
            "session_version",
            1,
        )
    ):
        session.clear()
        return None

    return user


def _csrf_token():
    token = session.get(
        "auth_csrf_token"
    )

    if not token:
        token = secrets.token_urlsafe(
            32
        )

        session[
            "auth_csrf_token"
        ] = token

    return token


def _valid_csrf():
    submitted = str(
        request.form.get(
            "_csrf",
            "",
        )
    )

    expected = str(
        session.get(
            "auth_csrf_token",
            "",
        )
    )

    return bool(
        submitted
        and expected
        and secrets.compare_digest(
            submitted,
            expected,
        )
    )


def _client_address():
    return str(
        request.remote_addr
        or "unknown"
    )


def _login_rate_limited():
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=15)
    ).isoformat()

    with db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS count
            FROM auth_login_events
            WHERE
                remote_addr = ?
                AND success = 0
                AND created_at >= ?
        """, (
            _client_address(),
            cutoff,
        )).fetchone()

    return int(
        row["count"] or 0
    ) >= 5


def _record_login_event(
    username,
    success,
):
    with db_write_lock:
        with db() as conn:
            conn.execute("""
                INSERT INTO auth_login_events (
                    username,
                    remote_addr,
                    success,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                username,
                _client_address(),
                int(bool(success)),
                datetime.now(
                    timezone.utc
                ).isoformat(),
            ))

            conn.commit()


@app.context_processor
def auth_template_context():
    return {
        "auth_user":
            _current_auth_user(),

        "auth_csrf_token":
            _csrf_token(),
    }


@app.before_request
def require_authentication():
    path = request.path

    if (
        path.startswith("/static/")
        or path in {
            "/login",
            "/logout",
            "/setup",
            "/forgot-password",
            "/reset-password",
        }
    ):
        return None

    has_users = (
        _auth_user_count() > 0
    )

    if not has_users:
        if path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": (
                    "BEACN administrator "
                    "setup is required."
                ),
                "setup_required": True,
            }), 401

        return redirect(
            url_for("auth_setup")
        )

    user = _current_auth_user()

    if user:
        app.permanent_session_lifetime = timedelta(
            hours=_session_timeout_hours()
        )

        session.permanent = True
        return None

    if path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": (
                "Authentication required."
            ),
        }), 401

    return redirect(
        url_for(
            "auth_login",
            next=request.full_path,
        )
    )


@app.route(
    "/setup",
    methods=["GET", "POST"],
)
def auth_setup():
    if _auth_user_count() > 0:
        return redirect(
            url_for("auth_login")
        )

    error = None

    if request.method == "POST":
        if not _valid_csrf():
            error = (
                "Security token expired. "
                "Please try again."
            )
        else:
            username = str(
                request.form.get(
                    "username",
                    "",
                )
            ).strip()

            email = str(
                request.form.get(
                    "email",
                    "",
                )
            ).strip().lower()

            password = str(
                request.form.get(
                    "password",
                    "",
                )
            )

            confirm = str(
                request.form.get(
                    "confirm_password",
                    "",
                )
            )

            if (
                len(username) < 3
                or len(username) > 64
            ):
                error = (
                    "Username must be between "
                    "3 and 64 characters."
                )

            elif (
                not email
                or "@" not in email
                or len(email) > 254
            ):
                error = (
                    "Enter a valid recovery "
                    "email address."
                )

            elif len(password) < 12:
                error = (
                    "Password must contain at "
                    "least 12 characters."
                )

            elif password != confirm:
                error = (
                    "Passwords do not match."
                )

            else:
                now = datetime.now(
                    timezone.utc
                ).isoformat()

                password_hash = (
                    generate_password_hash(
                        password,
                        method="scrypt",
                    )
                )

                with db_write_lock:
                    with db() as conn:
                        cursor = conn.execute("""
                            INSERT INTO auth_users (
                                username,
                                email,
                                password_hash,
                                is_admin,
                                is_enabled,
                                created_at,
                                updated_at
                            )
                            VALUES (?, ?, ?, 1, 1, ?, ?)
                        """, (
                            username,
                            email,
                            password_hash,
                            now,
                            now,
                        ))

                        user_id = (
                            cursor.lastrowid
                        )

                        conn.commit()

                session.clear()

                session[
                    "auth_user_id"
                ] = user_id

                session[
                    "auth_session_version"
                ] = 1

                session.permanent = True

                _csrf_token()

                return redirect(
                    url_for("index")
                )

    return render_template(
        "setup.html",
        error=error,
    )


@app.route(
    "/forgot-password",
    methods=["GET", "POST"],
)
def auth_forgot_password():
    message = None

    if request.method == "POST":
        email = str(
            request.form.get(
                "email",
                "",
            )
        ).strip().lower()

        if (
            _valid_csrf()
            and _claim_password_reset_request(
                email
            )
        ):

            with db() as conn:
                row = conn.execute("""
                    SELECT
                        id,
                        username,
                        email
                    FROM auth_users
                    WHERE
                        lower(email) = ?
                        AND is_enabled = 1
                """, (
                    email,
                )).fetchone()

            if (
                row
                and row["email"]
                and _smtp_configured()
            ):
                try:
                    token = (
                        _create_password_reset(
                            row["id"]
                        )
                    )

                    settings = (
                        _smtp_settings()
                    )

                    reset_url = (
                        f'{settings["base_url"]}'
                        f'/reset-password'
                        f'#token={token}'
                    )

                    body = (
                        "A password reset was requested "
                        "for your BEACN account.\n\n"
                        f"Username: {row['username']}\n\n"
                        "Reset your password using this link:\n"
                        f"{reset_url}\n\n"
                        "This link expires in 30 minutes "
                        "and can only be used once.\n\n"
                        "If you did not request this reset, "
                        "you can ignore this email."
                    )

                    _send_email(
                        row["email"],
                        "BEACN password reset",
                        body,
                    )

                except Exception:
                    app.logger.exception(
                        "Password reset email failed."
                    )

        # Deliberately identical response regardless
        # of account existence or SMTP state.
        message = (
            "If that email address is associated "
            "with an enabled BEACN account, "
            "a reset message will be sent."
        )

    return render_template(
        "forgot-password.html",
        message=message,
    )


@app.route(
    "/reset-password",
    methods=["GET", "POST"],
)
def auth_reset_password():
    token = ""

    valid_reset = (
        request.method == "GET"
    )

    if request.method == "POST":
        token = str(
            request.form.get(
                "token",
                "",
            )
        ).strip()

        valid_reset = None

    if request.method == "POST" and token:
        token_hash = (
            _hash_reset_token(token)
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        with db() as conn:
            valid_reset = conn.execute("""
                SELECT
                    r.id,
                    r.user_id,
                    r.expires_at,
                    r.used_at,
                    u.username
                FROM auth_password_resets r
                JOIN auth_users u
                    ON u.id = r.user_id
                WHERE
                    r.token_hash = ?
                    AND r.used_at IS NULL
                    AND r.expires_at > ?
                    AND u.is_enabled = 1
            """, (
                token_hash,
                now,
            )).fetchone()

    error = None

    if request.method == "POST":
        if not valid_reset:
            error = (
                "This password reset link "
                "is invalid or has expired."
            )

        elif not _valid_csrf():
            error = (
                "Security token expired. "
                "Please try again."
            )

        else:
            password = str(
                request.form.get(
                    "password",
                    "",
                )
            )

            confirm = str(
                request.form.get(
                    "confirm_password",
                    "",
                )
            )

            if len(password) < 12:
                error = (
                    "Password must contain "
                    "at least 12 characters."
                )

            elif password != confirm:
                error = (
                    "Passwords do not match."
                )

            else:
                password_hash = (
                    generate_password_hash(
                        password,
                        method="scrypt",
                    )
                )

                now = datetime.now(
                    timezone.utc
                ).isoformat()

                reset_consumed = False

                with db_write_lock:
                    with db() as conn:
                        conn.execute(
                            "BEGIN IMMEDIATE"
                        )

                        consumed = conn.execute("""
                            UPDATE auth_password_resets
                            SET used_at = ?
                            WHERE
                                id = ?
                                AND token_hash = ?
                                AND used_at IS NULL
                                AND expires_at > ?
                                AND EXISTS (
                                    SELECT 1
                                    FROM auth_users
                                    WHERE
                                        id = auth_password_resets.user_id
                                        AND is_enabled = 1
                                )
                        """, (
                            now,
                            valid_reset["id"],
                            token_hash,
                            now,
                        ))

                        if consumed.rowcount == 1:
                            updated = conn.execute("""
                                UPDATE auth_users
                                SET
                                    password_hash = ?,
                                    session_version =
                                        session_version + 1,
                                    updated_at = ?
                                WHERE
                                    id = ?
                                    AND is_enabled = 1
                            """, (
                                password_hash,
                                now,
                                valid_reset[
                                    "user_id"
                                ],
                            ))

                            if updated.rowcount == 1:
                                conn.commit()
                                reset_consumed = True
                            else:
                                conn.rollback()
                        else:
                            conn.rollback()

                if reset_consumed:
                    session.clear()

                    return redirect(
                        url_for(
                            "auth_login",
                            reset="success",
                        )
                    )

                error = (
                    "This password reset link "
                    "is invalid or has expired."
                )
                valid_reset = None

    return render_template(
        "reset-password.html",
        token=token,
        valid_reset=bool(valid_reset),
        error=error,
    )


@app.route(
    "/login",
    methods=["GET", "POST"],
)
def auth_login():
    if _auth_user_count() == 0:
        return redirect(
            url_for("auth_setup")
        )

    if _current_auth_user():
        return redirect(
            url_for("index")
        )

    error = None

    if request.method == "POST":
        if not _valid_csrf():
            error = (
                "Security token expired. "
                "Please try again."
            )

        elif _login_rate_limited():
            error = (
                "Too many failed attempts. "
                "Please wait 15 minutes."
            )

        else:
            username = str(
                request.form.get(
                    "username",
                    "",
                )
            ).strip()

            password = str(
                request.form.get(
                    "password",
                    "",
                )
            )

            with db() as conn:
                row = conn.execute("""
                    SELECT *
                    FROM auth_users
                    WHERE
                        username = ?
                        AND is_enabled = 1
                """, (
                    username,
                )).fetchone()

            valid = bool(
                row
                and check_password_hash(
                    row["password_hash"],
                    password,
                )
            )

            _record_login_event(
                username,
                valid,
            )

            if valid:
                session.clear()

                session[
                    "auth_user_id"
                ] = row["id"]

                session[
                    "auth_session_version"
                ] = int(
                    row["session_version"]
                    or 1
                )

                session.permanent = True

                _csrf_token()

                now = datetime.now(
                    timezone.utc
                ).isoformat()

                with db_write_lock:
                    with db() as conn:
                        conn.execute("""
                            UPDATE auth_users
                            SET
                                last_login_at = ?,
                                updated_at = ?
                            WHERE id = ?
                        """, (
                            now,
                            now,
                            row["id"],
                        ))

                        conn.commit()

                return redirect(
                    url_for("index")
                )

            error = (
                "Invalid username or password."
            )

    return render_template(
        "login.html",
        error=error,
    )


@app.post("/logout")
def auth_logout():
    if not _valid_csrf():
        return (
            "Invalid security token.",
            400,
        )

    user = _current_auth_user()

    if user:
        try:
            with db_write_lock:
                with db() as conn:
                    conn.execute(
                        "BEGIN IMMEDIATE"
                    )

                    updated = conn.execute("""
                        UPDATE auth_users
                        SET
                            session_version =
                                session_version + 1,
                            updated_at = ?
                        WHERE
                            id = ?
                            AND is_enabled = 1
                    """, (
                        datetime.now(
                            timezone.utc
                        ).isoformat(),
                        user["id"],
                    ))

                    if updated.rowcount != 1:
                        conn.rollback()
                        raise sqlite3.DatabaseError(
                            "Session invalidation failed."
                        )

                    conn.commit()

        except sqlite3.Error:
            app.logger.exception(
                "Logout session invalidation failed."
            )

            return (
                "Unable to log out. Please try again.",
                500,
            )

    session.clear()

    return redirect(
        url_for("auth_login")
    )


def init_db():
    with db_write_lock:
        with db() as conn:
            initialise_schema(conn)
            initialise_auth_schema(conn)
            initialise_security_settings_schema(conn)
            initialise_password_recovery_schema(conn)
            apply_migrations(conn)


@app.get("/")
def index():
    return render_template(
        "index.html",
        subnet=NETWORK_SUBNET,
        iperf_port=IPERF_PORT,
        app_name=APP_NAME,
        app_version=APP_VERSION,
        app_stage=APP_STAGE,
    )

@app.route(
    "/settings",
    methods=["GET", "POST"],
)
def settings_page():
    user = _current_auth_user()

    general_message = None
    general_error = None
    smtp_message = None
    smtp_error = None
    security_message = None
    security_error = None

    if request.method == "POST":
        if not _valid_csrf():
            general_error = (
                "Security token expired. "
                "Please try again."
            )

        else:
            action = str(
                request.form.get(
                    "action",
                    "",
                )
            ).strip()

            if action == "change_password":
                current_password = str(
                    request.form.get(
                        "current_password",
                        "",
                    )
                )

                new_password = str(
                    request.form.get(
                        "new_password",
                        "",
                    )
                )

                confirm_password = str(
                    request.form.get(
                        "confirm_password",
                        "",
                    )
                )

                with db() as conn:
                    row = conn.execute("""
                        SELECT *
                        FROM auth_users
                        WHERE id = ?
                    """, (
                        user["id"],
                    )).fetchone()

                if not row or not check_password_hash(
                    row["password_hash"],
                    current_password,
                ):
                    security_error = (
                        "Current password is incorrect."
                    )

                elif len(new_password) < 12:
                    security_error = (
                        "New password must contain "
                        "at least 12 characters."
                    )

                elif new_password != confirm_password:
                    security_error = (
                        "New passwords do not match."
                    )

                else:
                    now = datetime.now(
                        timezone.utc
                    ).isoformat()

                    password_hash = (
                        generate_password_hash(
                            new_password,
                            method="scrypt",
                        )
                    )

                    with db_write_lock:
                        with db() as conn:
                            conn.execute("""
                                UPDATE auth_users
                                SET
                                    password_hash = ?,
                                    updated_at = ?,
                                    session_version =
                                        session_version + 1
                                WHERE id = ?
                            """, (
                                password_hash,
                                now,
                                user["id"],
                            ))

                            conn.commit()

                    with db() as conn:
                        row = conn.execute("""
                            SELECT session_version
                            FROM auth_users
                            WHERE id = ?
                        """, (
                            user["id"],
                        )).fetchone()

                    session[
                        "auth_session_version"
                    ] = int(
                        row["session_version"]
                    )

                    security_message = (
                        "Password changed successfully."
                    )

            elif action == "smtp_settings":
                smtp_host = str(
                    request.form.get(
                        "smtp_host",
                        "",
                    )
                ).strip()

                smtp_port = str(
                    request.form.get(
                        "smtp_port",
                        "587",
                    )
                ).strip()

                smtp_security = str(
                    request.form.get(
                        "smtp_security",
                        "starttls",
                    )
                ).strip().lower()

                smtp_username = str(
                    request.form.get(
                        "smtp_username",
                        "",
                    )
                ).strip()

                smtp_password = str(
                    request.form.get(
                        "smtp_password",
                        "",
                    )
                )

                smtp_from_address = str(
                    request.form.get(
                        "smtp_from_address",
                        "",
                    )
                ).strip()

                smtp_from_name = str(
                    request.form.get(
                        "smtp_from_name",
                        "BEACN",
                    )
                ).strip()

                smtp_base_url = str(
                    request.form.get(
                        "smtp_base_url",
                        "",
                    )
                ).strip().rstrip("/")

                try:
                    smtp_port_value = int(
                        smtp_port
                    )
                except ValueError:
                    smtp_port_value = 0

                if (
                    not smtp_host
                    or smtp_port_value < 1
                    or smtp_port_value > 65535
                ):
                    smtp_error = (
                        "Enter a valid SMTP "
                        "host and port."
                    )

                elif smtp_security not in {
                    "none",
                    "starttls",
                    "ssl",
                }:
                    smtp_error = (
                        "Unsupported SMTP "
                        "security mode."
                    )

                elif (
                    not smtp_from_address
                    or "@" not in smtp_from_address
                ):
                    smtp_error = (
                        "Enter a valid sender "
                        "email address."
                    )

                elif not (
                    smtp_base_url.startswith(
                        "http://"
                    )
                    or smtp_base_url.startswith(
                        "https://"
                    )
                ):
                    smtp_error = (
                        "BEACN public URL must "
                        "begin with http:// or https://."
                    )

                else:
                    settings = {
                        "smtp.host":
                            smtp_host,

                        "smtp.port":
                            smtp_port_value,

                        "smtp.security":
                            smtp_security,

                        "smtp.username":
                            smtp_username,

                        "smtp.from_address":
                            smtp_from_address,

                        "smtp.from_name":
                            smtp_from_name or "BEACN",

                        "smtp.base_url":
                            smtp_base_url,
                    }

                    for key, value in settings.items():
                        _set_setting(
                            key,
                            value,
                        )

                    if smtp_password:
                        _set_setting(
                            "smtp.password",
                            smtp_password,
                        )

                    smtp_message = (
                        "SMTP settings saved."
                    )

            elif action == "smtp_test":
                destination = str(
                    request.form.get(
                        "test_email",
                        "",
                    )
                ).strip()

                if (
                    not destination
                    or "@" not in destination
                ):
                    smtp_error = (
                        "Enter a valid test "
                        "email address."
                    )

                else:
                    try:
                        smtp = _smtp_settings()

                        _send_email(
                            destination,
                            "BEACN · Email delivery test successful",
                            (
                                "BEACN\n"
                                "Network Intelligence\n\n"
                                "Email delivery test successful\n\n"
                                "This message confirms that BEACN can "
                                "send password recovery and security "
                                "notification emails through the "
                                "configured SMTP service.\n\n"
                                "Instance:\n"
                                f"{smtp.get('base_url', '')}\n\n"
                                "Recipient:\n"
                                f"{destination}\n\n"
                                "Status:\n"
                                "SMTP delivery operational\n\n"
                                "No action is required.\n\n"
                                "BEACN\n"
                                "Infrastructure discovery, diagnostics "
                                "and live device intelligence"
                            ),
                        )

                        smtp_message = (
                            "Test email sent."
                        )

                    except Exception as exc:
                        app.logger.exception(
                            "SMTP test failed."
                        )

                        smtp_error = (
                            "SMTP test failed: "
                            f"{exc}"
                        )

            elif action == "recovery_email":
                email = str(
                    request.form.get(
                        "recovery_email",
                        "",
                    )
                ).strip().lower()

                if (
                    not email
                    or "@" not in email
                    or len(email) > 254
                ):
                    security_error = (
                        "Enter a valid recovery "
                        "email address."
                    )

                else:
                    with db() as conn:
                        existing = conn.execute("""
                            SELECT id
                            FROM auth_users
                            WHERE
                                lower(email) = ?
                                AND id != ?
                        """, (
                            email,
                            user["id"],
                        )).fetchone()

                    if existing:
                        security_error = (
                            "That email address "
                            "is already in use."
                        )

                    else:
                        now = datetime.now(
                            timezone.utc
                        ).isoformat()

                        with db_write_lock:
                            with db() as conn:
                                conn.execute("""
                                    UPDATE auth_users
                                    SET
                                        email = ?,
                                        updated_at = ?
                                    WHERE id = ?
                                """, (
                                    email,
                                    now,
                                    user["id"],
                                ))

                                conn.commit()

                        security_message = (
                            "Recovery email updated."
                        )

            elif action == "session_timeout":
                raw_hours = str(
                    request.form.get(
                        "session_timeout_hours",
                        "",
                    )
                ).strip()

                try:
                    hours = int(raw_hours)
                except ValueError:
                    hours = 0

                if hours < 1 or hours > 168:
                    security_error = (
                        "Session timeout must be "
                        "between 1 and 168 hours."
                    )

                else:
                    _set_setting(
                        "security.session_timeout_hours",
                        hours,
                    )

                    security_message = (
                        "Session timeout updated."
                    )

            elif action == "logout_all":
                with db_write_lock:
                    with db() as conn:
                        conn.execute("""
                            UPDATE auth_users
                            SET
                                session_version =
                                    session_version + 1,
                                updated_at = ?
                            WHERE id = ?
                        """, (
                            datetime.now(
                                timezone.utc
                            ).isoformat(),
                            user["id"],
                        ))

                        conn.commit()

                session.clear()

                return redirect(
                    url_for("auth_login")
                )

    with db() as conn:
        login_rows = conn.execute("""
            SELECT
                username,
                remote_addr,
                success,
                created_at
            FROM auth_login_events
            ORDER BY id DESC
            LIMIT 25
        """).fetchall()

    login_events = [
        dict(row)
        for row in login_rows
    ]

    return render_template(
        "settings.html",
        user=_current_auth_user(),
        general_message=general_message,
        general_error=general_error,
        smtp_message=smtp_message,
        smtp_error=smtp_error,
        security_message=security_message,
        security_error=security_error,
        session_timeout_hours=(
            _session_timeout_hours()
        ),
        login_events=login_events,
        smtp_settings=_smtp_settings(),
        smtp_configured=_smtp_configured(),
    )


@app.get("/api/devices")
def devices():
    """Return canonical Device objects while preserving legacy UI fields."""
    canonical = {device.primary_ip: device for device in repository.list()}

    with db() as conn:
        rows = conn.execute("""
            SELECT
                id, ip, hostname, display_name, mac, vendor, is_online,
                iperf_available, first_seen, last_seen,
                agent_available, agent_version, agent_hostname,
                cpu_percent, memory_percent, uptime_seconds,
                agent_last_seen, os_name, os_version, device_type,
                device_type_source, connection_method,
                connection_parent_ip, connection_parent_ref,
                connection_source,
                management_url, notes
            FROM devices
            ORDER BY is_online DESC, ip
        """).fetchall()

    payload = []
    for row in rows:
        item = dict(row)
        device = canonical.get(row["ip"])
        if device:
            item["device_id"] = device.id
            item["primary_ip"] = device.primary_ip
            item["primary_mac"] = device.primary_mac
            item["agent_installed"] = device.agent_installed
        payload.append(item)

    def device_ip_sort_key(item):
        try:
            address = ipaddress.ip_address(
                str(item.get("ip", "")).strip()
            )
            return (
                address.version,
                int(address),
            )
        except ValueError:
            return (99, 0)

    payload.sort(key=device_ip_sort_key)

    with db() as conn:
        infrastructure_rows = conn.execute("""
            SELECT
                id,
                name,
                infrastructure_type,
                manufacturer,
                model,
                managed,
                port_count,
                location,
                management_url,
                notes,
                parent_ref,
                connection_method,
                interfaces_json,
                created_at,
                updated_at
            FROM infrastructure_objects
            ORDER BY name COLLATE NOCASE
        """).fetchall()

    infrastructure = []

    for row in infrastructure_rows:
        item = dict(row)

        try:
            item["interfaces"] = (
                json.loads(
                    item.pop("interfaces_json")
                )
                if item.get("interfaces_json")
                else []
            )
        except json.JSONDecodeError:
            item["interfaces"] = []
            item.pop("interfaces_json", None)

        item["managed"] = (
            None
            if item["managed"] is None
            else bool(item["managed"])
        )

        item["object_kind"] = "infrastructure"
        item["ref"] = f"infra:{item['id']}"

        infrastructure.append(item)

    return jsonify({
        "devices": payload,
        "infrastructure": infrastructure,
        "scan": scan_state,
        "subnet": NETWORK_SUBNET,
    })




def _relationship_context():
    """
    Build the normalized inventory consumed by Relationship
    Evidence Providers.

    This intentionally contains no provider-specific logic.
    """

    with db() as conn:
        device_rows = conn.execute("""
            SELECT
                id,
                ip,
                hostname,
                display_name,
                mac,
                vendor,
                is_online,
                agent_available,
                device_type,
                device_type_source,
                connection_method,
                connection_parent_ip,
                connection_parent_ref,
                connection_source,
                management_url,
                notes
            FROM devices
            ORDER BY ip
        """).fetchall()

        infrastructure_rows = conn.execute("""
            SELECT *
            FROM infrastructure_objects
            ORDER BY name COLLATE NOCASE
        """).fetchall()

    devices_payload = [
        dict(row)
        for row in device_rows
    ]

    infrastructure_payload = []

    for row in infrastructure_rows:
        item = dict(row)

        raw_interfaces = item.pop(
            "interfaces_json",
            None,
        )

        try:
            item["interfaces"] = (
                json.loads(
                    raw_interfaces
                )
                if raw_interfaces
                else []
            )
        except json.JSONDecodeError:
            item["interfaces"] = []

        item["managed"] = (
            None
            if item["managed"] is None
            else bool(
                item["managed"]
            )
        )

        item["object_kind"] = (
            "infrastructure"
        )

        item["ref"] = (
            f"infra:{item['id']}"
        )

        infrastructure_payload.append(
            item
        )

    return {
        "devices": devices_payload,
        "infrastructure":
            infrastructure_payload,
    }


@app.get("/api/relationships")
def relationship_intelligence():
    context = _relationship_context()

    manager = RelationshipManager()

    manager.register(
        InfrastructureProvider()
    )

    manager.register(
        ManualProvider()
    )

    manager.register(
        GenericProvider()
    )

    relationships = manager.evaluate(
        context
    )

    # relationship.evidence already contains every candidate
    # gathered for that subject, so providers do not need to be
    # executed a second time merely to count evidence.

    evidence = [
        item
        for relationship in relationships
        for item in relationship.evidence
    ]

    provider_labels = {
        "generic": "Generic inference",
        "infrastructure": "Infrastructure hierarchy",
        "manual": "Manual override",
        "snmp": "SNMP",
        "wireless": "Wireless association",
        "agent": "BEACN Agent",
        "lldp": "LLDP",
        "cdp": "CDP",
    }

    reason_labels = {
        "strong_wired_endpoint":
            "Strong wired endpoint evidence",

        "single_distribution_switch":
            "Single known distribution switch",

        "single_known_isp_gateway":
            "Single known ISP gateway",

        "configured_infrastructure_parent":
            "Configured infrastructure hierarchy",

        "manual_override":
            "Manual topology override",

        "wireless_association":
            "Wireless client association",

        "bridge_fdb":
            "Switch forwarding table",

        "lldp_neighbor":
            "LLDP neighbour",

        "agent_network":
            "Agent network evidence",
    }

    # ---------------------------------------------------------
    # Build one lookup table for every object participating in
    # the topology graph.
    # ---------------------------------------------------------

    object_index = {}

    for device in context["devices"]:
        ip = str(
            device.get("ip", "")
        ).strip()

        if not ip:
            continue

        ref = f"device:{ip}"

        name = (
            device.get("display_name")
            or device.get("hostname")
            or ip
        )

        evidence_text = " ".join(
            str(value or "")
            for value in (
                device.get("display_name"),
                device.get("hostname"),
            )
        ).lower()

        is_core_service = (
            "pihole" in evidence_text
            or "pi-hole" in evidence_text
        )

        object_index[ref] = {
            "ref": ref,
            "id": device.get("id"),
            "object_kind": "device",
            "name": name,
            "ip": ip,
            "hostname":
                device.get("hostname"),
            "device_type":
                device.get("device_type")
                or "unknown",
            "vendor":
                device.get("vendor")
                or "",
            "is_online":
                bool(
                    device.get("is_online")
                ),
            "agent_available":
                bool(
                    device.get(
                        "agent_available"
                    )
                ),
            "presentation_role": (
                "core_service"
                if is_core_service
                else (
                    "access_point"
                    if device.get(
                        "device_type"
                    ) == "access_point"
                    else "endpoint"
                )
            ),
        }

    for item in context["infrastructure"]:
        ref = str(
            item.get("ref", "")
        ).strip()

        if not ref:
            continue

        interfaces = (
            item.get("interfaces")
            if isinstance(
                item.get("interfaces"),
                list,
            )
            else []
        )

        primary_ip = next(
            (
                str(interface.get("address"))
                for interface in interfaces
                if isinstance(
                    interface,
                    dict,
                )
                and interface.get("address")
            ),
            None,
        )

        object_index[ref] = {
            "ref": ref,
            "id": item.get("id"),
            "object_kind":
                "infrastructure",
            "name":
                item.get("name")
                or ref,
            "ip": primary_ip,
            "infrastructure_type":
                item.get(
                    "infrastructure_type"
                )
                or "other",
            "manufacturer":
                item.get("manufacturer")
                or "",
            "model":
                item.get("model")
                or "",
            "managed":
                item.get("managed"),
            "location":
                item.get("location"),
            "presentation_role":
                "infrastructure",
        }

    def object_payload(ref):
        if not ref:
            return None

        item = object_index.get(ref)

        if item:
            return dict(item)

        return {
            "ref": ref,
            "object_kind": "unknown",
            "name": ref,
            "presentation_role": "unknown",
        }

    # ---------------------------------------------------------
    # Relationships
    # ---------------------------------------------------------

    relationship_payload = []
    resolved_device_refs = set()

    provider_relationship_counts = {}

    for relationship in relationships:
        if relationship.subject_ref.startswith(
            "device:"
        ):
            resolved_device_refs.add(
                relationship.subject_ref
            )

        provider_relationship_counts[
            relationship.provider
        ] = (
            provider_relationship_counts.get(
                relationship.provider,
                0,
            )
            + 1
        )

        subject = object_payload(
            relationship.subject_ref
        )

        parent = object_payload(
            relationship.parent_ref
        )

        relationship_payload.append({
            "resolved": True,

            "resolution_status":
                "resolved",

            "subject_ref":
                relationship.subject_ref,

            "parent_ref":
                relationship.parent_ref,

            "subject": subject,
            "parent": parent,

            "subject_id": (
                subject.get("id")
                if subject
                else None
            ),

            "subject_kind": (
                subject.get("object_kind")
                if subject
                else "unknown"
            ),

            "parent_id": (
                parent.get("id")
                if parent
                else None
            ),

            "parent_kind": (
                parent.get("object_kind")
                if parent
                else "unknown"
            ),

            "transport":
                relationship.transport,

            "provider":
                relationship.provider,

            "provider_label":
                provider_labels.get(
                    relationship.provider,
                    relationship.provider.replace(
                        "_",
                        " ",
                    ).title(),
                ),

            "confidence":
                relationship.confidence,

            "reason":
                relationship.reason,

            "reason_label":
                reason_labels.get(
                    relationship.reason,
                    relationship.reason.replace(
                        "_",
                        " ",
                    ).title(),
                ),

            "placement": (
                "manual"
                if relationship.provider
                == "manual"
                else "automatic"
            ),

            "evidence": [
                {
                    "provider":
                        item.provider,

                    "provider_label":
                        provider_labels.get(
                            item.provider,
                            item.provider.replace(
                                "_",
                                " ",
                            ).title(),
                        ),

                    "parent_ref":
                        item.parent_ref,

                    "parent":
                        object_payload(
                            item.parent_ref
                        ),

                    "transport":
                        item.transport,

                    "confidence":
                        item.confidence,

                    "reason":
                        item.reason,

                    "reason_label":
                        reason_labels.get(
                            item.reason,
                            item.reason.replace(
                                "_",
                                " ",
                            ).title(),
                        ),
                }
                for item
                in relationship.evidence
            ],
        })

    # ---------------------------------------------------------
    # Unresolved devices
    # ---------------------------------------------------------

    all_device_refs = {
        ref
        for ref, item
        in object_index.items()
        if item.get("object_kind")
        == "device"
    }

    unresolved_refs = sorted(
        all_device_refs
        - resolved_device_refs
    )

    unresolved = [
        object_payload(ref)
        for ref in unresolved_refs
    ]

    diagnostics_by_subject = {}

    for diagnostic in manager.diagnostics:
        subject_ref = diagnostic.get("subject_ref")

        if subject_ref:
            diagnostics_by_subject.setdefault(
                subject_ref,
                [],
            ).append(diagnostic)

    def decorate_unresolved(item):
        item_diagnostics = (
            diagnostics_by_subject.get(
                item["ref"],
                [],
            )
        )

        diagnostic_codes = {
            diagnostic.get("code")
            for diagnostic in item_diagnostics
        }

        if "incomplete_manual" in diagnostic_codes:
            resolution_status = "invalid_manual"
        elif "ambiguous_tie" in diagnostic_codes:
            resolution_status = "ambiguous"
        elif diagnostic_codes & {
            "cycle_rejected",
            "self_parent",
        }:
            resolution_status = "graph_rejected"
        elif "invalid_parent" in diagnostic_codes:
            resolution_status = (
                "invalid_manual"
                if any(
                    diagnostic.get("provider")
                    == "manual"
                    for diagnostic
                    in item_diagnostics
                )
                else "invalid_parent"
            )
        else:
            resolution_status = "no_evidence"

        item["resolved"] = False
        item["resolution_status"] = (
            resolution_status
        )
        item["resolution_diagnostics"] = (
            item_diagnostics
        )

        return item

    unresolved = [
        decorate_unresolved(item)
        for item in unresolved
    ]

    resolved_refs = {
        item["subject_ref"]
        for item in relationship_payload
    }

    unresolved_infrastructure = []

    for infrastructure_item in context["infrastructure"]:
        subject_ref = infrastructure_item.get("ref")
        parent_ref = infrastructure_item.get("parent_ref")

        if (
            not subject_ref
            or not parent_ref
            or subject_ref in resolved_refs
        ):
            continue

        item = object_payload(subject_ref)
        item["intended_parent_ref"] = parent_ref
        unresolved_infrastructure.append(
            decorate_unresolved(item)
        )

    unresolved_relationships = sorted(
        [
            *unresolved,
            *unresolved_infrastructure,
        ],
        key=lambda item: item["ref"],
    )

    unresolved_endpoints = [
        item
        for item in unresolved
        if item.get(
            "presentation_role"
        ) == "endpoint"
    ]

    unresolved_access_points = [
        item
        for item in unresolved
        if item.get(
            "presentation_role"
        ) == "access_point"
    ]

    unresolved_core_services = [
        item
        for item in unresolved
        if item.get(
            "presentation_role"
        ) == "core_service"
    ]

    # ---------------------------------------------------------
    # Provider health / counters
    # ---------------------------------------------------------

    provider_evidence_counts = {}

    for item in evidence:
        provider_evidence_counts[
            item.provider
        ] = (
            provider_evidence_counts.get(
                item.provider,
                0,
            )
            + 1
        )

    providers = []

    for provider in manager.providers:
        providers.append({
            "name":
                provider.name,

            "label":
                provider_labels.get(
                    provider.name,
                    provider.name.replace(
                        "_",
                        " ",
                    ).title(),
                ),

            "status":
                "healthy",

            "evidence_count":
                provider_evidence_counts.get(
                    provider.name,
                    0,
                ),

            "relationship_count":
                provider_relationship_counts.get(
                    provider.name,
                    0,
                ),
        })

    infrastructure_count = sum(
        1
        for item in object_index.values()
        if item.get("object_kind")
        == "infrastructure"
    )

    return jsonify({
        "ok": True,

        "engine": {
            "name":
                "BEACN Relationship Manager",

            "mode":
                "evidence_driven",

            "status":
                "healthy",
        },

        "summary": {
            "relationships":
                len(
                    relationship_payload
                ),

            "device_relationships":
                len(
                    resolved_device_refs
                ),

            "infrastructure_relationships":
                sum(
                    1
                    for item
                    in relationship_payload
                    if item[
                        "subject"
                    ].get(
                        "object_kind"
                    )
                    == "infrastructure"
                ),

            "unresolved_devices":
                len(
                    unresolved
                ),

            "unresolved_endpoints":
                len(
                    unresolved_endpoints
                ),

            "unresolved_access_points":
                len(
                    unresolved_access_points
                ),

            "core_services_without_parent":
                len(
                    unresolved_core_services
                ),

            "infrastructure_objects":
                infrastructure_count,

            "providers":
                len(
                    manager.providers
                ),

            "evidence_items":
                len(
                    evidence
                ),
        },

        "providers":
            providers,

        "relationships":
            relationship_payload,

        "unresolved":
            unresolved,

        "unresolved_relationships":
            unresolved_relationships,

        "diagnostics":
            manager.diagnostics,
    })



INFRASTRUCTURE_TYPES = {
    "internet",
    "isp_gateway",
    "router",
    "firewall",
    "switch",
    "access_point",
    "patch_panel",
    "ups",
    "rack",
    "poe_injector",
    "other",
}


def _infrastructure_payload(row):
    item = dict(row)

    raw_interfaces = item.pop(
        "interfaces_json",
        None,
    )

    try:
        item["interfaces"] = (
            json.loads(raw_interfaces)
            if raw_interfaces
            else []
        )
    except json.JSONDecodeError:
        item["interfaces"] = []

    item["managed"] = (
        None
        if item["managed"] is None
        else bool(item["managed"])
    )

    item["object_kind"] = "infrastructure"
    item["ref"] = f"infra:{item['id']}"

    return item


@app.get("/api/infrastructure")
def infrastructure_list():
    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM infrastructure_objects
            ORDER BY name COLLATE NOCASE
        """).fetchall()

    return jsonify({
        "ok": True,
        "infrastructure": [
            _infrastructure_payload(row)
            for row in rows
        ],
    })


@app.post("/api/infrastructure")
def infrastructure_create():
    payload = request.get_json(
        silent=True
    ) or {}

    name = str(
        payload.get("name", "")
    ).strip()

    infrastructure_type = str(
        payload.get(
            "infrastructure_type",
            "other",
        )
    ).strip().lower()

    manufacturer = str(
        payload.get("manufacturer", "")
    ).strip()

    model = str(
        payload.get("model", "")
    ).strip()

    location = str(
        payload.get("location", "")
    ).strip()

    management_url = str(
        payload.get("management_url", "")
    ).strip()

    notes = str(
        payload.get("notes", "")
    ).strip()

    parent_ref = str(
        payload.get("parent_ref", "")
    ).strip()

    connection_method = str(
        payload.get(
            "connection_method",
            "wired",
        )
    ).strip().lower()

    interfaces = payload.get(
        "interfaces",
        [],
    )

    managed_raw = payload.get(
        "managed",
        None,
    )

    managed = (
        None
        if managed_raw is None
        else int(bool(managed_raw))
    )

    port_count = payload.get(
        "port_count",
        None,
    )

    if not name:
        return jsonify({
            "ok": False,
            "error": "Infrastructure name is required.",
        }), 400

    if len(name) > 120:
        return jsonify({
            "ok": False,
            "error": "Infrastructure name is too long.",
        }), 400

    if infrastructure_type not in INFRASTRUCTURE_TYPES:
        return jsonify({
            "ok": False,
            "error": "Unsupported infrastructure type.",
        }), 400

    if connection_method not in {
        "wired",
        "wireless",
        "virtual",
        "unknown",
    }:
        return jsonify({
            "ok": False,
            "error": "Unsupported connection method.",
        }), 400

    if (
        management_url
        and not (
            management_url.startswith("http://")
            or management_url.startswith("https://")
        )
    ):
        return jsonify({
            "ok": False,
            "error": (
                "Management URL must begin with "
                "http:// or https://."
            ),
        }), 400

    if not isinstance(interfaces, list):
        return jsonify({
            "ok": False,
            "error": "Interfaces must be a list.",
        }), 400

    if port_count not in (None, ""):
        try:
            port_count = int(port_count)
        except (TypeError, ValueError):
            return jsonify({
                "ok": False,
                "error": "Port count must be numeric.",
            }), 400

        if port_count < 0 or port_count > 4096:
            return jsonify({
                "ok": False,
                "error": "Port count is outside the allowed range.",
            }), 400
    else:
        port_count = None

    object_id = str(uuid4())
    now = utc_now()

    with db_write_lock:
        with db() as conn:
            conn.execute("""
                INSERT INTO infrastructure_objects (
                    id,
                    name,
                    infrastructure_type,
                    manufacturer,
                    model,
                    managed,
                    port_count,
                    location,
                    management_url,
                    notes,
                    parent_ref,
                    connection_method,
                    interfaces_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                object_id,
                name,
                infrastructure_type,
                manufacturer or None,
                model or None,
                managed,
                port_count,
                location or None,
                management_url or None,
                notes or None,
                parent_ref or None,
                connection_method,
                json.dumps(
                    interfaces,
                    separators=(",", ":"),
                ),
                now,
                now,
            ))

            row = conn.execute(
                """
                SELECT *
                FROM infrastructure_objects
                WHERE id = ?
                """,
                (object_id,),
            ).fetchone()

            conn.commit()

    return jsonify({
        "ok": True,
        "infrastructure":
            _infrastructure_payload(row),
    }), 201


@app.patch("/api/infrastructure/<object_id>")
def infrastructure_update(object_id):
    payload = request.get_json(
        silent=True
    ) or {}

    allowed = {
        "name",
        "infrastructure_type",
        "manufacturer",
        "model",
        "managed",
        "port_count",
        "location",
        "management_url",
        "notes",
        "parent_ref",
        "connection_method",
        "interfaces",
    }

    updates = {
        key: value
        for key, value in payload.items()
        if key in allowed
    }

    if not updates:
        return jsonify({
            "ok": False,
            "error": "No supported fields supplied.",
        }), 400

    with db_write_lock:
        with db() as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM infrastructure_objects
                WHERE id = ?
                """,
                (object_id,),
            ).fetchone()

            if not existing:
                return jsonify({
                    "ok": False,
                    "error": (
                        "Infrastructure object not found."
                    ),
                }), 404

            current = dict(existing)

            for key, value in updates.items():
                if key == "interfaces":
                    current["interfaces_json"] = (
                        json.dumps(
                            value or [],
                            separators=(",", ":"),
                        )
                    )
                    continue

                current[key] = value

            if (
                current["infrastructure_type"]
                not in INFRASTRUCTURE_TYPES
            ):
                return jsonify({
                    "ok": False,
                    "error": (
                        "Unsupported infrastructure type."
                    ),
                }), 400

            current["managed"] = (
                None
                if current["managed"] is None
                else int(bool(current["managed"]))
            )

            current["updated_at"] = utc_now()

            conn.execute("""
                UPDATE infrastructure_objects
                SET
                    name = ?,
                    infrastructure_type = ?,
                    manufacturer = ?,
                    model = ?,
                    managed = ?,
                    port_count = ?,
                    location = ?,
                    management_url = ?,
                    notes = ?,
                    parent_ref = ?,
                    connection_method = ?,
                    interfaces_json = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                current["name"],
                current["infrastructure_type"],
                current["manufacturer"],
                current["model"],
                current["managed"],
                current["port_count"],
                current["location"],
                current["management_url"],
                current["notes"],
                current["parent_ref"],
                current["connection_method"],
                current["interfaces_json"],
                current["updated_at"],
                object_id,
            ))

            row = conn.execute(
                """
                SELECT *
                FROM infrastructure_objects
                WHERE id = ?
                """,
                (object_id,),
            ).fetchone()

            conn.commit()

    return jsonify({
        "ok": True,
        "infrastructure":
            _infrastructure_payload(row),
    })


@app.get("/api/device-types")
def device_type_summary():
    """Return inventory totals grouped by classified device type."""
    with db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(
                    NULLIF(device_type, ''),
                    'unknown'
                ) AS device_type,
                COUNT(*) AS total
            FROM devices
            GROUP BY device_type
            ORDER BY total DESC, device_type
        """).fetchall()

    types = [
        {
            "device_type": row["device_type"],
            "total": row["total"],
        }
        for row in rows
    ]

    with db() as conn:
        status = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN is_online = 1 THEN 1
                        ELSE 0
                    END
                ) AS online
            FROM devices
        """).fetchone()

    total = int(status["total"] or 0)
    online = int(status["online"] or 0)

    return jsonify({
        "total": total,
        "online": online,
        "offline": max(0, total - online),
        "types": types,
    })


DEVICE_TYPES = {
    "access_point",
    "appliance",
    "camera",
    "computer",
    "doorbell",
    "game_console",
    "iot",
    "media_tuner",
    "nas",
    "phone",
    "raspberry_pi",
    "router",
    "speaker",
    "switch",
    "television",
    "unknown",
    "ups",
}


@app.post("/api/device/<target>/identity")
def update_device_identity(target):
    """Store a manually managed friendly name and device type."""
    if not valid_target(target):
        return jsonify({
            "ok": False,
            "error": "Invalid target.",
        }), 400

    payload = request.get_json(silent=True) or {}

    display_name = str(
        payload.get("display_name", "")
    ).strip()

    device_type = str(
        payload.get("device_type", "")
    ).strip().lower()

    connection_method = str(
        payload.get("connection_method", "automatic")
    ).strip().lower()

    connection_parent_ip = str(
        payload.get("connection_parent_ip", "")
    ).strip()

    connection_parent_ref = str(
        payload.get("connection_parent_ref", "")
    ).strip()

    management_url = str(
        payload.get("management_url", "")
    ).strip()

    notes = str(
        payload.get("notes", "")
    ).strip()

    if len(display_name) > 100:
        return jsonify({
            "ok": False,
            "error": "Friendly name must be 100 characters or fewer.",
        }), 400

    if device_type not in DEVICE_TYPES:
        return jsonify({
            "ok": False,
            "error": "Unsupported device type.",
        }), 400

    if len(management_url) > 500:
        return jsonify({
            "ok": False,
            "error": "Management URL must be 500 characters or fewer.",
        }), 400

    if (
        management_url
        and not (
            management_url.startswith("http://")
            or management_url.startswith("https://")
        )
    ):
        return jsonify({
            "ok": False,
            "error": (
                "Management URL must begin with "
                "http:// or https://."
            ),
        }), 400

    if len(notes) > 2000:
        return jsonify({
            "ok": False,
            "error": "Notes must be 2000 characters or fewer.",
        }), 400

    allowed_connection_methods = {
        "automatic",
        "wired",
        "wireless",
        "unknown",
    }

    if connection_method not in allowed_connection_methods:
        return jsonify({
            "ok": False,
            "error": "Unsupported connection method.",
        }), 400

    if connection_method == "automatic":
        connection_parent_ip = ""
        connection_parent_ref = ""

    # Backward compatibility:
    # existing manually assigned devices still use parent IP.
    if connection_parent_ip and not connection_parent_ref:
        connection_parent_ref = (
            f"device:{connection_parent_ip}"
        )

    if connection_parent_ip and not valid_target(connection_parent_ip):
        return jsonify({
            "ok": False,
            "error": "Invalid parent device.",
        }), 400

    if connection_parent_ref.startswith("infra:"):
        infrastructure_id = (
            connection_parent_ref.split(
                ":",
                1,
            )[1]
        )

        with db() as conn:
            infrastructure_parent = conn.execute(
                """
                SELECT id
                FROM infrastructure_objects
                WHERE id = ?
                """,
                (infrastructure_id,),
            ).fetchone()

        if not infrastructure_parent:
            return jsonify({
                "ok": False,
                "error": (
                    "Infrastructure parent was not found."
                ),
            }), 400

        # An infrastructure parent replaces the legacy IP parent.
        connection_parent_ip = ""

    if connection_parent_ip == target:
        return jsonify({
            "ok": False,
            "error": "A device cannot connect through itself.",
        }), 400

    with db_write_lock:
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM devices WHERE ip = ?",
                (target,),
            ).fetchone()

            if not row:
                return jsonify({
                    "ok": False,
                    "error": "Device not found.",
                }), 404

            if connection_parent_ip:
                parent = conn.execute("""
                    SELECT
                        ip,
                        device_type
                    FROM devices
                    WHERE ip = ?
                """, (
                    connection_parent_ip,
                )).fetchone()

                if not parent:
                    return jsonify({
                        "ok": False,
                        "error": "Parent device was not found.",
                    }), 400

                if parent["device_type"] not in {
                    "router",
                    "switch",
                    "access_point",
                }:
                    return jsonify({
                        "ok": False,
                        "error": (
                            "Parent must be a router, switch, "
                            "or access point."
                        ),
                    }), 400

            conn.execute("""
                UPDATE devices
                SET display_name = ?,
                    device_type = ?,
                    device_type_source = 'manual',
                    connection_method = ?,
                    connection_parent_ip = NULLIF(?, ''),
                    connection_parent_ref = NULLIF(?, ''),
                    connection_source = CASE
                        WHEN ? = 'automatic'
                        THEN 'inferred'
                        ELSE 'manual'
                    END,
                    management_url = NULLIF(?, ''),
                    notes = NULLIF(?, '')
                WHERE ip = ?
            """, (
                display_name,
                device_type,
                connection_method,
                connection_parent_ip,
                connection_parent_ref,
                connection_method,
                management_url,
                notes,
                target,
            ))

            updated = conn.execute(
                """
                SELECT
                    id,
                    ip,
                    hostname,
                    display_name,
                    device_type,
                    device_type_source,
                    connection_method,
                    connection_parent_ip,
                    connection_parent_ref,
                    connection_source,
                    management_url,
                    notes
                FROM devices
                WHERE ip = ?
                """,
                (target,),
            ).fetchone()

            conn.commit()

    return jsonify({
        "ok": True,
        "device": dict(updated),
    })


@app.get("/api/devices/<device_id>")
def canonical_device_details(device_id):
    device = repository.get(device_id)
    if not device:
        return jsonify({"ok": False, "error": "Device not found."}), 404

    return jsonify({
        "ok": True,
        "device": device.to_dict(),
        "observations": list(repository.observations(device.id, limit=100)),
    })


@app.get("/api/device/<target>")
def device_details(target):
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Invalid target."}), 400

    refresh = request.args.get("refresh", "0") == "1"
    fresh_agent = (
        normalise_windows_name(fetch_agent_status(target))
        if refresh
        else None
    )

    database_guard = db_write_lock if fresh_agent else nullcontext()

    with database_guard:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE ip = ?",
                (target,),
            ).fetchone()

            if not row:
                return jsonify({"ok": False, "error": "Device not found."}), 404

            if fresh_agent:
                now = utc_now()
                update_device_from_agent(conn, target, fresh_agent, now)
                prune_telemetry(conn)
                row = conn.execute(
                    "SELECT * FROM devices WHERE ip = ?",
                    (target,),
                ).fetchone()

            agent_payload = None
            if row["agent_payload"]:
                try:
                    agent_payload = normalise_windows_name(
                        json.loads(row["agent_payload"])
                    )
                except json.JSONDecodeError:
                    agent_payload = None

    snmp_payload = get_snmp_snapshot(
        target
    )

    return jsonify({
        "ok": True,
        "device": {
            key: row[key]
            for key in row.keys()
            if key != "agent_payload"
        },
        "agent": agent_payload,
        "snmp": snmp_payload,
    })


@app.get("/api/telemetry/<target>")
def telemetry(target):
    if not valid_target(target):
        return jsonify({"ok": False, "error": "Invalid target."}), 400

    ranges = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
    selected_range = request.args.get("range", "1h")
    hours = ranges.get(selected_range, 1)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")

    try:
        requested_limit = int(request.args.get("limit", str(TELEMETRY_MAX_POINTS)))
    except ValueError:
        requested_limit = TELEMETRY_MAX_POINTS
    limit = max(10, min(requested_limit, TELEMETRY_MAX_POINTS))

    with db() as conn:
        rows = conn.execute("""
            SELECT cpu_percent, memory_percent, memory_available_bytes,
                   uptime_seconds, cpu_temperature_c, cpu_power_w,
                   cpu_clock_mhz, gpu_load_percent, gpu_temperature_c,
                   gpu_power_w, created_at
            FROM telemetry_history
            WHERE target_ip = ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
        """, (target, cutoff, limit)).fetchall()

    return jsonify({
        "ok": True,
        "target": target,
        "range": selected_range,
        "interval_seconds": METRICS_INTERVAL_SECONDS,
        "points": [dict(row) for row in reversed(rows)],
    })


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scan_network, daemon=True).start()
    threading.Thread(target=collect_agent_metrics, daemon=True).start()
    app.run(host="0.0.0.0", port=APP_PORT, threaded=True)
