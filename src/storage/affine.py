"""AFFiNE (self-hosted) storage for the Knowledge Agent.

Writes pages via the AFFiNE **sync protocol** (socket.io + Yjs) — a pure
backend path, no browser/Playwright. The doc binary itself is produced by a
small Node helper (/root/affine-docgen/docgen.mjs) that uses the exact
@blocksuite library AFFiNE ships, so pages render like normal ones.

Fully best-effort: every failure raises a regular exception; callers must
guard with try/except so Notion is never affected.
"""
from __future__ import annotations

import asyncio
import base64
import http.cookiejar
import json
import logging
import subprocess
import uuid

import socketio

from ..config import settings

logger = logging.getLogger(__name__)

DOCGEN = "/root/affine-docgen/docgen.mjs"


class AFFiNEStorage:
    def __init__(self):
        self._jar: http.cookiejar.CookieJar | None = None

    # ---------- auth ----------
    def _cookies(self) -> str:
        if not settings.affine_enabled:
            raise RuntimeError("AFFINE not configured (AFFINE_ENABLED unset)")
        if self._jar is None or not settings.affine_email or not settings.affine_password:
            raise RuntimeError("AFFiNE email/password not configured")
        if not self._jar:
            self._jar = http.cookiejar.CookieJar()
        # login
        import urllib.request
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        body = json.dumps({
            "email": settings.affine_email,
            "password": settings.affine_password,
        }).encode()
        req = urllib.request.Request(
            settings.affine_url + "/api/auth/sign-in", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=30) as r:
            r.read()
        parts = []
        for c in self._jar:
            if c.is_expired():
                continue
            parts.append(f"{c.name}={c.value}")
        if not parts:
            raise RuntimeError("AFFiNE sign-in returned no session cookie")
        return "; ".join(parts)

    # ---------- doc binary (blocksuite) ----------
    def _gen_update(self, doc_id: str, title: str, body_md: str) -> str:
        """Call the Node blocksuite helper -> base64 Yjs update."""
        proc = subprocess.run(
            ["node", DOCGEN, doc_id, title, body_md],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"docgen failed: {proc.stderr[-400:]}")
        out = proc.stdout.strip()
        if not out:
            raise RuntimeError("docgen returned empty update")
        return out

    # ---------- sync push ----------
    def _push(self, doc_id: str, update_b64: str) -> None:
        ws = settings.affine_workspace_id or ""
        if not ws:
            raise RuntimeError("AFFINE_WORKSPACE_ID not configured")
        cookie = self._cookies()

        sio = socketio.Client()
        result: dict = {}

        @sio.on("connect")
        def on_connect():
            sio.emit("space:join", {
                "spaceType": "workspace",
                "spaceId": ws,
                "clientVersion": "0.27.4",
            }, callback=lambda *a: _joined(a))

        def _joined(a):
            try:
                result["join"] = a[0] if isinstance(a, (list, tuple)) else a
                sio.emit("space:push-doc-update", {
                    "spaceType": "workspace",
                    "spaceId": ws,
                    "docId": doc_id,
                    "update": update_b64,
                }, callback=lambda *b: _pushed(b))
            except Exception as e:  # noqa: BLE001
                result["error"] = str(e)
                sio.disconnect()

        def _pushed(b):
            d = b[0] if isinstance(b, (list, tuple)) else b
            result["push"] = d
            sio.disconnect()

        sio.connect(
            settings.affine_url,
            headers={"Cookie": cookie},
            transports=["websocket"], wait_timeout=20)
        sio.wait()
        push = result.get("push") or {}
        if not push.get("accepted"):
            raise RuntimeError(f"AFFiNE push rejected: {push}")

    # ---------- public API (mirror of NotionStorage.save) ----------
    async def save(self, content, analysis) -> str | None:
        title = getattr(content, "title", "") or "Untitled"
        body = self._build_markdown(content, analysis)
        doc_id = "ka-" + uuid.uuid4().hex[:16]
        update = await asyncio.to_thread(self._gen_update, doc_id, title, body)
        await asyncio.to_thread(self._push, doc_id, update)
        base = (settings.affine_public_url or settings.affine_url).rstrip("/")
        return f"{base}/workspace/{settings.affine_workspace_id}/{doc_id}"

    def _build_markdown(self, content, analysis) -> str:
        lines = []
        if getattr(analysis, "summary", None):
            lines.append(analysis.summary)
        if getattr(analysis, "key_points", None):
            lines.append("")
            for p in analysis.key_points[:5]:
                lines.append(f"- {p}")
        if getattr(content, "url", None):
            lines.append("")
            lines.append(f"🔗 {content.url}")
        if getattr(analysis, "category", None):
            lines.append("")
            lines.append(f"دسته: {analysis.category}")
        return "\n".join(lines) or ""


storage = AFFiNEStorage()
