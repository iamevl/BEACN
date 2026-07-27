"""Compatibility launcher retained for existing development commands.

The pywin32 service implementation was removed in v0.9.1. WinSW packaging will
be introduced in the next milestone.
"""
from beacn_agent.main import main


if __name__ == "__main__":
    raise SystemExit(main())
