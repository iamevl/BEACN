import argparse
import getpass
import sys
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from beacn.common import db
from beacn.runtime import db_write_lock


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def get_user(username):
    with db() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                username,
                email,
                is_admin,
                is_enabled,
                session_version,
                last_login_at
            FROM auth_users
            WHERE username = ?
            COLLATE NOCASE
            """,
            (username,),
        ).fetchone()

    return row


def list_users(_args):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                username,
                email,
                is_admin,
                is_enabled,
                session_version,
                last_login_at
            FROM auth_users
            ORDER BY username COLLATE NOCASE
            """
        ).fetchall()

    if not rows:
        print("No BEACN users exist.")
        return 0

    print("")
    print("BEACN users")
    print("=" * 72)

    for row in rows:
        role = (
            "Administrator"
            if row["is_admin"]
            else "User"
        )

        state = (
            "Enabled"
            if row["is_enabled"]
            else "Disabled"
        )

        print(
            f'{row["username"]} | '
            f'{role} | '
            f'{state}'
        )

        print(
            f'  Email: '
            f'{row["email"] or "Not configured"}'
        )

        print(
            f'  Session version: '
            f'{row["session_version"]}'
        )

        print(
            f'  Last login: '
            f'{row["last_login_at"] or "Never"}'
        )

    return 0


def reset_password(args):
    user = get_user(
        args.username
    )

    if not user:
        print(
            f'User "{args.username}" '
            "does not exist.",
            file=sys.stderr,
        )
        return 1

    print("")
    print("BEACN Administrator Recovery")
    print("=" * 32)
    print(f'Account: {user["username"]}')
    print("")

    password = getpass.getpass(
        "New password: "
    )

    confirm = getpass.getpass(
        "Confirm password: "
    )

    if password != confirm:
        print(
            "Passwords do not match.",
            file=sys.stderr,
        )
        return 1

    if len(password) < 12:
        print(
            "Password must contain "
            "at least 12 characters.",
            file=sys.stderr,
        )
        return 1

    password_hash = (
        generate_password_hash(
            password,
            method="scrypt",
        )
    )

    now = utc_now()

    with db_write_lock:
        with db() as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET
                    password_hash = ?,
                    updated_at = ?,
                    session_version =
                        session_version + 1
                WHERE id = ?
                """,
                (
                    password_hash,
                    now,
                    user["id"],
                ),
            )

            conn.execute(
                """
                UPDATE auth_password_resets
                SET used_at = ?
                WHERE
                    user_id = ?
                    AND used_at IS NULL
                """,
                (
                    now,
                    user["id"],
                ),
            )

            conn.commit()

    print("")
    print("Password updated successfully.")
    print(
        "All existing sessions "
        "have been invalidated."
    )
    print(
        "Any outstanding password-reset "
        "links were invalidated."
    )

    return 0


def set_email(args):
    user = get_user(
        args.username
    )

    if not user:
        print(
            f'User "{args.username}" '
            "does not exist.",
            file=sys.stderr,
        )
        return 1

    email = (
        args.email
        .strip()
        .lower()
    )

    if (
        not email
        or "@" not in email
        or len(email) > 254
    ):
        print(
            "Enter a valid email address.",
            file=sys.stderr,
        )
        return 1

    with db() as conn:
        duplicate = conn.execute(
            """
            SELECT id
            FROM auth_users
            WHERE
                lower(email) = ?
                AND id != ?
            """,
            (
                email,
                user["id"],
            ),
        ).fetchone()

    if duplicate:
        print(
            "That email address is "
            "already assigned to another user.",
            file=sys.stderr,
        )
        return 1

    with db_write_lock:
        with db() as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET
                    email = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    email,
                    utc_now(),
                    user["id"],
                ),
            )

            conn.commit()

    print(
        f'Recovery email for '
        f'{user["username"]} set to {email}.'
    )

    return 0


def logout_all(args):
    user = get_user(
        args.username
    )

    if not user:
        print(
            f'User "{args.username}" '
            "does not exist.",
            file=sys.stderr,
        )
        return 1

    with db_write_lock:
        with db() as conn:
            conn.execute(
                """
                UPDATE auth_users
                SET
                    session_version =
                        session_version + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    user["id"],
                ),
            )

            conn.commit()

    print(
        f'All sessions for '
        f'{user["username"]} invalidated.'
    )

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m beacn.admin",
        description=(
            "Local BEACN administration "
            "and recovery utility."
        ),
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    list_parser = commands.add_parser(
        "list-users",
        help="List BEACN user accounts.",
    )

    list_parser.set_defaults(
        handler=list_users
    )

    password_parser = commands.add_parser(
        "reset-password",
        help=(
            "Reset a user's password "
            "from the BEACN host."
        ),
    )

    password_parser.add_argument(
        "username"
    )

    password_parser.set_defaults(
        handler=reset_password
    )

    email_parser = commands.add_parser(
        "set-email",
        help=(
            "Set a user's recovery "
            "email address."
        ),
    )

    email_parser.add_argument(
        "username"
    )

    email_parser.add_argument(
        "email"
    )

    email_parser.set_defaults(
        handler=set_email
    )

    logout_parser = commands.add_parser(
        "logout-all",
        help=(
            "Invalidate all active "
            "sessions for a user."
        ),
    )

    logout_parser.add_argument(
        "username"
    )

    logout_parser.set_defaults(
        handler=logout_all
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    return args.handler(
        args
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
