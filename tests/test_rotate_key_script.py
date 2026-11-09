"""Smoke tests for the Odoo key-rotation script (3D-1).

These cover the pure/parsable parts only — real rotation happens against the
live Odoo container and is covered by the two-step verification inside the
script itself (not here).
"""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rotate_odoo_key.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("rotate_odoo_key", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_script_imports_and_has_helpers():
    mod = _load_script()
    assert callable(mod.main)
    assert callable(mod._sha256_prefix)
    assert callable(mod._query_old_ids)
    assert callable(mod._gen_key)


def test_sha256_prefix_len():
    mod = _load_script()
    assert mod._sha256_prefix("super-secret-key", 16) == "baf698c16450117d"
    assert len(mod._sha256_prefix("abc")) == 16


def test_help_runs():
    import subprocess
    r = subprocess.run(
        [os.fspath(ROOT / "venv" / "bin" / "python"), os.fspath(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--restart-service" in r.stdout
    assert "--key-file" in r.stdout
