# Force re-embed every doc whose chunk-0 is NOT bridged (doesn't start with its summary_fa).
# The earlier grouped run's done-set was too eager; this targets the stragglers precisely.
import sys
sys.path.insert(0, '/root/knowledge-agent-repo')
from src.rag.embedder import get_embedder
from src.rag.chunker import chunk_text
from src.rag import vector_store
from src.storage.sqlite_store import _get_connection

conn = _get_connection()
rows = conn.execute(
    "SELECT c.id, c.full_text, c.summary_fa, substr(e.chunk_text, 1, 60) "
    "FROM content c JOIN embeddings e ON e.content_id = c.id AND e.chunk_index = 0"
).fetchall()
conn.close()

emb = get_embedder()
fixed = 0
for cid, full, sfa, c0 in rows:
    sfa_head = (sfa or "").strip()[:40].replace("\n", " ")
    if sfa_head and (c0 or "").lstrip().startswith(sfa_head[:25]):
        continue
    if not (full or "").strip():
        continue
    chunks = chunk_text(full)
    if not chunks:
        continue
    bridge = ((sfa or "").strip() + "\n" + chunks[0]).strip()
    vectors = emb.embed([bridge] + chunks[1:])
    vector_store.store_chunks(cid, [bridge] + chunks[1:], vectors)
    fixed += 1
    print(f"fixed cid {cid}", flush=True)
print("DONE — bridged docs:", fixed)
