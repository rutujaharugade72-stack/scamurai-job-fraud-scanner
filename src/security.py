"""Security helpers for Scamurai.

* validate_public_url / safe_get  - SSRF protection for the URL importer.
* safe_next_url                   - open-redirect protection for ?next=.
* RateLimiter / rate_limit        - small in-memory sliding-window limiter.

Known limits (documented, not hidden):
* safe_get validates every redirect hop, but DNS can in theory change between
  validation and connection (DNS rebinding). For a hardened deployment, run the
  app in a network that cannot reach internal services at all.
* The rate limiter is per-process. With several gunicorn workers each worker
  counts separately; use Redis (e.g. Flask-Limiter) if you scale out.
"""
import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urljoin, urlparse

import requests

MAX_RESPONSE_BYTES = 2 * 1024 * 1024   # 2 MB is plenty for a job page
MAX_REDIRECTS = 5
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}


class UnsafeURLError(ValueError):
    """Raised when a URL points somewhere the server must not fetch."""


def _ip_is_public(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])   # strip IPv6 zone id
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> str:
    """Return `url` if it is an http(s) URL whose host resolves ONLY to public
    IP addresses. Raise UnsafeURLError otherwise.

    `resolver` is injectable so tests do not need real DNS."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("Empty URL.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http:// and https:// links are allowed.")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("The link has no host name.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Links with embedded credentials are not allowed.")
    try:
        port = parsed.port
    except ValueError:
        raise UnsafeURLError("The link has an invalid port.")
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("That port is not allowed.")
    if host.lower() in ("localhost",) or host.lower().endswith((".local", ".internal", ".localhost")):
        raise UnsafeURLError("Internal host names are not allowed.")

    # A bare IP literal can be checked without DNS.
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = resolver(host, port or (443 if parsed.scheme == "https" else 80),
                             type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise UnsafeURLError("Could not resolve that host name.")
        addresses = [info[4][0] for info in infos]
    if not addresses:
        raise UnsafeURLError("Could not resolve that host name.")
    for addr in addresses:
        if not _ip_is_public(addr):
            raise UnsafeURLError("That address points to a private or internal network.")
    return url.strip()


def safe_get(url: str, headers=None, timeout=12, resolver=socket.getaddrinfo):
    """requests.get() with SSRF protection: every hop (including redirects) is
    validated, redirects are capped and the body is capped at MAX_RESPONSE_BYTES.
    Returns a normal requests.Response."""
    current = validate_public_url(url, resolver=resolver)
    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(current, headers=headers, timeout=timeout,
                            allow_redirects=False, stream=True)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise UnsafeURLError("Redirect without a destination.")
            current = validate_public_url(urljoin(current, location), resolver=resolver)
            continue
        data = bytearray()
        for chunk in resp.iter_content(chunk_size=16384):
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                resp.close()
                raise UnsafeURLError("That page is too large to import.")
        resp._content = bytes(data)          # lets .text / .content work normally
        resp._content_consumed = True
        return resp
    raise UnsafeURLError("Too many redirects.")


def safe_next_url(target, default="/"):
    """Only allow same-site relative paths after login (blocks //evil.com,
    https://evil.com and backslash tricks)."""
    if not target or not isinstance(target, str):
        return default
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default
    return target


class RateLimiter:
    """Sliding-window limiter. allow(key, limit, window) -> (ok, retry_after)."""

    def __init__(self, clock=time.monotonic):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._clock = clock

    def allow(self, key, limit: int, window: float):
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window - (now - q[0])) + 1)
            q.append(now)
            return True, 0

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(limit: int, window: float, key_func, scope: str, enabled=lambda: True):
    """Decorator: reject with HTTP 429 once `limit` calls per `window` seconds
    are used for the key returned by key_func()."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import jsonify, request
            if enabled():
                ok, retry = limiter.allow(f"{scope}:{key_func()}", limit, window)
                if not ok:
                    body = {"error": "Too many requests. Please slow down and try again shortly.",
                            "retry_after_seconds": retry}
                    resp = jsonify(body)
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry)
                    if not request.path.startswith("/api/"):
                        return ("Too many attempts. Please wait a moment and try again.", 429,
                                {"Retry-After": str(retry)})
                    return resp
            return fn(*args, **kwargs)
        return wrapper
    return decorator
