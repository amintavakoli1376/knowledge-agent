"""تست گارد SSRF — بدون نیاز به شبکه (DNS و کلاینت شبیه‌سازی می‌شوند)."""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.utils.url_guard import UnsafeURLError, safe_get, validate_public_url

PUBLIC_IP = "93.184.216.34"  # آدرس عمومی نمونه


def _patch_dns(monkeypatch, ip=PUBLIC_IP):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


BLOCKED_URLS = [
    "http://127.0.0.1:8001/app",
    "http://127.0.0.1:8080/index",
    "http://localhost:3000",
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://172.16.9.9/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8080/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://0.0.0.0:8080/",
    "http://100.64.1.1/",  # CGNAT
    "ftp://example.com/file",
    "file:///etc/passwd",
    "http://user:pass@example.com/",
    "",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_blocks_internal_and_invalid(url, monkeypatch):
    _patch_dns(monkeypatch)
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2402.09739",
        "https://www.youtube.com/watch?v=abc",
        "http://example.com/post",
        "https://example.com:8443/path?q=1",
    ],
)
def test_allows_normal_public_urls(url, monkeypatch):
    _patch_dns(monkeypatch)
    assert validate_public_url(url) == url


def test_blocks_hostname_that_resolves_to_private_ip(monkeypatch):
    """کلاه‌برداری DNS: دامنهٔ عمومی که به آدرس داخلی اشاره می‌کند."""
    _patch_dns(monkeypatch, ip="127.0.0.1")
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://evil.example.com/x")


def test_blocks_when_dns_cannot_resolve(monkeypatch):
    def boom(host, port, *args, **kwargs):
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://no-such-host.example/")


class _Resp:
    def __init__(self, status_code=200, location=None, text="ok"):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.text = text


class _Client:
    """کلاینت ساختگی: زنجیرهٔ ریدایرکت را بدون شبکه اجرا می‌کند."""

    def __init__(self, responses):
        self.responses = responses
        self.called = []

    async def get(self, url, follow_redirects=False, **kwargs):
        self.called.append(url)
        return self.responses[url]


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    _patch_dns(monkeypatch)
    client = _Client(
        {
            "https://public.example.com/start": _Resp(302, location="http://127.0.0.1:8001/app"),
        }
    )
    with pytest.raises(UnsafeURLError):
        import asyncio

        asyncio.run(safe_get(client, "https://public.example.com/start"))


def test_redirect_chain_to_public_is_followed(monkeypatch):
    _patch_dns(monkeypatch)
    client = _Client(
        {
            "https://a.example.com/x": _Resp(301, location="https://b.example.com/y"),
            "https://b.example.com/y": _Resp(200, text="done"),
        }
    )
    import asyncio

    resp = asyncio.run(safe_get(client, "https://a.example.com/x"))
    assert resp.status_code == 200
    assert client.called == ["https://a.example.com/x", "https://b.example.com/y"]
