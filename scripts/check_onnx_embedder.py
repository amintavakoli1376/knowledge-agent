"""Smoke + RAM + quality check for the ONNX int8 embedder (no torch)."""
import json
import os
import subprocess
import sys
import time

def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1

def hwm_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024, 1)
    return -1

sys.path.insert(0, "/root/knowledge-agent-repo")
out = {"rss_before": rss_mb()}
t0 = time.time()
from src.rag.embedder import get_embedder, EMBEDDING_DIM
emb = get_embedder()
out["import_cost_sec"] = round(time.time() - t0, 2)
out["rss_after_import"] = rss_mb()

# first encode triggers model load
t1 = time.time()
vec = emb.embed_query("حافظهٔ عامل در سیستم‌های چندعاملی چگونه پیاده‌سازی می‌شود؟")
out["first_encode_sec"] = round(time.time() - t1, 2)
out["rss_after_load"] = rss_mb()
out["dim"] = len(vec)

passages = [
    ("R", "حافظهٔ عامل‌ها در سیستم‌های چندعاملی معمولاً با یک لایهٔ ذخیره‌سازی برداری و خلاصه‌سازی تجربه‌ها پیاده‌سازی می‌شود."),
    ("N1", "قیمت تتر امروز در بازار ایران با نوسان همراه بود و معامله‌گران منتظر تصمیم بانک مرکزی هستند."),
    ("N2", "دستور پخت آبگوشت سنتی با گوشت گوسفند و نخود و لوبیا سفید."),
    ("N3", "آنتروپی در ترمودینامیک معیاری از بی‌نظمی سیستم است و در چرخهٔ کارنو کاربرد دارد."),
]
t2 = time.time()
pvecs = emb.embed_passages([p for _, p in passages])
out["encode_4_passages_sec"] = round(time.time() - t2, 2)

scored = sorted(
    ((tag, round(emb.similarity(vec, v), 4)) for (tag, _), v in zip(passages, pvecs)),
    key=lambda x: -x[1],
)
out["ranking"] = scored
out["top1_is_relevant"] = scored[0][0] == "R"

# batch throughput
t3 = time.time()
emb.embed_passages(["متن تست شماره %d دربارهٔ گراف دانش و بازیابی اطلاعات." % i for i in range(32)])
out["batch32_sec"] = round(time.time() - t3, 2)

out["rss_end"] = rss_mb()
out["peak_hwm"] = hwm_mb()

# torch must not be in the process at all
out["torch_imported"] = "torch" in sys.modules
out["sentence_transformers_imported"] = "sentence_transformers" in sys.modules

print(json.dumps(out, ensure_ascii=False, indent=2))
