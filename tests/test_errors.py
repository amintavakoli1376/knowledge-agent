"""تست پیام‌های کاربرپسند — هیچ جزئیات فنی نباید در پیام کاربر برود."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.utils.errors import friendly_error


class FakeAPIError(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


CASES = [
    (FakeAPIError("Error code: 429 - rate limit reached"), "سهمیه"),
    (TimeoutError("Read timed out"), "طول کشید"),
    (FakeAPIError("Client error '403 Forbidden'"), "بدون ورود"),
    (FakeAPIError("404 Not Found for url"), "پیدا نشد"),
    (ConnectionError("connection refused"), "اتصال"),
]


@pytest.mark.parametrize("exc,expected_part", CASES)
def test_maps_to_persian_actionable_message(exc, expected_part):
    msg = friendly_error(exc, "extract")
    assert expected_part in msg


def test_no_technical_leakage():
    """نام کلاس، کد وضعیت و متن خام کتابخانه در پیام کاربر نیست."""
    exc = FakeAPIError("ValueError: invalid literal for int() with base 10: 'x'", status_code=500)
    msg = friendly_error(exc, "extract")
    for leak in ("Error", "ValueError", "500", "int()", "base 10", "Traceback"):
        assert leak not in msg


def test_fallback_per_context():
    exc = RuntimeError("something odd happened")
    assert "پایگاه دانش" in friendly_error(exc, "rag")
    assert "فایل" in friendly_error(exc, "document")
    assert "ذخیره" in friendly_error(exc, "save")
    assert friendly_error(exc, "nonsense-kind")  # کرش نکند
