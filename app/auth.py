"""Optional owner sign-in.

Without ``APP_PASSWORD_HASH`` the app keeps its single-user local behavior: every
request may read and write (admin routes keep their own loopback/API-key guard).

With ``APP_PASSWORD_HASH`` set ("protected mode", e.g. a public demo):

* anyone may browse (GET), so visitors and recruiters get a read-only demo;
* every state-changing request (POST/PUT/PATCH/DELETE) needs the owner session;
* admin routes accept the owner session in addition to the existing admin guard.

Sessions are Flask signed cookies (HttpOnly, SameSite=Lax; Secure via
``SESSION_COOKIE_SECURE``). Cross-site writes are already rejected by the Origin
check in ``app.security``. Generate a hash with::

    python -m app.auth hash-password
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, Flask, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

OWNER_ROLE = "owner"
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_PUBLIC_WRITE_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class _LoginLimiter:
    """In-process sliding window of failed logins per client address."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        window = self._failures[key]
        while window and now - window[0] > LOGIN_WINDOW_SECONDS:
            window.popleft()
        return window

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= LOGIN_MAX_FAILURES

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def auth_required() -> bool:
    return bool(current_app.config.get("APP_PASSWORD_HASH"))


def is_owner() -> bool:
    return not auth_required() or session.get("role") == OWNER_ROLE


def _client_key() -> str:
    return request.remote_addr or "unknown"


def _require_owner_for_writes():
    g.owner_authenticated = auth_required() and session.get("role") == OWNER_ROLE
    if not auth_required() or not request.path.startswith("/api/"):
        return None
    if request.method in _SAFE_METHODS or request.path in _PUBLIC_WRITE_PATHS:
        return None
    if g.owner_authenticated:
        return None
    if request.path.startswith("/api/admin/"):
        return None  # admin guard decides (owner session, API key, or loopback)
    return jsonify({"error": "Sign in to make changes", "authRequired": True}), 401


@auth_bp.route("/session", methods=["GET"])
def auth_session():
    required = auth_required()
    owner = is_owner()
    return jsonify(
        {
            "authRequired": required,
            "authenticated": owner,
            "role": OWNER_ROLE if owner else "guest",
        }
    )


@auth_bp.route("/login", methods=["POST"])
def login():
    if not auth_required():
        return jsonify({"error": "Sign-in is not enabled on this server"}), 400
    limiter: _LoginLimiter = current_app.extensions["login_limiter"]
    key = _client_key()
    if limiter.blocked(key):
        return jsonify({"error": "Too many attempts. Try again in a few minutes."}), 429
    body = request.get_json(silent=True)
    password = body.get("password") if isinstance(body, dict) else None
    if not isinstance(password, str) or not password or len(password) > 1024:
        return jsonify({"error": "Password is required"}), 400
    stored = current_app.config["APP_PASSWORD_HASH"]
    try:
        valid = check_password_hash(stored, password)
    except ValueError:  # malformed hash in config
        current_app.logger.error("APP_PASSWORD_HASH is not a valid Werkzeug password hash")
        valid = False
    if not valid:
        limiter.record_failure(key)
        return jsonify({"error": "Incorrect password"}), 401
    limiter.reset(key)
    session.clear()
    session.permanent = True
    session["role"] = OWNER_ROLE
    return jsonify({"authRequired": True, "authenticated": True, "role": OWNER_ROLE})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"authRequired": auth_required(), "authenticated": not auth_required(), "role": "guest" if auth_required() else OWNER_ROLE})


def _load_secret_key(app: Flask) -> str:
    """SECRET_KEY from env, else a random key persisted next to the database (0600)."""
    configured = os.getenv("SECRET_KEY")
    if configured:
        return configured
    key_path = Path(app.config["DATABASE_PATH"]).resolve().parent / ".flask_secret_key"
    try:
        return key_path.read_text().strip()
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = key_path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(key)
    try:
        os.link(tmp_path, key_path)  # atomic: first process wins, others read its key
    except FileExistsError:
        pass
    finally:
        tmp_path.unlink(missing_ok=True)
    return key_path.read_text().strip()


def init_auth(app: Flask) -> None:
    app.config.setdefault("APP_PASSWORD_HASH", os.getenv("APP_PASSWORD_HASH") or None)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app)
    # Flask pre-populates these keys with its defaults, so assign rather than setdefault.
    app.config.update(
        SESSION_COOKIE_NAME="stock_tracker_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    app.extensions["login_limiter"] = _LoginLimiter()
    app.before_request(_require_owner_for_writes)
    app.register_blueprint(auth_bp)


def _cli() -> None:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Owner sign-in helpers")
    parser.add_argument("command", choices=["hash-password"])
    parser.parse_args()
    password = getpass.getpass("New owner password: ")
    if len(password) < 10:
        raise SystemExit("Use at least 10 characters.")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    print(generate_password_hash(password))
    print("\nSet this as APP_PASSWORD_HASH in .env (quote it: the value contains '$').")


if __name__ == "__main__":
    _cli()
