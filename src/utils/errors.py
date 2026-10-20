"""تبدیل خطای فنی به پیام قابل فهم برای کاربر.

قاعده: جزئیات خطا فقط در لاگ می‌ماند؛ کاربر پیام کوتاه و قابل اقدام می‌بیند.
هیچ‌جای پیام کاربر نباید نام کلاس استثنا، کد وضعیت HTTP یا متن خام کتابخانه باشد.
"""
from __future__ import annotations


def _text(exc: BaseException) -> str:
    return f"{type(exc).__name__} {exc}".lower()


def friendly_error(exc: BaseException, kind: str = "generic") -> str:
    t = _text(exc)

    if "quota" in t or "429" in t or "rate limit" in t or "insufficient" in t:
        return "سهمیهٔ هوش مصنوعی فعلاً تمام شده است. چند دقیقهٔ دیگر دوباره امتحان کن."

    if "timeout" in t or "timed out" in t or "readtimeout" in t:
        return "خواندن این صفحه طول کشید و متوقف شد. یک‌بار دیگر امتحان کن."

    if "401" in t or "403" in t or "unauthorized" in t or "forbidden" in t or "login" in t or "signup" in t:
        return "این صفحه بدون ورود باز نمی‌شود، پس ربات نمی‌تواند متنش را بخواند."

    if "404" in t or "not found" in t or "gone" in t:
        return "این آدرس پیدا نشد؛ احتمالاً حذف شده یا نشانی اشتباه است."

    if "connection" in t or "network" in t or "dns" in t or "unreachable" in t or "resolve" in t:
        return "اتصال به این سایت برقرار نشد. اتصال شبکه را بررسی کن و دوباره بفرست."

    if isinstance(exc, (ValueError, KeyError)) and "json" in t:
        return "پاسخ هوش مصنوعی قابل خواندن نبود. دوباره امتحان کن."

    if "pdf" in t and ("empty" in t or "no text" in t or "scanned" in t):
        return "این فایل متنی برای خواندن ندارد (احتمالاً اسکن تصویری است)."

    if "unsupported" in t or "not supported" in t:
        return "این نوع محتوا پشتیبانی نمی‌شود."

    fallback = {
        "extract": "متن این لینک خوانده نشد. یک‌بار دیگر بفرست یا متن را دستی ارسال کن.",
        "rag": "جست‌وجو در پایگاه دانش انجام نشد. سؤالت را یک‌بار دیگر بفرست.",
        "document": "این فایل پردازش نشد. اگر ممکن است نسخهٔ متنی یا PDF آن را بفرست.",
        "save": "ذخیره انجام نشد. دوباره تلاش کن؛ اگر تکرار شد اطلاع بده.",
        "llm": "ساخت خلاصه انجام نشد. محتوا ذخیره شد و خلاصه بعداً ساخته می‌شود.",
        "generic": "انجام نشد. یک‌بار دیگر تلاش کن؛ اگر تکرار شد اطلاع بده.",
    }
    return fallback.get(kind, fallback["generic"])
