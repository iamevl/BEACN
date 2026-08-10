# BEACN Administrator Recovery Console

## Overview

BEACN provides a local administration and recovery mechanism intended
to prevent administrators from becoming permanently locked out of an
installation.

The local recovery path is independent of:

- the BEACN web interface
- SMTP
- Cloudflare
- DNS
- browser sessions
- external identity providers

If the web interface cannot be accessed, the BEACN host remains the
final recovery mechanism.

## Opening the Administration Console

SSH to the host running BEACN and run:

    beacn-admin

The command can be run from any directory after the launcher has been
installed into `/usr/local/bin`.

The Administration Console provides grouped menus for:

- Security and recovery
- Console services
- Diagnostics
- Logs

## Security and Recovery

The Security and Recovery menu includes:

1. List users
2. Reset user password
3. Set recovery email
4. Log out all user sessions

A local password reset:

- does not require the existing password
- writes a new secure password hash to the BEACN database
- invalidates all active browser sessions for that account
- invalidates outstanding password reset links

This is the primary break-glass recovery mechanism if email-based
recovery is unavailable.

## Console Services

The Administration Console can:

- Show console status
- Restart the BEACN console
- Start the BEACN console
- Stop the BEACN console
- Rebuild and restart BEACN

## Diagnostics

Diagnostic functions include:

- SQLite integrity check
- Authentication health check
- User count
- Enabled user count
- Outstanding password reset token count
- Container status
- BEACN host information
- Docker version
- Git revision
- Git working tree status

## Logs

The Administration Console can:

- View recent BEACN logs
- Follow live BEACN logs

## Direct CLI Commands

The interactive Administration Console is built on top of the BEACN
administrator CLI. These commands remain available for scripting or
direct recovery.

### List users

    cd /opt/beacn-console

    docker compose exec -T beacn-console       python -m beacn.admin list-users

### Reset a password

    cd /opt/beacn-console

    docker compose exec beacn-console       python -m beacn.admin reset-password USERNAME

Do not use -T for password reset because the password prompts require
an interactive terminal.

### Set recovery email

    cd /opt/beacn-console

    docker compose exec -T beacn-console       python -m beacn.admin set-email USERNAME user@example.com

### Invalidate all sessions

    cd /opt/beacn-console

    docker compose exec -T beacn-console       python -m beacn.admin logout-all USERNAME

## Disaster Recovery Principle

BEACN should never require reinstallation simply because an
administrator has forgotten a password.

Loss of SMTP, Cloudflare, DNS, browser sessions, the public BEACN URL,
or the web interface itself should not prevent recovery.

Local access to the host running BEACN is the final break-glass
recovery path.

## Future Administration Console Enhancements

Potential additions include:

- Backup and restore
- BEACN update manager
- SMTP configuration testing
- Agent management
- Plugin management
- Database maintenance
- Certificate management
- Health report generation
- Configuration export
- Configuration import
