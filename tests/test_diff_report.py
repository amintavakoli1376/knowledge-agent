"""Tests for the daily divergence report (scripts/diff_report.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "diff_report.py"


def test_diff_report_exit_zero_when_clean():
    """در حالت سینک، خروجی باید 0 باشد (CLEAN)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=60,
    )
    assert proc.returncode == 0, f"diff_report returned {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "CLEAN" in proc.stdout


def test_diff_report_lists_all_tables():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=60,
    )
    for t in ("content", "doc_versions", "embeddings", "concepts"):
        assert t in proc.stdout, f"table {t} missing from report"
