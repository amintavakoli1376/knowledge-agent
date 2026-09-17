# Rebuild all embeddings with a Persian-summary "language bridge" chunk-0 (resumable, background).
import sys, sqlite3
sys.path.insert(0, '/root/knowledge-agent-repo')
from src.rag.embedder import get_embedder
from src.rag.chunker import chunk_text
from src.rag import vector_store
from src.storage.sqlite_store import _get_connection

conn = _get_connection()
rows = conn.execute("SELECT id, full_text, summary_fa FROM content ORDER BY id").fetchall()
done = {r[0] for r in conn.execute("SELECT DISTINCT content_id FROM embeddings WHERE chunk_index=0")}
conn.close()

emb = get_embedder()
total = 0
skipped = 0
for cid, full, sfa in rows:
    if not (full or "").strip():
        continue
    if cid in done:
        skipped += 1
        continue
    chunks = chunk_text(full)
    if not chunks:
        continue
    bridge = ((sfa or "").strip() + "\n" + chunks[0]).strip()
    vectors = emb.embed([bridge] + chunks[1:])
    vector_store.store_chunks(cid, [bridge] + chunks[1:], vectors)
    total += len(chunks)
    print(f"cid {cid}: {len(chunks)} chunks (skipped {skipped} so far)", flush=True)
print(f"DONE — re-indexed {total} chunks across docs, skipped(already-done): {skipped}")
