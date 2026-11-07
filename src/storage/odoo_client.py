"""OdooClient — JSON-RPC 2.0 client for Odoo 19 (Epic 2).

Wraps the jsonrpc endpoint of a self-hosted Odoo instance and exposes
``execute_kw`` calls in an async-safe, urllib-only style that mirrors the
existing Outline storage backend.
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request

from ..config import settings

logger = logging.getLogger(__name__)


class OdooError(Exception):
    """Odoo returned a JSON-RPC error payload."""


class AuthError(OdooError):
    """Auth failed (401/403)."""


class RateLimitError(OdooError):
    """Odoo rate-limited us (429)."""


def _is_retryable(exc: Exception) -> bool:
    """True for transient HTTP errors (429/5xx/timeout), False for auth/4xx."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    if isinstance(exc, (TimeoutError, urllib.error.URLError)):
        return True
    return False


def _with_retry(fn, max_attempts: int = 4, base_delay: float = 1.0):
    """Call ``fn`` with exponential backoff on retryable errors.

    Base delay doubles each attempt (with a little jitter). Non-retryable
    errors propagate immediately.
    """
    import random
    import time

    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable(exc) or attempt >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            logger.warning(
                "retry %s/%s after %s: %s", attempt, max_attempts, round(delay, 2), exc
            )
            time.sleep(delay)


def _extract_error(resp: bytes) -> str:
    """Pull the JSON-RPC error message without echoing any secret."""
    try:
        data = json.loads(resp.decode())
    except Exception:
        return resp.decode(errors="replace")[:200]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("data", {}).get("message") or ""
        return str(msg)[:200]
    return str(err)[:200]


class OdooClient:
    """Minimal JSON-RPC 2.0 ``execute_kw`` client for Odoo 19."""

    def __init__(self, cfg=settings) -> None:
        self._cfg = cfg
        self._ctx = ssl._create_unverified_context()  # self-signed test cert
        self.base = cfg.odoo_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Odoo-Database": self._cfg.odoo_db,
            "API-KEY": self._cfg.odoo_api_key,
        }

    def execute_kw(
        self,
        model: str,
        method: str,
        args: list | None = None,
        kwargs: dict | None = None,
    ) -> object:
        """Run ``execute_kw`` on the model (Odoo shell-standard 7-arg form).

        Args: [db, uid, password, model, method, args, kwargs]. We authenticate
        with ``odoo_uid`` + ``odoo_api_key`` (the service-user password).
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    self._cfg.odoo_db,
                    self._cfg.odoo_uid,
                    self._cfg.odoo_api_key,
                    model,
                    method,
                    args if args is not None else [],
                    kwargs or {},
                ],
            },
        }
        req = urllib.request.Request(
            self.base + "/jsonrpc",
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=30) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 403):
                raise AuthError(f"Odoo auth failed (HTTP {code})") from e
            if code == 429:
                raise RateLimitError("Odoo rate limit (429)") from e
            raise OdooError(f"Odoo HTTP {code}") from e
        except (TimeoutError, urllib.error.URLError) as e:
            raise OdooError(f"Odoo connection error: {e}") from e

        data = json.loads(body.decode())
        if data.get("error"):
            raise OdooError(_extract_error(body))
        return data.get("result")
