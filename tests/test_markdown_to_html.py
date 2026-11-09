"""Epic — Markdown → HTML (FR-003) + RTL per-paragraph (FR-008)."""
import pytest

from lxml import html as lxml_html

from src.models import AIAnalysis, ExtractedContent
from src.storage.odoo import OdooStorage


def _content(full_text="Body text here", title="Sample Article", platform="website", url="https://example.com/a"):
    return ExtractedContent(
        title=title,
        url=url,
        full_text=full_text,
        platform=platform,
        published_date="2026-11-01",
    )


def _analysis(summary_fa="خلاصه"):
    return AIAnalysis(summary_fa=summary_fa, category="Tech", tags=["ai"])


def _make_store():
    from src.config import Settings

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
    )
    return OdooStorage(cfg=cfg, data_dir="/tmp")


def test_markdown_to_html_renders_valid_html():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="## فصل اول\n- مورد ۱\n- مورد ۲"),
        _analysis(),
    )
    # HTML معتبر است (lxml parse می‌کند)
    lxml_html.fromstring("<div>" + html + "</div>")
    # عنوان به‌صورت h1 رندر شده
    assert "<h1>Sample Article</h1>" in html
    # بولدت به‌صورت li رندر شده
    assert "<li>" in html
    # هدینگ دوم به‌صورت h2 رندر شده
    assert "<h2>فصل اول</h2>" in html


def test_markdown_to_html_escapes_raw_html():
    # FR-003: HTML خام ورودی باید غیرفعال شود (امن).
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="<script>alert('xss')</script>"),
        _analysis(),
    )
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html


def test_rtl_per_paragraph_persian():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="این یک متن فارسی است."),
        _analysis(summary_fa="خلاصه فارسی"),
    )
    # پاراگراف فارسی باید dir="rtl" داشته باشد
    assert 'dir="rtl"' in html


def test_rtl_per_paragraph_latin():
    store = _make_store()
    html = store._markdown_to_html(
        _content(full_text="This is an English paragraph."),
        _analysis(summary_fa=""),
    )
    # پاراگراف انگلیسی باید dir="ltr" داشته باشد
    assert 'dir="ltr"' in html


def test_save_sets_icon_from_platform(tmp_path):
    from src.config import Settings

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.next_id = 100

        def execute_kw(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))
            if method == "search_read" and args and args[0] and args[0][0]:
                return [{"id": 5}] if args[0][0][1] == "=" else []
            if method == "create":
                new_id = self.next_id
                self.next_id += 1
                return new_id
            return 0

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
    )
    # platform شناخته‌شده
    client = FakeClient()
    store = OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))
    store.save(_content(platform="youtube", url="https://example.com/y"), _analysis())
    create_call = [c for c in client.calls if c[1] == "create"]
    assert create_call[-1][2][0]["icon"] == "▶️"
    # platform ناشناخته → پیش‌فرض
    client2 = FakeClient()
    store2 = OdooStorage(client=client2, cfg=cfg, data_dir=str(tmp_path))
    store2.save(_content(platform="mystery", url="https://example.com/m"), _analysis())
    create_call2 = [c for c in client2.calls if c[1] == "create"]
    assert create_call2[-1][2][0]["icon"] == "📄"


def test_large_body_goes_to_attachment(tmp_path):
    from src.config import Settings

    class FakeClient:
        def __init__(self):
            self.calls = []
            self.att_id = 900
            self.article_id = 100

        def execute_kw(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))
            if method == "search_read" and args and args[0] and args[0][0]:
                return [{"id": 1}] if args[0][0][1] == "=" else []
            if model == "knowledge.article" and method == "create":
                self.article_id += 1
                return self.article_id
            if model == "ir.attachment" and method == "create":
                self.att_id += 1
                return self.att_id
            if model == "knowledge.article" and method == "write":
                return True
            return 0

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
        odoo_body_size_limit=200,  # آستانه بسیار کوچک برای واداشتن به مسیر attachment
    )
    client = FakeClient()
    store = OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))
    big_text = "کلمه " * 200  # > 200 کاراکتر
    store.save(_content(full_text=big_text), _analysis())

    # مقاله ساخته شد
    creates = [c for c in client.calls if c[0] == "knowledge.article" and c[1] == "create"]
    assert len(creates) == 1
    # بدنهٔ مقاله کوتاه است و لینک has
    body = creates[0][2][0]["body"]
    assert len(body) < 2000
    assert "مشاهدهٔ محتوای کامل" in body

    # attachment ساخته شد و بدنه با لینک واقعی به‌روز شد
    att_creates = [c for c in client.calls if c[0] == "ir.attachment" and c[1] == "create"]
    assert len(att_creates) == 1
    att = att_creates[0][2][0]
    assert att["res_model"] == "knowledge.article"
    assert att["res_id"] == creates[0][2][0].get("res_id") or client.article_id
    assert att["mimetype"] == "text/html"
    # datas باید base64 داشته باشد
    assert isinstance(att["datas"], str) and att["datas"]

    writes = [c for c in client.calls if c[0] == "knowledge.article" and c[1] == "write"]
    assert len(writes) == 1
    written_body = writes[0][2][1]["body"]
    assert "__FULL_LINK__" not in written_body
    assert str(client.att_id) in written_body  # لینک واقعی قرار گرفت


def test_small_body_no_attachment(tmp_path):
    from src.config import Settings

    class FakeClient:
        def __init__(self):
            self.calls = []

        def execute_kw(self, model, method, args, kwargs=None):
            self.calls.append((model, method, args))
            if method == "search_read":
                return []
            if method == "create":
                return 1
            return 0

    cfg = Settings(
        odoo_enabled=True,
        odoo_url="https://odoo.example.com",
        odoo_db="knowledge",
        odoo_api_key="sk-test",
        odoo_uid=6,
        odoo_body_size_limit=50000,
    )
    client = FakeClient()
    store = OdooStorage(client=client, cfg=cfg, data_dir=str(tmp_path))
    store.save(_content(full_text="Short text"), _analysis())
    # هیچ attachment ساخته نشد
    att = [c for c in client.calls if c[0] == "ir.attachment"]
    assert att == []
