"""Tests for structured logging helper (Epic 2 Task D1)."""
import json
import logging

from src.storage.logging_util import JsonFormatter, get_structured_logger


class CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
        self.setFormatter(JsonFormatter())

    def emit(self, record):
        self.records.append(self.format(record))


def _capture():
    logger = get_structured_logger("test.odoo.log", capture=True)
    handler = CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


def test_logger_emits_valid_json():
    logger, h = _capture()
    logger.info("hello", extra={"model": "knowledge.article", "id": 3})
    assert len(h.records) == 1
    obj = json.loads(h.records[0])
    assert obj["message"] == "hello"
    assert obj["model"] == "knowledge.article"
    assert obj["id"] == 3
    assert obj["level"] == "INFO"
    assert obj["logger"] == "test.odoo.log"


def test_logger_masks_api_key():
    logger, h = _capture()
    secret = "sk-super-secret-value-123"
    logger.error("failed %s", secret)
    text = h.records[0]
    assert secret not in text
    obj = json.loads(text)
    assert "sk-super-secret" not in obj["message"]


def test_logger_redact_extra():
    logger, h = _capture()
    logger.warning("warn", extra={"api_key": "sk-topsecret", "db": "ka_test"})
    text = h.records[0]
    assert "sk-topsecret" not in text
    obj = json.loads(text)
    assert obj["api_key"] == "[REDACTED]"
    assert obj["db"] == "ka_test"
