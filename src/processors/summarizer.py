"""AI summarization processor using official Google GenAI SDK."""
import json
import google.generativeai as genai
from ..config import settings
from ..models import ExtractedContent, AIAnalysis


class ContentSummarizer:
    """Summarize content using Google Gemini SDK."""
    
    def __init__(self):
        genai.configure(api_key=settings.openai_api_key)  # using the stored API key field
        self.model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
    
    async def analyze(self, content: ExtractedContent) -> AIAnalysis:
        """Analyze and summarize content using Gemini."""
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

            # Call Gemini asynchronously
            response = await self.model.generate_content_async(prompt)
            result = json.loads(response.text)
            
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
                summary_fa=f"خطا در خلاصه‌سازی با جمنای: {str(e)}",
                summary_en=f"Gemini summarization error: {str(e)}",
                category="General",
                tags=["error"],
                priority="Low"
            )
