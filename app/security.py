"""Request-origin protections for the API.

* CORS is limited to an allowlist (``CORS_ORIGINS``) instead of every origin.
* State-changing requests (POST/PUT/PATCH/DELETE) and all admin routes are rejected
  when the browser says they come from another site (``Origin`` not allowed).
  Without this, any web page could drive a developer's local API, because the
  admin guard trusts loopback peers and the victim's own browser is one.
* Admin routes require a local or allowlisted ``Host`` header when no admin key is
  set, which stops DNS-rebinding pages from reaching them through 127.0.0.1.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from flask import Flask, current_app, jsonify, request
from flask_cors import CORS

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _split_env(name: str) -> list[str]:
    return [item.strip().rstrip("/") for item in os.getenv(name, "").split(",") if item.strip()]


def configured_origins() -> list[str]:
    return _split_env("CORS_ORIGINS") or list(DEFAULT_CORS_ORIGINS)


def configured_hosts() -> set[str]:
    return {host.lower() for host in _split_env("ALLOWED_HOSTS")}


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def origin_allowed(origin: str | None) -> bool:
    """No Origin header (curl, scripts, same-origin GETs) is allowed; otherwise it must be
    allowlisted or be this API's own origin."""
    if not origin:
        return True
    normalized = _origin_of(origin)
    if not normalized:  # "null" (sandboxed iframes, file://) and other opaque origins
        return False
    allowed = {item.lower() for item in current_app.config.get("CORS_ORIGINS", ())}
    return normalized in allowed or normalized == _origin_of(request.host_url)


def host_is_local_or_allowed() -> bool:
    hostname = (urlsplit(f"//{request.host}").hostname or "").lower()
    return hostname in LOCAL_HOSTNAMES or hostname in current_app.config.get("ALLOWED_HOSTS", set())


def _reject_cross_site():
    if not request.path.startswith("/api/"):
        return None
    is_admin = request.path.startswith("/api/admin/")
    if request.method == "OPTIONS" or (request.method not in _MUTATING_METHODS and not is_admin):
        return None
    if not origin_allowed(request.headers.get("Origin")):
        return jsonify({"error": "Cross-site request rejected"}), 403
    return None


def init_security(app: Flask) -> None:
    app.config.setdefault("CORS_ORIGINS", configured_origins())
    app.config.setdefault("ALLOWED_HOSTS", configured_hosts())
    CORS(
        app,
        resources={r"/api/*": {"origins": list(app.config["CORS_ORIGINS"])}},
        supports_credentials=True,
    )
    app.before_request(_reject_cross_site)
