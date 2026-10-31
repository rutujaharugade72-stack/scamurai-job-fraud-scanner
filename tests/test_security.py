import socket

import pytest

from security import (RateLimiter, UnsafeURLError, safe_next_url, validate_public_url)


def fake_resolver(ip):
    def _resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return _resolve


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost:5000/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.9/",
    "http://169.254.169.254/latest/meta-data/",     # cloud metadata endpoint
    "http://[::1]/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "ftp://example.com/file",
    "gopher://example.com/",
    "http://user:pass@example.com/",
    "http://example.com:22/",
    "http://printer.local/",
    "",
])
def test_blocks_unsafe_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url, resolver=fake_resolver("93.184.216.34"))


def test_blocks_hostname_that_resolves_to_private_ip():
    # e.g. attacker-controlled DNS name pointing at an internal address
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://evil.example.com/job", resolver=fake_resolver("10.1.2.3"))


def test_allows_public_host():
    url = "https://careers.example.com/jobs/1"
    assert validate_public_url(url, resolver=fake_resolver("93.184.216.34")) == url


def test_unresolvable_host_rejected():
    def boom(*a, **k):
        raise socket.gaierror("nope")
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://no-such-host.invalid/", resolver=boom)


@pytest.mark.parametrize("target,expected", [
    ("/history", "/history"),
    ("/?tab=1", "/?tab=1"),
    ("https://evil.com", "/"),
    ("//evil.com", "/"),
    ("/\\evil.com", "/"),
    ("javascript:alert(1)", "/"),
    (None, "/"),
    ("", "/"),
])
def test_safe_next_url(target, expected):
    assert safe_next_url(target) == expected


def test_rate_limiter_blocks_then_recovers():
    now = [1000.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert all(rl.allow("k", 3, 60)[0] for _ in range(3))
    ok, retry = rl.allow("k", 3, 60)
    assert not ok and retry >= 1
    assert rl.allow("other-key", 3, 60)[0]          # keys are independent
    now[0] += 61                                    # window passes
    assert rl.allow("k", 3, 60)[0]
