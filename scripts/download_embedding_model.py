"""دانلود مدل embedding (e5-small int8 ONNX) به models/e5-small-int8/.

چرا این اسکریپت هست: فایل مدل ۱۱۸ مگابایت است و در گیت کامیت نمی‌شود؛
هر نصب تازه با یک دستور مدل را می‌گیرد.
"""
from __future__ import annotations

import os
import shutil
import sys

from huggingface_hub import hf_hub_download

REPO = "intfloat/multilingual-e5-small"
FILES = {
    "onnx/model_qint8_avx512_vnni.onnx": "model.onnx",  # ۱۱۸MB نسخهٔ رسمی int8
    "tokenizer.json": "tokenizer.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
}
DEST = os.environ.get(
    "EMBED_MODEL_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "e5-small-int8"),
)


def main() -> int:
    os.makedirs(DEST, exist_ok=True)
    for remote, local in FILES.items():
        target = os.path.join(DEST, local)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            print(f"skip (exists): {local}")
            continue
        path = hf_hub_download(REPO, remote)
        shutil.copy(path, target)
        print(f"downloaded: {local} ({os.path.getsize(target) / 1e6:.1f} MB)")
    print(f"model ready at {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
