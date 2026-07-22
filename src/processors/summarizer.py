"""AI summarization processor."""
import json
from openai import AsyncOpenAI
from ..config import settings
from ..models import ExtractedContent, AIAnalysis


class ContentSummarizer:
    """Summarize content using OpenAI LLM."""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
    
    async def analyze(self, content: ExtractedContent) -> AIAnalysis:
        """Analyze and summarize content."""
        if not content.full_text or content.full_text.startswith("Failed"):
            return AIAnalysis(
                summary_fa="❌ امکان استخراج محتوا وجود نداشت.",
                summary_en="❌ Could not extract content.",
                category="Error",
                tags=["error"],
                priority="Low"
            )
        
        try:
            prompt = f"""You are a knowledge management assistant. Analyze the following content and return a JSON object.

Content Title: {content.title[:200]}
Content URL: {content.url}
Platform: {content.platform}

Content:
{content.full_text[:5000]}

Return a JSON object with these fields:
- summary_fa: خلاصه ۳-۴ خطی به فارسی روان
- summary_en: 3-4 line summary in English
- key_points: array of 3-5 key points (in English)
- category: one of ["AI/ML", "Technology", "Business", "Science", "Design", "Programming", "Social", "News", "Tutorial", "Opinion", "General"]
- tags: array of 3-5 relevant tags (lowercase, in English)
- priority: one of ["High", "Medium", "Low"] based on content importance

Respond with ONLY the JSON object, no other text."""

            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a knowledge management assistant. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=800,
                temperature=0.3
            )
            
            result = json.loads(response.choices[0].message.content)
            
            return AIAnalysis(
                summary_fa=result.get("summary_fa", "خلاصه در دسترس نیست."),
                summary_en=result.get("summary_en", "Summary not available."),
                key_points=result.get("key_points", []),
                category=result.get("category", "General"),
                tags=result.get("tags", []),
                priority=result.get("priority", "Medium"),
            )
            
        except Exception as e:
            return AIAnalysis(
                summary_fa=f"خطا در خلاصه‌سازی: {str(e)}",
                summary_en=f"Summarization error: {str(e)}",
                category="General",
                tags=["error"]
            )
