"""Outbound HTTP for untrusted URLs (RSS feeds and the article links inside them).

Feed items can point anywhere, including loopback admin routes, cloud metadata
(169.254.169.254) or LAN hosts, and article bodies are later served by the public
news API. ``safe_get`` therefore:

* allows only http/https URLs,
* resolves the host and rejects non-public addresses (private, loopback,
  link-local, reserved, multicast, ...) before every request, including each
  redirect hop (redirects are followed manually),
* caps the response size and the total time spent reading it,
* optionally requires an HTML content type for article pages.

Limitation: the address is checked at resolve time; a DNS answer that changes
between the check and the connection (rebinding) is not prevented here.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 5
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_REDIRECT_CODES = {301, 302, 303, 307, 308}


class UnsafeURLError(requests.RequestException):
    """URL is not an allowed public http(s) destination."""


class ResponseTooLargeError(requests.RequestException):
    """Response body exceeded the configured byte cap."""


class UnexpectedContentTypeError(requests.RequestException):
    """Response content type is not one of the allowed types."""


@dataclass
class SafeResponse:
    url: str
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    encoding: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} for {_host(self.url)}", response=None)


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def is_http_url(url: str | None) -> bool:
    """True for absolute http(s) URLs with a host (rejects javascript:, data:, file:, ...)."""
    if not url or not isinstance(url, str):
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _resolve_host(host: str) -> list[str]:
    """Resolve ``host`` to IP strings. Tests monkeypatch this to avoid real DNS."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    ip = mapped or ip
    return ip.is_global and not ip.is_multicast


def validate_public_url(url: str) -> None:
    """Raise UnsafeURLError unless ``url`` is http(s) and its host resolves only to public IPs."""
    if not is_http_url(url):
        raise UnsafeURLError(f"Only http(s) URLs are allowed: {url[:80]!r}")
    host = _host(url)
    try:
        addresses = [host] if _looks_like_ip(host) else _resolve_host(host)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeURLError(f"Cannot resolve host {host!r}") from exc
    if not addresses:
        raise UnsafeURLError(f"Cannot resolve host {host!r}")
    for address in addresses:
        try:
            public = _is_public_ip(address)
        except ValueError as exc:
            raise UnsafeURLError(f"Invalid address for {host!r}") from exc
        if not public:
            raise UnsafeURLError(f"Refusing non-public address for host {host!r}")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _read_capped(response, max_bytes: int, total_timeout: float) -> bytes:
    iter_content = getattr(response, "iter_content", None)
    if iter_content is None:  # simple test doubles expose only .text
        body = (getattr(response, "text", "") or "").encode("utf-8")
        if len(body) > max_bytes:
            raise ResponseTooLargeError(f"Response exceeds {max_bytes} bytes")
        return body
    deadline = time.monotonic() + total_timeout
    chunks: list[bytes] = []
    size = 0
    for chunk in iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise ResponseTooLargeError(f"Response exceeds {max_bytes} bytes")
        if time.monotonic() > deadline:
            raise requests.Timeout("Total read time exceeded")
        chunks.append(chunk)
    return b"".join(chunks)


def safe_get(
    session,
    url: str,
    *,
    timeout: float = 20,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allowed_content_types: tuple[str, ...] | None = None,
    total_timeout: float | None = None,
) -> SafeResponse:
    """GET an untrusted URL with SSRF, size and time limits. See module docstring."""
    read_budget = total_timeout if total_timeout is not None else timeout * 3
    current = url
    for _ in range(max_redirects + 1):
        validate_public_url(current)
        response = session.get(current, timeout=timeout, allow_redirects=False, stream=True)
        try:
            status = int(getattr(response, "status_code", 200))
            headers = {k.lower(): v for k, v in dict(getattr(response, "headers", None) or {}).items()}
            if status in _REDIRECT_CODES and headers.get("location"):
                current = urljoin(current, headers["location"])
                continue
            if allowed_content_types and status < 400:
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in allowed_content_types:
                    raise UnexpectedContentTypeError(f"Unexpected content type {content_type!r}")
            body = _read_capped(response, max_bytes, read_budget) if status < 400 else b""
            return SafeResponse(
                url=current,
                status_code=status,
                headers=headers,
                content=body,
                encoding=getattr(response, "encoding", None),
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    raise UnsafeURLError(f"Too many redirects for {_host(url)!r}")
