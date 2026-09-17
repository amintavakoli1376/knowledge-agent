"""AI summarization processor — OpenAI-compatible (any base_url/model)."""
import json
import logging
from openai import OpenAI, AsyncOpenAI
from ..config import settings
from ..models import ExtractedContent, AIAnalysis

logger = logging.getLogger(__name__)


class ContentSummarizer:
    """Summarize content using any OpenAI-compatible endpoint."""

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

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
- concepts: array of 2-6 core technical/business concepts this content is ABOUT (short Persian phrases, e.g. "حافظه عامل‌های هوش مصنوعی", "گراف دانش")
- connections: array of 0-4 short Persian phrases describing how this connects to broader AI/business topics (e.g. "مرتبط با معماری حافظه عامل‌ها")
- contradictions: array of 0-3 Persian sentences — claims here that likely conflict with mainstream/previous knowledge, else []

Respond with ONLY the JSON object."""
        try:
            resp = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-returning analyst."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
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
            resp = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-returning assistant."},
                    {"role": "user", "content": prompt},
                ],
                # gpt-5.x فقط temperature پیش‌فرض را قبول می‌کند — پارامتر نفرستاده می‌شود
            )

            raw = resp.choices[0].message.content or ""
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

        except Exception as e:
            logger.error(f"Summarization error: {e}")
            return AIAnalysis(
                summary_fa=f"خطا در خلاصه‌سازی: {str(e)}",
                summary_en=f"Summarization error: {str(e)}",
                category="General",
                tags=["error"],
                priority="Low"
            )