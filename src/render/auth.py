"""احراز هویت سادهٔ وب برای Knowledge Agent.

مدل تهدید: سرویس روی IP عمومی بدون پروکسی اجرا می‌شود. یک توکن واحد (WEB_AUTH_TOKEN)
همهٔ مسیرها را قفل می‌کند؛ سه راه ارائهٔ توکن:

  ۱) کوکی `ka_auth` — بعد از ورود در صفحهٔ /login (برای مرورگر/گوشی)
  ۲) هدر `Authorization: Bearer <token>` (برای API و اسکریپت)
  ۳) کوئری `?token=<token>` (برای باز کردن سریع لینک؛ در لاگ پروکسی می‌ماند)

کوکی مقدار خام توکن را نگه نمی‌دارد؛ HMAC آن ذخیره می‌شود تا نشت کوکی = نشت توکن نباشد.
مسیرهای عمومی: /health، /static، /login، /logout.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE_NAME = "ka_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # ۳۰ روز

PUBLIC_PREFIXES = ("/static",)
PUBLIC_PATHS = {"/health", "/login", "/logout", "/favicon.ico"}


def _cookie_value(token: str) -> str:
    """مقدار کوکی = HMAC توکن؛ افشای کوکی بدون توکن بی‌فایده است."""
    return hmac.new(token.encode(), b"ka-cookie-v1", hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


def presented_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if request.query_params.get("token"):
        return request.query_params["token"].strip()
    return ""


def authorized(request: Request, expected: str) -> bool:
    if not expected:
        return False  # توکن تنظیم نشده → همه چیز بسته (fail-closed)
    token = presented_token(request)
    if token and _same(token, expected):
        return True
    cookie = request.cookies.get(COOKIE_NAME, "")
    return bool(cookie) and _same(cookie, _cookie_value(expected))


def wants_html(request: Request) -> bool:
    if request.url.path.startswith("/api/"):
        return False
    return "text/html" in request.headers.get("accept", "")


def unauthorized(request: Request, expected_set: bool) -> Response:
    if wants_html(request):
        nxt = request.url.path
        if request.url.query:
            nxt += "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
    detail = "unauthorized" if expected_set else "web auth token not configured"
    return JSONResponse(
        {"detail": detail},
        status_code=401 if expected_set else 503,
        headers={"WWW-Authenticate": "Bearer"},
    )


LOGIN_HTML = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ورود · عامل دانش</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100svh; display:grid; place-items:center;
    font-family: ui-sans-serif, system-ui, "Vazirmatn", sans-serif;
    background:#f6f7f9; color:#1f2328; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#16181d; color:#e6e8eb; }} }}
  form {{ width:min(92vw,380px); background:inherit; border:1px solid #d8dbe0;
    border-radius:16px; padding:24px; display:flex; flex-direction:column; gap:14px;
    box-shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06); }}
  h1 {{ font-size:17px; margin:0 0 2px; font-weight:600; }}
  p.hint {{ margin:0; font-size:13px; opacity:.7; line-height:1.7; }}
  label {{ font-size:13px; opacity:.85; }}
  input {{ padding:11px 12px; border:1px solid #cfd4da; border-radius:10px; font-size:15px;
    background:transparent; color:inherit; direction:ltr; text-align:left; }}
  input:focus {{ outline:2px solid #006b5f; outline-offset:1px; border-color:#006b5f; }}
  button {{ padding:12px; border:0; border-radius:10px; background:#006b5f; color:#fff;
    font-size:15px; font-weight:600; cursor:pointer; }}
  .err {{ color:#b42318; font-size:13px; }}
</style></head>
<body>
<form method="post" action="/login">
  <h1>ورود به عامل دانش</h1>
  <p class="hint">توکن دسترسی را وارد کن. یک‌بار که وارد شوی، تا ۳۰ روز در همین مرورگر می‌مانی.</p>
  <label for="token">توکن</label>
  <input id="token" name="token" type="password" autocomplete="current-password"
         placeholder="••••••••••" required autofocus>
  {error}
  <input type="hidden" name="next" value="{next}">
  <button type="submit">ورود</button>
</form>
</body></html>
"""


def login_page(next_path: str = "/index", error: str = "") -> HTMLResponse:
    body = LOGIN_HTML.format(
        next=quote(next_path, safe=""),
        error=f'<div class="err">{error}</div>' if error else "",
    )
    return HTMLResponse(body, status_code=200 if not error else 401)
