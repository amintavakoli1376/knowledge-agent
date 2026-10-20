"""AI summarization processor — OpenAI-compatible (any base_url/model)."""
import asyncio
import json
import logging
from openai import OpenAI, AsyncOpenAI
from ..config import settings
from ..models import ExtractedContent, AIAnalysis

logger = logging.getLogger(__name__)

def _max_tokens_kwargs() -> dict:
    """سقف توکن خروجی برای همهٔ فراخوانی‌های LLM.

    چرا لازم است: مدل‌های reasoning ممکن است کل بودجهٔ پیش‌فرض را صرف
    ``reasoning_tokens`` کنند و خروجی ``content`` خالی برگردد
    (``finish_reason=length``) — همان چیزی که در کالیبره‌سازی مفاهیم دیدیم.
    ``openai_max_tokens=0`` یعنی پارامتر فرستاده نشود.
    """
    try:
        limit = int(settings.openai_max_tokens)
    except Exception:  # noqa: BLE001
        limit = 0
    return {"max_tokens": limit} if limit > 0 else {}



# نشانگرهای خطای سهمیه (429) — کوچک/بزرگ حروف مهم نیست
_QUOTA_MARKERS = (
    "monthly spending limit",
    "insufficient_quota",
    "freeusagelimit",  # FreeUsageLimit
    "free usage limit",
)

# حداکثر تلاش برای ریکاوری یک رکورد pending_resummarize
MAX_RESUMMARIZE_ATTEMPTS = 5

# فاصله بین رکوردها در ریکاوری (backoff سبک بین درخواست‌ها)
RECOVERY_SLEEP_SECONDS = 3.0


class SummarizationError(RuntimeError):
    """خلاصه‌سازی شکست خورد (غیر از سهمیه). متن خطا فقط لاگ می‌شود."""


class QuotaExceededError(RuntimeError):
    """خطای سهمیه‌ی سرویس AI (429) — قابل ریکاوی بعداً (مثلاً پس از شارژ سهمیه)."""


def is_quota_error(exc: BaseException) -> bool:
    """تشخیص خطای سهمیه (429 / monthly spending limit / insufficient_quota).

    کل زنجیرهٔ استثناها (cause ها) بررسی می‌شود چون SDK معمولاً خطای HTTP را
    داخل استثنای خودش می‌پیچد.
    """
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        # 1) status_code == 429 به‌همراه نشانگر سهمیه در پیام/بدنه
        status = getattr(cur, "status_code", None)
        if status == 429:
            text = " ".join(
                str(part) for part in (cur.args + (str(cur),)) if part
            )
            body = getattr(cur, "body", None)
            if body is not None:
                text += " " + json.dumps(body, default=str)
            low = text.lower()
            if any(m in low for m in _QUOTA_MARKERS):
                return True
        # 2) نشانگر صریح سهمیه در خود پیام (بدون نیاز به status)
        msg = str(cur).lower()
        if any(m in msg for m in _QUOTA_MARKERS):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


def _quota_pending_analysis(err: BaseException) -> AIAnalysis:
    """خروجی analyze هنگام سقف سهمیه: رکورد ذخیره می‌شود ولی خلاصهٔ خالی + flag."""
    return AIAnalysis(
        summary_fa="",
        summary_en="",
        key_points=[],
        category="",
        tags=["pending"],
        priority="Medium",
        analysis_status="pending_resummarize",
        analysis_error=str(err)[:200],
        analysis_attempts=1,
    )


class ContentSummarizer:
    """Summarize content using any OpenAI-compatible endpoint."""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    async def _chat(self, **kwargs) -> str:
        """فراخوانی LLM با یک تلاش دوباره روی پاسخ خالی.

        روترها گاهی 200 با ``content`` خالی برمی‌گردانند (مدل بودجه را صرف
        ``reasoning`` می‌کند یا گلیچ گذرا). قبلاً همین باعث می‌شد خلاصه با متن
        «خطا در خلاصه‌سازی: Expecting value…» ذخیره شود.
        """
        meta = ""
        for attempt in (1, 2):
            resp = await self.client.chat.completions.create(**kwargs)
            ch = resp.choices[0]
            content = (ch.message.content or "").strip()
            if content:
                return content
            meta = f"finish={ch.finish_reason}"
            logger.warning(
                "LLM returned empty content (attempt %s, %s) — retrying", attempt, meta
            )
        raise RuntimeError(f"LLM returned empty content twice ({meta})")

    async def _analyze_step1(self, content: ExtractedContent) -> dict:
        """پاس ۱ — تحلیل ساختارمند (LLM-Wiki two-step ingest).

        خروجی: entities/concepts/connections/contradictions. خطا → دیکشنری خالی
        (تا پاس ۲ همیشه اجرا شود و ingest شکست نخورد).
        """
        prompt = f"""You are a knowledge-base analyst (LLM-Wiki pattern, step 1 of 2).
Analyze this content and extract STRUCTURED metadata. Read carefully — do not invent.

Content Title: {content.title[:200]}
Content URL: {content.url}
Platform: {content.platform}

Content:
{content.full_text[:5000]}

Return ONLY a JSON object with these fields:
- entities: array of 0-8 named things concretely mentioned (people, companies, products, models) — Persian or English as written in content
- concepts: array of 2-6 CANONICAL concepts. Persian preferred; standard English acronyms (RAG, LLM, PDF) are allowed. Each item MUST be 1-3 words and generic enough to repeat across documents (e.g. «مهندسی معکوس», «گراف دانش», «مدل زبانی», «بازیابی اطلاعات»). Never use document-specific compound phrases, headings, sentences, or comma-joined lists — those never match other documents.
- connections: array of 0-4 short Persian phrases describing how this connects to broader AI/business topics (e.g. "مرتبط با معماری حافظه عامل‌ها")
- contradictions: array of 0-3 Persian sentences — claims here that likely conflict with mainstream/previous knowledge, else []

Respond with ONLY the JSON object."""
        try:
            raw = await self._chat(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-returning analyst."},
                    {"role": "user", "content": prompt},
                ],
                **_max_tokens_kwargs(),
            )
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1:
                return {}
            result = json.loads(raw[start:end + 1])
            # فقط کلیدهای مجاز، فقط لیست رشته‌ای
            out = {}
            for key in ("entities", "concepts", "connections", "contradictions"):
                val = result.get(key, [])
                if isinstance(val, list):
                    out[key] = [str(v).strip()[:120] for v in val if str(v).strip()][:8]
                else:
                    out[key] = []
            return out
        except Exception as e:
            logger.warning(f"Step-1 analysis failed (non-fatal): {e}")
            return {}

    async def analyze(self, content: ExtractedContent) -> AIAnalysis:
        """Analyze and summarize content using the configured model."""
        if not content.full_text or content.full_text.startswith("Failed"):
            return AIAnalysis(
                summary_fa="❌ امکان استخراج محتوا وجود نداشت.",
                summary_en="❌ Could not extract content.",
                category="Error",
                tags=["error"],
                priority="Low"
            )

        prompt = f"""You are a knowledge management assistant. Analyze the following content and return a JSON object.

Content Title: {content.title[:200]}
Content URL: {content.url}
Platform: {content.platform}

Content:
{content.full_text[:5000]}

Return a JSON object with these fields:
- summary_fa: خلاصه ۳-۴ خطی به فارسی روان
- summary_en: 3-4 line summary in English
- key_points: array of 3-5 key points، هر نکته یک جمله کوتاه فارسی روان (فارسی بنویس، انگلیسی نه)
- category: one of ["AI/ML", "Technology", "Business", "Science", "Design", "Programming", "Social", "News", "Tutorial", "Opinion", "General"]
- tags: array of 3-5 relevant tags (lowercase, in English)
- priority: one of ["High", "Medium", "Low"] based on content importance

Respond with ONLY the JSON object, no other text."""

        try:
            raw = await self._chat(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-returning assistant."},
                    {"role": "user", "content": prompt},
                ],
                # gpt-5.x فقط temperature پیش‌فرض را قبول می‌کند — پارامتر نفرستاده می‌شود
                **_max_tokens_kwargs(),
            )
        except Exception as e:
            if is_quota_error(e):
                # سهمیهٔ AI تمام شده — خطا را بالا بفرست تا caller رکورد را
                # با flag ذخیره کند (نه با متن خطا در summary)
                logger.error(f"Summarization blocked by AI quota (429): {e}")
                raise QuotaExceededError(str(e)[:200]) from e
            logger.error(f"Summarization error: {e}")
            # هیچ متن خطایی در فیلد summary ذخیره نمی‌شود؛ caller پیام کاربرپسند می‌دهد
            raise SummarizationError(str(e)[:300]) from e

        try:
            # استخراج JSON خالص از پاسخ (ممکنه مدل متن اضافی بذاره)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start:end + 1]

            result = json.loads(raw)
        except Exception as e:
            logger.error(f"Summarization error: {e}")
            return AIAnalysis(
                summary_fa=f"خطا در خلاصه‌سازی: {str(e)}",
                summary_en=f"Summarization error: {str(e)}",
                category="General",
                tags=["error"],
                priority="Low"
            )

        # LLM-Wiki 1a: پاس ۱ (تحلیل ساختارمند) — خطا/خالی → فیلدهای خالی (legacy سازگار)
        step1 = await self._analyze_step1(content)
        return AIAnalysis(
            summary_fa=result.get("summary_fa", "خلاصه در دسترس نیست."),
            summary_en=result.get("summary_en", "Summary not available."),
            key_points=result.get("key_points", []),
            category=result.get("category", "General"),
            tags=result.get("tags", []),
            priority=result.get("priority", "Medium"),
            entities=step1.get("entities", []),
            concepts=step1.get("concepts", []),
            connections=step1.get("connections", []),
            contradictions=step1.get("contradictions", []),
        )

    async def summarize_ai(self, content: ExtractedContent) -> AIAnalysis:
        """فقط فراخوانی AI خلاصه‌سازی (بدون پاس ۱ و بدون فالبک خطا).

        کاربرد در ریکاوری: خطای سهمیه مستقیم به بالا می‌رسد؛ خروجی موفق
        خلاصه‌های خام است (بدون mutate کردن متن خطا در summary).
        """
        prompt = f"""You are a knowledge management assistant. Analyze the following content and return a JSON object.

Content Title: {content.title[:200]}
Content URL: {content.url}
Platform: {content.platform}

Content:
{content.full_text[:5000]}

Return a JSON object with these fields:
- summary_fa: خلاصه ۳-۴ خطی به فارسی روان
- summary_en: 3-4 line summary in English
- key_points: array of 3-5 key points، هر نکته یک جمله کوتاه فارسی روان (فارسی بنویس، انگلیسی نه)
- category: one of ["AI/ML", "Technology", "Business", "Science", "Design", "Programming", "Social", "News", "Tutorial", "Opinion", "General"]
- tags: array of 3-5 relevant tags (lowercase, in English)
- priority: one of ["High", "Medium", "Low"] based on content importance

Respond with ONLY the JSON object, no other text."""

        raw = await self._chat(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": "You are a precise JSON-returning assistant."},
                {"role": "user", "content": prompt},
            ],
            **_max_tokens_kwargs(),
        )
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        result = json.loads(raw)
        return AIAnalysis(
            summary_fa=result.get("summary_fa", ""),
            summary_en=result.get("summary_en", ""),
            key_points=result.get("key_points", []),
            category=result.get("category", "General"),
            tags=result.get("tags", []),
            priority=result.get("priority", "Medium"),
        )