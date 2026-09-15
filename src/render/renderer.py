"""کاغذ renderer: بلوک‌های parser → HTML قطعی با کلاس‌های kaghaz.css.

هر بلوک به یک کامپوننت دیزاین‌سیستم «کاغذ» نگاشت می‌شود:
heading/paragraph/callout/todo/code/table/list/quote/hr + بج‌های
رنگی دسته/وضعیت (بدون ایموجی) + properties سند.
"""

from __future__ import annotations

import html
from typing import Any

from src.render.parser import Block, inline_html

# دسته → کلاس بج (رنگ‌های fixed palette)
CATEGORY_BADGE = {
    "AI/ML": "blue",
    "AI": "blue",
    "ML": "blue",
    "Social": "indigo",
    "News": "sky",
    "Web": "green",
    "Website": "green",
    "Video": "amber",
    "Paper": "sky",
}
# وضعیت → بج
STATUS_BADGE = {
    "unread": ("amber", "خوانده‌نشده"),
    "reading": ("sky", "در حال خواندن"),
    "read": ("green", "خوانده‌شده"),
    "archived": ("gray", "بایگانی"),
}


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def badge(text: str, cls: str) -> str:
    return f'<span class="kg-badge {esc(cls)}">{esc(text)}</span>'


def category_badge(cat: str) -> str:
    if not cat:
        return ""
    cls = CATEGORY_BADGE.get(cat.strip(), CATEGORY_BADGE.get(cat.strip().lower(), "gray"))
    return badge(cat, cls)


def status_badge(status: str) -> str:
    if not status:
        return ""
    key = status.strip().lower()
    cls, fa = STATUS_BADGE.get(key, ("gray", status))
    return badge(fa, cls)


def render_block(b: Block) -> str:
    t, d = b.type, b.data
    if t == "doc_title":
        return ""  # تیتر در header صفحه رندر می‌شود، نه در بدنه
    if t == "paragraph":
        return f'<p class="kg-p kg-has-gutter">{d["html"]}</p>'
    if t == "heading":
        lvl = d.get("level", 2)
        return f'<h{lvl} class="kg-h{lvl} kg-has-gutter">{d["html"]}</h{lvl}>'
    if t == "quote":
        return f'<blockquote class="kg-quote kg-has-gutter">{d["html"]}</blockquote>'
    if t == "callout":
        icon = d.get("emoji") or "💡"
        return (
            '<div class="kg-callout">'
            f'<div class="kg-callout-icon">{esc(icon)}</div>'
            f'<div class="kg-callout-body">{d["html"]}</div>'
            "</div>"
        )
    if t == "todo":
        rows = []
        for it in d["items"]:
            checked = "checked" if it["checked"] else ""
            done = " done" if it["checked"] else ""
            rows.append(
                f'<div class="kg-todo{done}">'
                f'<input type="checkbox" {checked} disabled>'
                f"<label>{it['html']}</label>"
                "</div>"
            )
        return f'<div class="kg-todos">{"".join(rows)}</div>'
    if t == "list":
        tag = "ol" if d.get("ordered") else "ul"
        lis = "".join(f'<li class="kg-li">{it}</li>' for it in d["items"])
        return f'<{tag} class="kg-{"ol" if d.get("ordered") else "ul"}">{lis}</{tag}>'
    if t == "code":
        lang = d.get("lang", "text")
        return (
            '<div class="kg-code">'
            '<div class="kg-code-head">'
            '<div class="kg-code-dots"><i></i><i></i><i></i></div>'
            f"<span>{esc(lang)}</span>"
            '<button class="kg-code-copy" onclick="kgCopyCode(this)">کپی کردن کد</button>'
            "</div>"
            f"<pre><code>{esc(d['code'])}</code></pre>"
            "</div>"
        )
    if t == "table":
        ths = "".join(f"<th>{esc(h)}</th>" for h in d["header"])
        trs = []
        for row in d["rows"]:
            tds = "".join(f"<td>{inline_html(c)}</td>" for c in row)
            trs.append(f"<tr>{tds}</tr>")
        return (
            '<div class="kg-table-wrap"><table class="kg-table">'
            f"<thead><tr>{ths}</tr></thead>"
            f'<tbody>{"".join(trs)}</tbody>'
            "</table></div>"
        )
    if t == "hr":
        return '<hr class="kg-hr">'
    return ""


def render_blocks(blocks: list[Block]) -> str:
    # بخش Summary (EN) در صفحات پست‌ها نمایش داده نمی‌شود (خواست کاربر؛
    # داده در دیتابیس/Outline سالم می‌ماند) — هدر و محتوایش حذف می‌شود.
    filtered = []
    skip = False
    for b in blocks:
        if b.type == "heading" and "summary (en)" in (b.data.get("html", "") or "").lower():
            skip = True
            continue
        if skip:
            # بخش تا هدر بعدی ادامه دارد
            if b.type == "heading":
                skip = False
            else:
                continue
        filtered.append(b)
    parts = [render_block(b) for b in filtered]
    return "\n".join(p for p in parts if p)


# ---------- document properties (از Notion) ----------

def render_properties(meta: dict) -> str:
    """متادیتای سند (دسته/پلتفرم/وضعیت/...) به‌صورت properties ناتیون-مانند.

    آیکون‌ها ایموجی‌اند (نه فونت آیکون) — نام Material Icons بدون فونت‌شان
    به‌صورت متن خام رندر می‌شد (باگ «devices / person» در سند healthcare).
    """
    rows = []
    items = [
        ("🗓", "تاریخ ذخیره", esc(meta.get("date", ""))),
        ("category", "دسته", category_badge(meta.get("category", ""))),
        ("🌐", "پلتفرم", esc(meta.get("platform", "")) or "—"),
        ("◉", "وضعیت", status_badge(meta.get("status", ""))),
        ("👤", "نویسنده", esc(meta.get("author", "")) or "—"),
    ]
    if meta.get("source_url"):
        host = meta.get("source_host", "") or meta["source_url"]
        items.append(
            ("🔗", "منبع", f'<a href="{esc(meta["source_url"])}" target="_blank" rel="noopener">{esc(host)}</a>')
        )
    if meta.get("tags"):
        tags = meta["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split("،") if t.strip()]
        tag_html = "".join(badge(t, "gray") for t in tags[:8])
        items.append(("label", "برچسب‌ها", f'<span class="kg-row">{tag_html}</span>'))
    for icon, key, val in items:
        if not val:
            continue
        rows.append(
            '<div class="kg-prop">'
            f'<div class="kg-prop-k">{icon} {esc(key)}</div>'
            f'<div class="kg-prop-v">{val}</div>'
            "</div>"
        )
    if not rows:
        return ""
    return f'<div class="kg-props">{"".join(rows)}</div>'


COPY_JS = """
<script>
function kgCopyCode(btn){
  const code = btn.closest('.kg-code').querySelector('code').innerText;
  navigator.clipboard.writeText(code).then(()=>{
    btn.textContent='کپی شد!';
    setTimeout(()=>{btn.textContent='کپی کردن کد';},1500);
  });
}
</script>
"""
