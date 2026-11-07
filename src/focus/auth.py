"""Soulseek-style auth: one form, unknown usernames become accounts."""

import hashlib
import hmac
import secrets
import sqlite3
from base64 import b64decode, b64encode

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config

COOKIE = "focus_session"
MAX_AGE = 60 * 60 * 24 * 180


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=64 << 20)
    return f"scrypt$16384$8$1${b64encode(salt).decode()}${b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(
            password.encode(),
            salt=b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(b64decode(digest)),
            maxmem=64 << 20,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, b64decode(digest))


def serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.secret(), salt="focus-session")


def login_or_register(con: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    """None means the username exists and the password is wrong."""
    user = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user:
        return user if verify_password(password, user["password"]) else None
    with con:
        cur = con.execute(
            "INSERT INTO users (username, display_name, password) VALUES (?, ?, ?)",
            (username, username, hash_password(password)),
        )
        con.execute(
            "INSERT INTO projects (user_id, name, position) VALUES (?, 'inbox', 0)",
            (cur.lastrowid,),
        )
    return con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def session_cookie(username: str) -> str:
    return serializer().dumps(username)


def user_from_request(request: Request, con: sqlite3.Connection) -> sqlite3.Row | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        username = serializer().loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return None
    return con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def api_token_ok(token: str | None) -> bool:
    if not token:
        return False
    try:
        return hmac.compare_digest(token, config.hermes_token())
    except FileNotFoundError:
        return False


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="not signed in")
