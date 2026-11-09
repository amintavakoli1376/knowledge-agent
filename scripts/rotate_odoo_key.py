#!/usr/bin/env python3
"""Two-step Odoo API-key rotation for the Knowledge Agent service user.

Step 1 (create & save): generate a NEW key inside the Odoo container via
    ``odoo shell`` + ``res.users.apikeys._generate``, write it to
    ``<keyfile>.new`` (chmod 600) and verify it with a real JSON-RPC
    ``search_read`` before touching anything live.
Step 2 (swap & revoke): only after successful verification, atomically move
    the new key into place and unlink the OLD key row(s) in the DB.

Security: the key never appears on stdout, in logs or in commits — only a
sha256 prefix of the new key is reported. If any step before revocation fails,
the old key is left fully usable (nothing is revoked), so the service is never
left without a valid credential.

Usage:
    python3 scripts/rotate_odoo_key.py \
        [--key-file /root/odoo/.ka_service_key] \
        [--db production] [--uid 5] [--container odoo-app] \
        [--url http://127.0.0.1:8069] \
        [--name "Knowledge Agent mirror (rotated)"] \
        [--expiration-days 90] [--restart-service]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

KEY_BEGIN = "<<<KAKEY>>>"
KEY_END = "<<</KAKEY>>>"
ID_BEGIN = "<<<KANEWID>>>"
ID_END = "<<</KANEWID>>>"
RVK_BEGIN = "<<<KAREVOKED>>>"
RVK_END = "<<</KAREVOKED>>>"


def _log(msg: str) -> None:
    print(f"rotate_odoo_key: {msg}", flush=True)


def _container_running(container: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return container in out.split()


def _query_old_ids(db: str, uid: int, db_container: str) -> list[int]:
    """Read the pre-existing apikey row ids for the service user (read-only)."""
    sql = f"select id from res_users_apikeys where user_id={uid} order by id;"
    r = subprocess.run(
        ["docker", "exec", db_container, "psql", "-U", "odoo", "-d", db,
         "-tA", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    ids: list[int] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            ids.append(int(line))
    return ids


def _run_shell(container: str, db: str, snippet: str) -> str | None:
    """Pipe a Python snippet into ``odoo shell`` and return stdout.

    Returns None if the container command failed to run.
    """
    r = subprocess.run(
        ["docker", "exec", "-i", container, "odoo", "shell",
         "-d", db, "--no-http"],
        input=snippet, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0 and not r.stdout:
        _log(f"odoo shell failed rc={r.returncode}")
        return None
    return r.stdout


def _gen_key(container: str, db: str, uid: int, name: str,
             expiration_days: int) -> tuple[str, str] | None:
    """Create a new rpc API key for ``uid`` inside the container.

    Returns (plaintext_key, new_row_id) or None on failure.
    """  # noqa: D401
    snippet = f"""\
import datetime
user = env['res.users'].browse({uid})
key = env['res.users.apikeys'].with_user(user).sudo()._generate(
    'rpc', {name!r},
    datetime.datetime.now() + datetime.timedelta(days={expiration_days}))
env.cr.commit()
print('{KEY_BEGIN}' + key + '{KEY_END}')
rows = env['res.users.apikeys'].search(
    [('user_id','=',{uid}),('name','=',{name!r})])
if len(rows) == 1:
    print('{ID_BEGIN}' + str(rows.id) + '{ID_END}')
else:
    print('{ID_BEGIN}multi{ID_END}')
"""
    stdout = _run_shell(container, db, snippet)
    if stdout is None:
        return None
    m = re.search(re.escape(KEY_BEGIN) + r"(.*?)" + re.escape(KEY_END),
                  stdout, re.S)
    if not m:
        _log("new key marker not found in odoo shell output")
        return None
    key = m.group(1).strip()
    if not key:
        _log("generated key is empty")
        return None
    mid = re.search(re.escape(ID_BEGIN) + r"(.*?)" + re.escape(ID_END),
                    stdout, re.S)
    new_id = mid.group(1).strip() if mid else "multi"
    return key, new_id


def _verify_key(url: str, db: str, key: str, uid: int) -> bool:
    """Run a real ``search_read`` with the key to prove it authenticates."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from src.config import Settings
        from src.storage.odoo_client import OdooClient, AuthError
    except Exception as e:  # noqa: BLE001
        _log(f"cannot import repo client: {e}")
        return False
    try:
        cfg = Settings(odoo_url=url, odoo_db=db,
                       odoo_api_key=key, odoo_uid=uid)
        client = OdooClient(cfg)
        result = client.execute_kw(
            "res.partner", "search_read", [[]], {"fields": ["id"], "limit": 1}
        )
        return isinstance(result, list)
    except AuthError:
        return False
    except Exception as e:  # noqa: BLE001
        _log(f"verify failed: {e}")
        return False


def _revoke(container: str, db: str, old_ids: list[int]) -> bool:
    if not old_ids:
        _log("no old keys to revoke")
        return True
    id_list = ", ".join(str(i) for i in old_ids)
    snippet = f"""\
env['res.users.apikeys'].browse([{id_list}]).unlink()
env.cr.commit()
print('{RVK_BEGIN}' + str(len([{id_list}])) + '{RVK_END}')
"""
    stdout = _run_shell(container, db, snippet)
    if stdout is None:
        return False
    return bool(re.search(re.escape(RVK_BEGIN), stdout))


def _restart_service_and_health(timeout: float = 60.0) -> bool:
    try:
        r = subprocess.run(["systemctl", "restart", "knowledge-agent"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            _log(f"systemctl restart failed: {r.stderr.strip()}")
            return False
    except Exception as e:  # noqa: BLE001
        _log(f"restart failed: {e}")
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8080/health", timeout=5
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    _log("health check did not recover in time")
    return False


def _sha256_prefix(key: str, length: int = 16) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:length]


def main() -> int:
    ap = argparse.ArgumentParser(description="Two-step Odoo API key rotation")
    ap.add_argument("--key-file", default="/root/odoo/.ka_service_key")
    ap.add_argument("--db", default="production")
    ap.add_argument("--uid", type=int, default=5,
                    help="Odoo service-user id (KA service user = 5)")
    ap.add_argument("--container", default="odoo-app")
    ap.add_argument("--db-container", default="odoo-db")
    ap.add_argument("--url", default="http://127.0.0.1:8069")
    ap.add_argument("--name", default="Knowledge Agent mirror (rotated)")
    ap.add_argument("--expiration-days", type=int, default=90)
    ap.add_argument("--restart-service", action="store_true",
                    help="Restart knowledge-agent and check /health after swap")
    args = ap.parse_args()

    if not _container_running(args.container):
        _log(f"container {args.container} is not running — aborting")
        return 2

    old_ids = _query_old_ids(args.db, args.uid, args.db_container)
    _log(f"pre-existing key rows for uid {args.uid}: {old_ids}")

    # ---- Step 1: generate + save + verify ----
    generated = _gen_key(args.container, args.db, args.uid,
                         args.name, args.expiration_days)
    if generated is None:
        _log("key generation failed — nothing changed")
        return 1
    new_key, new_id = generated
    _log(f"new key row id: {new_id}")

    new_file = args.key_file + ".new"
    try:
        fd = os.open(new_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new_key + "\n")
        os.chmod(new_file, 0o600)
    except OSError as e:
        _log(f"cannot write {new_file}: {e}")
        return 1

    _log("verifying new key with a real search_read…")
    if not _verify_key(args.url, args.db, new_key, args.uid):
        _log("verification FAILED — removing .new, keeping old key untouched")
        try:
            os.remove(new_file)
        except OSError:
            pass
        if new_id.isdigit():
            _log(f"cleaning up unverified new key row {new_id}")
            _revoke(args.container, args.db, [int(new_id)])
        return 1
    _log("new key verified OK")

    # ---- Step 2: swap, optional restart, then revoke ----
    prev_file = args.key_file + ".prev"
    try:
        if os.path.exists(args.key_file):
            os.replace(args.key_file, prev_file)
        os.replace(new_file, args.key_file)
        os.chmod(args.key_file, 0o600)
    except OSError as e:
        _log(f"swap failed: {e}")
        return 1
    _log(f"swapped {args.key_file} <- new key")

    if args.restart_service:
        _log("restarting knowledge-agent and checking /health…")
        if not _restart_service_and_health():
            _log("health check failed (old key NOT revoked)")
            return 1

    if not _revoke(args.container, args.db, old_ids):
        _log("revocation failed — new key is live, old key(s) still present")
        return 1
    _log(f"revoked old key row(s): {old_ids}")

    try:
        os.remove(prev_file)
    except OSError:
        pass

    _log(f"done — new key sha256 prefix: {_sha256_prefix(new_key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
