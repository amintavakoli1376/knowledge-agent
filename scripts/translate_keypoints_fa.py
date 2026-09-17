"""Translate English key_points rows to Persian — resumable, batch, runs in background."""
import sys, asyncio, json, sqlite3, time
sys.path.insert(0, '/root/knowledge-agent-repo')
from src.config import settings
from openai import AsyncOpenAI

DB = '/root/knowledge-agent-repo/data/knowledge.db'
client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

def is_en(s):
    try:
        pts = json.loads(s)
    except Exception:
        return False
    if not isinstance(pts, list) or not pts:
        return False
    letters = [c for c in " ".join(pts) if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if ord(c) < 128) / len(letters) > 0.5

PROMPT = """Translate each bullet point into fluent Persian (فارسی روان). Keep technical terms like FHIR, RPM, LLM, AI in Latin. Return a JSON array of strings, same order, same count. Respond with ONLY the JSON array."""

async def main():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT id, key_points FROM content WHERE key_points NOT IN ('', '[]')").fetchall()
    targets = [(i, kp) for i, kp in rows if is_en(kp)]
    print(f"remaining to translate: {len(targets)}", flush=True)
    done, failed = 0, []
    for i, kp in targets:
        try:
            pts = json.loads(kp)
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-returning translator."},
                    {"role": "user", "content": PROMPT + "\n\n" + json.dumps(pts, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            s, e = raw.find("["), raw.rfind("]")
            arr = json.loads(raw[s:e + 1])
            if isinstance(arr, list) and len(arr) == len(pts):
                db.execute("UPDATE content SET key_points=? WHERE id=?", (json.dumps(arr, ensure_ascii=False), i))
                db.commit()
                done += 1
            else:
                failed.append((i, "count mismatch"))
        except Exception as ex:
            failed.append((i, str(ex)[:80]))
        time.sleep(0.2)
    print(f"translated: {done} | failed: {len(failed)} {failed[:5]}", flush=True)

asyncio.run(main())
