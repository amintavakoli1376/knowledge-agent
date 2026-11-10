"""Unit tests for scripts/migrate_to_odoo.py (FR-010 backfill helpers)."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from migrate_to_odoo import _json_list, _json_dict, _to_content, _to_analysis  # noqa: E402


class TestJsonHelpers:
    def test_json_list_parses_token(self):
        assert _json_list('["a", "b"]') == ["a", "b"]

    def test_json_list_none_and_invalid(self):
        assert _json_list(None) == []
        assert _json_list("not json") == []
        assert _json_list("{}") == []

    def test_json_dict_parses(self):
        assert _json_dict('{"a": 1}') == {"a": 1}

    def test_json_dict_none_and_invalid(self):
        assert _json_dict(None) == {}
        assert _json_dict("[1,2]") == {}


class TestMapping:
    def test_to_content_maps_fields(self):
        row = {
            "title": "T", "full_text": "F", "url": "https://x",
            "platform": "youtube", "author": "A",
            "created_at": datetime(2026, 11, 9, 12, 0, 0),
            "metadata": '{"video_id": "abc"}',
        }
        c = _to_content(row)
        assert c.title == "T"
        assert c.full_text == "F"
        assert c.url == "https://x"
        assert c.platform == "youtube"
        assert c.author == "A"
        assert c.published_date == "2026-11-09"
        assert c.metadata == {"video_id": "abc"}

    def test_to_analysis_maps_fields(self):
        row = {
            "summary_fa": "S", "summary_en": "E",
            "key_points": '["k1"]', "category": "General",
            "tags": '["t1", "t2"]', "priority": "High",
        }
        a = _to_analysis(row)
        assert a.summary_fa == "S"
        assert a.summary_en == "E"
        assert a.key_points == ["k1"]
        assert a.tags == ["t1", "t2"]
        assert a.priority == "High"
        assert a.category == "General"
