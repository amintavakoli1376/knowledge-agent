"""Text chunking utilities for RAG."""
from typing import List


def chunk_text(text: str, max_chars: int = 900, overlap: int = 100) -> List[str]:
    """Split text into overlapping chunks by character count (best-effort at sentence boundaries).

    For Persian, sentence boundaries are '؟', '!', '.', and newlines.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text.strip()]

    chunks: List[str] = []
    start = 0
    n = len(text)

    # Persian/English sentence terminators
    terminators = ['؟', '!', '.', '\n']

    while start < n:
        end = min(start + max_chars, n)

        if end < n:
            # Try to cut at a sentence boundary near the end
            window = text[start:end]
            last_cut = -1
            for term in terminators:
                idx = window.rfind(term)
                if idx > max_chars * 0.5:
                    last_cut = max(last_cut, idx)

            if last_cut != -1 and len(window) - last_cut < max_chars * 0.5:
                end = start + last_cut + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break
        start = max(end - overlap, start + 1)

    return chunks