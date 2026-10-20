"""گارد SSRF — جلوگیری از وادار کردن سرور به خواندن آدرس‌های داخلی.

مسئله: هر URLی که کاربر به بات یا `/api/save` بدهد، توسط *سرور* خوانده می‌شود.
بدون گارد، می‌توان سرور را وادار کرد به `127.0.0.1` (سرویس‌های داخلی مثل دلا)،
شبکهٔ خصوصی (`10/8`، `192.168/16`) یا آدرس متادیتای ابری (`169.254.169.254` —
جای کلیدهای ماشین) درخواست بزند و محتوایش را برگرداند.

راهکار: هر URL پیش از درخواست اعتبارسنجی می‌شود و در صورت ریدایرکت، **هر پرش**
جداگانه بررسی می‌شود (ریدایرکت به آدرس داخلی هم بسته است).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5

# میزبان‌های شناخته‌شدهٔ متادیتای ابری
BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "localhost",
    "localhost.localdomain",
}


class UnsafeURLError(ValueError):
    """URL به آدرس داخلی/غیرمجاز اشاره می‌کند."""


def _blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local          # شامل 169.254.169.254
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))  # CGNAT
    )


def _resolve(host: str) -> list[ipaddress._BaseAddress]:
    """همهٔ IPهای میزبان را برمی‌گرداند؛ خطای DNS = رد کردن (نمی‌توان تأیید کرد)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"آدرس قابل تشخیص نیست: {host}") from e
    ips = []
    for info in infos:
        addr = info[4][0]
        try:
            ips.append(ipaddress.ip_address(addr.split("%")[0]))
        except ValueError:
            continue
    if not ips:
        raise UnsafeURLError(f"آدرس قابل تشخیص نیست: {host}")
    return ips


def validate_public_url(url: str) -> str:
    """URL را بررسی و نسخهٔ پاک‌شده‌اش را برمی‌گرداند؛ وگرنه UnsafeURLError."""
    url = (url or "").strip()
    if not url:
        raise UnsafeURLError("آدرس خالی است")

    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"پروتکل پشتیبانی نمی‌شود: {parts.scheme or '—'}")
    if not parts.hostname:
        raise UnsafeURLError("آدرس میزبان ندارد")
    if parts.username or parts.password:
        raise UnsafeURLError("آدرس حاوی نام کاربری/رمز است")

    host = parts.hostname.strip("[]").lower()
    if host in BLOCKED_HOSTNAMES or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeURLError(f"میزبان داخلی مسدود است: {host}")

    # اگر خود میزبان IP است، همان را چک کن؛ وگرنه DNS را حل کن (کلاه‌برداری DNS)
    # نکته: استثنای زیر عمداً فقط ValueError ساخت ip_address را می‌گیرد، نه
    # UnsafeURLError (که خودش زیرکلاس ValueError است و باید بالا برود).
    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _blocked_ip(literal):
            raise UnsafeURLError(f"آدرس داخلی مسدود است: {host}")
    else:
        for ip in _resolve(host):
            if _blocked_ip(ip):
                raise UnsafeURLError(f"میزبان به آدرس داخلی اشاره می‌کند: {host} → {ip}")

    return url


async def safe_get(client, url: str, *, max_redirects: int = MAX_REDIRECTS, **kwargs):
    """GET با بررسی هر پرش ریدایرکت (نه فقط آدرس اول)."""
    import httpx  # noqa: F401  (نوع پاسخ از client می‌آید)

    current = validate_public_url(url)
    for _ in range(max_redirects + 1):
        response = await client.get(current, follow_redirects=False, **kwargs)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return response
            current = validate_public_url(urljoin(current, location))
            continue
        return response
    raise UnsafeURLError("ریدایرکت‌های پیاپی بیش از حد مجاز")
