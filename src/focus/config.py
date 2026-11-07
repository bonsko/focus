"""Paths and settings. Everything lives under $FOCUS_HOME (XDG data dir)."""

import os
import secrets
from pathlib import Path

HOME = Path(os.environ.get("FOCUS_HOME", Path.home() / ".local" / "share" / "focus"))
DATA = HOME / "data"
AVATARS = DATA / "avatars"
DB_PATH = DATA / "focus.db"
SECRET_PATH = DATA / "secret"
TOKEN_PATH = Path(os.environ.get("FOCUS_TOKEN_PATH", Path.home() / ".config" / "focus" / "hermes_token"))

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
AVATARS.mkdir(parents=True, exist_ok=True)


def secret() -> str:
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32))
        SECRET_PATH.chmod(0o600)
    return SECRET_PATH.read_text().strip()


def hermes_token() -> str:
    return TOKEN_PATH.read_text().strip()
