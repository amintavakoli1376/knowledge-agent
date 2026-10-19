"""Arena-off memory probe + int8 vs fp32 drift check (no torch)."""
import json, os, sys, time
import numpy as np

def rss():
    for l in open("/proc/self/status"):
        if l.startswith("VmRSS:"): return round(int(l.split()[1])/1024, 1)

import onnxruntime as ort
from tokenizers import Tokenizer

MODEL = "/root/knowledge-agent-repo/models/e5-small-int8"
tok = Tokenizer.from_file(f"{MODEL}/tokenizer.json")
tok.enable_truncation(max_length=512)
tok.enable_padding(pad_id=1, pad_token="<pad>")

def session(path, arena: bool):
    o = ort.SessionOptions()
    o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    o.enable_cpu_mem_arena = arena
    o.intra_op_num_threads = 2
    return ort.InferenceSession(path, sess_options=o, providers=["CPUExecutionProvider"])

def embed(sess, texts):
    e = tok.encode_batch(texts)
    ids = np.asarray([x.ids for x in e], dtype=np.int64)
    m = np.asarray([x.attention_mask for x in e], dtype=np.int64)
    h = sess.run(None, {"input_ids": ids, "attention_mask": m, "token_type_ids": np.zeros_like(ids)})[0]
    mask = m[..., None].astype(np.float32)
    v = (h*mask).sum(1) / np.clip(mask.sum(1), 1e-9, None)
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-12, None)

res = {"rss_start": rss()}
t0 = time.time(); s_arena = session(f"{MODEL}/model.onnx", True); res["load_arena_on_sec"] = round(time.time()-t0,2)
v1 = embed(s_arena, ["query: حافظهٔ عامل در سیستم‌های چندعاملی"])
res["rss_arena_on"] = rss()

fp32 = "/tmp/e5-fp32.onnx"
if os.path.exists(fp32):
    t1 = time.time()
    s_noarena = session(fp32, False)
    res["load_fp32_noarena_sec"] = round(time.time()-t1, 2)
    v2 = embed(s_noarena, ["query: حافظهٔ عامل در سیستم‌های چندعاملی"])
    res["rss_fp32_loaded"] = rss()
    cos = float(np.dot(v1[0], v2[0]))
    # drift on a larger sample
    texts = ["passage: " + t for t in [
        "گراف دانش و بازیابی اطلاعات در سیستم‌های RAG",
        "قیمت تتر و بازار ارز دیجیتال ایران",
        "دستور پخت آبگوشت سنتی",
        "حافظهٔ بلندمدت در عامل‌های هوش مصنوعی",
        "شبکهٔ عصبی ترانسفورمر و توجه چندسر",
    ]]
    a = embed(s_arena, texts); b = embed(s_noarena, texts)
    cosines = [float(np.dot(x, y)) for x, y in zip(a, b)]
    res["int8_vs_fp32"] = {
        "same_text_cosine": round(cos, 5),
        "mean_cosine": round(float(np.mean(cosines)), 5),
        "min_cosine": round(float(np.min(cosines)), 5),
        "max_abs_diff_of_similarity_matrix": round(float(np.max(np.abs(a @ a.T - b @ b.T))), 5),
    }
print(json.dumps(res, ensure_ascii=False, indent=2))
