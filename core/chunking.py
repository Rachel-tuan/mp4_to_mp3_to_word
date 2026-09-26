"""Splits long transcripts into LLM-safe chunks.

Kept independent from the DeepSeek service so it can be unit tested and
reused by any future LLM provider. Splitting is done on paragraph/sentence
boundaries where possible so we don't cut a sentence in half mid-chunk.
"""
from __future__ import annotations

import re
from typing import List

# Rough char budget per chunk. DeepSeek's context window is large, but we
# keep chunks conservative so a single chunk request/response stays fast
# and cheap, and so a mid-stream failure only costs one chunk of work.
DEFAULT_MAX_CHARS = 6000

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s*")


def needs_chunking(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> bool:
    return len(text) > max_chars


def split_transcript(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> List[str]:
    """Split ``text`` into chunks each <= ``max_chars`` (best effort).

    Strategy: split into paragraphs first, then greedily pack paragraphs
    into chunks; a paragraph that is itself too long gets split on
    sentence boundaries.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    units: List[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            units.append(para)
        else:
            sentences = _SENTENCE_SPLIT_RE.split(para)
            buf = ""
            for sent in sentences:
                if not sent:
                    continue
                if len(buf) + len(sent) <= max_chars:
                    buf += sent
                else:
                    if buf:
                        units.append(buf)
                    # A single sentence longer than max_chars: hard cut.
                    if len(sent) > max_chars:
                        for i in range(0, len(sent), max_chars):
                            units.append(sent[i:i + max_chars])
                        buf = ""
                    else:
                        buf = sent
            if buf:
                units.append(buf)

    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = unit
    if current:
        chunks.append(current)

    return chunks
