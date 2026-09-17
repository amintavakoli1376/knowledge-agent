"""Markdown → Block parser for the Kaghaz (کاغذ) renderer.

اسناد Outline مارک‌داون ساده دارند؛ این ماژول آنها را به بلوک‌های
تایپ‌دار تبدیل می‌کند تا renderer بتواند هر بلوک را با کامپوننت
مخصوص «کاغذ» رندر کند. Inline rendering با markdown-it انجام می‌شود
(سینجر-لاینه و امن: escape پیشفرض + HTML خام غیرفعال).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt


# بلوک‌هایی که پشتیبانی می‌شوند:
#   heading(level, text) | paragraph(html) | quote(html)
#   callout(emoji, html) | todo(items=[{checked,html}])
#   list(ordered, items=[html]) | code(lang, code) | table(...) | hr

INLINE_MD = MarkdownIt("commonmark", {"html": False}).disable("linkify")


@dataclass
class Block:
    type: str
    data: dict = field(default_factory=dict)


# کالوت: «> 💡 متن» یا «> 📌 ...» → بلوک callout با آیکون
_CALLOUT_EMOJI = re.compile(r"^([\U0001F300-\U0001FAFF\u2600-\u27BF])\s*(.*)$")

_INLINE_CODE = re.compile(r"`([^`]+)`")


def inline_html(md: str) -> str:
    """Markdown inline → HTML (برای داخل بلوک‌ها)."""
    return INLINE_MD.renderInline(md or "")


def _table_block(rows: list[list[str]]) -> Block:
    header = rows[0]
    body = rows[2:] if len(rows) > 1 and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in rows[1]) else rows[1:]
    return Block("table", {"header": header, "rows": body})


def parse(md: str) -> list[Block]:
    """متن مارک‌داون سند Outline → لیست بلوک‌ها."""
    blocks: list[Block] = []
    lines = (md or "").split("\n")
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # --- fence code ``` ---
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            blocks.append(Block("code", {"lang": lang or "text", "code": "\n".join(code_lines)}))
            continue

        # --- heading ---
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            # اولین H1 سند = تیتر صفحه (در template جدا رندر میشود)
            if level == 1 and not any(b.type == "doc_title" for b in blocks):
                blocks.append(Block("doc_title", {"text": text}))
            else:
                blocks.append(Block("heading", {"level": min(level, 3), "html": inline_html(text)}))
            i += 1
            continue

        # --- table ---
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            if rows:
                blocks.append(_table_block(rows))
            continue

        # --- hr ---
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            blocks.append(Block("hr", {}))
            i += 1
            continue

        # --- blockquote (و callout) ---
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            qtext = "\n".join(quote_lines).strip()
            cm = _CALLOUT_EMOJI.match(qtext)
            if cm:
                blocks.append(Block("callout", {"emoji": cm.group(1), "html": inline_html(cm.group(2))}))
            else:
                blocks.append(Block("quote", {"html": inline_html(qtext)}))
            continue

        # --- todo list ---
        if re.match(r"^[-*]\s+\[[ xX]\]", stripped):
            items = []
            while i < n and re.match(r"^[-*]\s+\[[ xX]\]", lines[i].strip()):
                t = lines[i].strip()
                checked = t[5].lower() == "x"
                items.append({"checked": checked, "html": inline_html(t[6:].strip())})
                i += 1
            blocks.append(Block("todo", {"items": items}))
            continue

        # --- bullet list ---
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(inline_html(lines[i].strip()[2:].strip()))
                i += 1
            blocks.append(Block("list", {"ordered": False, "items": items}))
            continue

        # --- بولت یونیکد (• / ‣ / ◦) — خروجی برخی ابزارها یک پاراگراف با • های
        #     وسط جمله می‌سازد؛ به لیست واقعی تبدیل شود ---
        if stripped.startswith(("•", "‣", "◦")):
            # ممکن است چند نکته در یک خط باشند: «• الف. • ب. • ج.»
            parts = [p.strip() for p in re.split(r"\s+[•‣◦]\s+|\s+[•‣◦]\s*", stripped) if p.strip()]
            items = [inline_html(re.sub(r"^[•‣◦]\s*", "", p)) for p in parts]
            blocks.append(Block("list", {"ordered": False, "items": items}))
            i += 1
            continue

        # --- ordered list ---
        if re.match(r"^\d+[.)]\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(inline_html(re.sub(r"^\d+[.)]\s+", "", lines[i].strip())))
                i += 1
            blocks.append(Block("list", {"ordered": True, "items": items}))
            continue

        # --- blank ---
        if not stripped:
            i += 1
            continue

        # --- paragraph (پیوسته تا خط خالی) ---
        para: list[str] = []
        while i < n and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|```|\||>|[-*]\s|\d+[.)]\s|(-{3,}|\*{3,}|_{3,})$)", lines[i].strip()
        ):
            para.append(lines[i].strip())
            i += 1
        if para:
            blocks.append(Block("paragraph", {"html": inline_html("\n".join(para))}))
        else:
            i += 1  # safety

    return blocks
