import hashlib
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "console"))

_IMPORT_DATA = tempfile.TemporaryDirectory(
    prefix="beacn-auth-tests-"
)
os.environ["DATA_DIR"] = _IMPORT_DATA.name
os.environ["BEACN_SECRET_KEY"] = "test-only-secret-key"
os.environ["DOCKER_MONITORING_ENABLED"] = "false"
os.environ["NETWORK_SUBNET"] = "192.0.2.25/24"

import app as beacn_app  # noqa: E402
from beacn.database import Database  # noqa: E402
from beacn.services import health as health_service  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    database = Database(tmp_path / "beacn.db")
    monkeypatch.setattr(
        beacn_app,
        "db",
        database.connect,
    )
    monkeypatch.setattr(
        health_service,
        "db",
        database.connect,
    )

    beacn_app.app.config.update(
        TESTING=True,
        SESSION_COOKIE_SECURE=False,
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    beacn_app.app.permanent_session_lifetime = (
        timedelta(hours=8)
    )
    beacn_app.init_db()

    return beacn_app.app


def create_user(password="SyntheticPassword!"):
    now = datetime.now(timezone.utc).isoformat()

    with beacn_app.db() as conn:
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
            "synthetic",
            "synthetic@example.invalid",
            generate_password_hash(
                password,
                method="scrypt",
            ),
            now,
            now,
        ))
        conn.commit()

    return cursor.lastrowid


def csrf_from_html(response):
    match = re.search(
        r'name="_csrf"\s+value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match
    return match.group(1)


def login(client, password="SyntheticPassword!"):
    csrf = csrf_from_html(client.get("/login"))
    response = client.post(
        "/login",
        data={
            "_csrf": csrf,
            "username": "synthetic",
            "password": password,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return client.get_cookie("session").value


def clone_client(app, cookie):
    client = app.test_client()
    client.set_cookie("session", cookie)
    return client


def response_cookie(response):
    for header in response.headers.getlist(
        "Set-Cookie"
    ):
        parsed = SimpleCookie()
        parsed.load(header)

        if (
            "session" in parsed
            and parsed["session"].value
        ):
            return parsed["session"].value

    return None


def session_version(user_id):
    with beacn_app.db() as conn:
        row = conn.execute("""
            SELECT session_version
            FROM auth_users
            WHERE id = ?
        """, (user_id,)).fetchone()

    return int(row["session_version"])


def test_login_session_and_refresh_behaviour(app):
    user_id = create_user()
    client = app.test_client()
    login(client)

    with client.session_transaction() as session:
        assert session.permanent is True
        assert session["auth_user_id"] == user_id
        assert session["auth_session_version"] == 1
        assert session["auth_csrf_token"]

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response_cookie(response)
    assert app.config[
        "SESSION_REFRESH_EACH_REQUEST"
    ] is True
    assert (
        app.permanent_session_lifetime
        == timedelta(hours=8)
    )


def test_logout_invalidates_delayed_and_other_cookies(app):
    user_id = create_user()
    client = app.test_client()
    original_cookie = login(client)

    with client.session_transaction() as session:
        csrf = session["auth_csrf_token"]

    inflight = clone_client(app, original_cookie)
    inflight_response = inflight.get("/api/health")
    delayed_cookie = response_cookie(
        inflight_response
    )
    assert delayed_cookie

    logout_client = clone_client(
        app,
        original_cookie,
    )
    response = logout_client.post(
        "/logout",
        data={"_csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert session_version(user_id) == 2
    assert logout_client.get_cookie("session") is None
    assert any(
        "Max-Age=0" in header
        or "Expires=Thu, 01 Jan 1970" in header
        for header in response.headers.getlist(
            "Set-Cookie"
        )
    )

    delayed_api = clone_client(app, delayed_cookie)
    assert delayed_api.get(
        "/api/health"
    ).status_code == 401

    delayed_page = clone_client(app, delayed_cookie)
    page_response = delayed_page.get(
        "/",
        follow_redirects=False,
    )
    assert page_response.status_code == 302
    assert page_response.headers[
        "Location"
    ].startswith("/login")

    other_tab = clone_client(app, original_cookie)
    assert other_tab.get(
        "/api/health"
    ).status_code == 401


def test_invalid_csrf_preserves_session(app):
    user_id = create_user()
    client = app.test_client()
    original_cookie = login(client)

    response = client.post(
        "/logout",
        data={"_csrf": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert session_version(user_id) == 1
    assert client.get_cookie("session").value
    assert client.get_cookie(
        "session"
    ).value != ""
    assert original_cookie
    assert client.get("/api/health").status_code == 200


def test_logout_database_failure_preserves_session(
    app,
    monkeypatch,
):
    user_id = create_user()
    client = app.test_client()
    login(client)

    with client.session_transaction() as session:
        csrf = session["auth_csrf_token"]

    connect = beacn_app.db
    calls = 0

    def fail_invalidation():
        nonlocal calls
        calls += 1

        if calls == 2:
            raise sqlite3.OperationalError(
                "synthetic failure"
            )

        return connect()

    monkeypatch.setattr(
        beacn_app,
        "db",
        fail_invalidation,
    )
    response = client.post(
        "/logout",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    monkeypatch.setattr(beacn_app, "db", connect)

    assert response.status_code == 500
    assert response.get_data(as_text=True) == (
        "Unable to log out. Please try again."
    )
    assert session_version(user_id) == 1
    assert client.get_cookie("session") is not None
    assert client.get("/api/health").status_code == 200


def test_logout_all_still_invalidates_session(app):
    user_id = create_user()
    client = app.test_client()
    original_cookie = login(client)

    with client.session_transaction() as session:
        csrf = session["auth_csrf_token"]

    response = client.post(
        "/settings",
        data={
            "_csrf": csrf,
            "action": "logout_all",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert session_version(user_id) == 2
    assert client.get_cookie("session") is None
    assert clone_client(
        app,
        original_cookie,
    ).get("/api/health").status_code == 401


def test_password_change_preserves_current_session(app):
    user_id = create_user()
    client = app.test_client()
    old_cookie = login(client)

    with client.session_transaction() as session:
        csrf = session["auth_csrf_token"]

    response = client.post(
        "/settings",
        data={
            "_csrf": csrf,
            "action": "change_password",
            "current_password": "SyntheticPassword!",
            "new_password": "SyntheticNewPassword!",
            "confirm_password": "SyntheticNewPassword!",
        },
    )

    assert response.status_code == 200
    assert session_version(user_id) == 2
    assert client.get("/api/health").status_code == 200
    assert clone_client(
        app,
        old_cookie,
    ).get("/api/health").status_code == 401


def test_password_reset_invalidates_existing_session(app):
    user_id = create_user()
    old_client = app.test_client()
    old_cookie = login(old_client)
    token = "a" * 64
    now = datetime.now(timezone.utc)

    with beacn_app.db() as conn:
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
            hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest(),
            now.isoformat(),
            (
                now + timedelta(minutes=30)
            ).isoformat(),
            "127.0.0.1",
        ))
        conn.commit()

    reset_client = app.test_client()
    csrf = csrf_from_html(
        reset_client.get("/reset-password")
    )
    response = reset_client.post(
        "/reset-password",
        data={
            "_csrf": csrf,
            "token": token,
            "password": "SyntheticResetPassword!",
            "confirm_password": (
                "SyntheticResetPassword!"
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == (
        "/login?reset=success"
    )
    assert session_version(user_id) == 2
    assert clone_client(
        app,
        old_cookie,
    ).get("/api/health").status_code == 401
