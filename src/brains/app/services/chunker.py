import math
import re

APPROX_TOKENS_PER_WORD = 1.3
TARGET_MIN_TOKENS = 400
TARGET_MAX_TOKENS = 700


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text.split()) * APPROX_TOKENS_PER_WORD)


def _split_at_words(text: str, max_tokens: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if _estimate_tokens(candidate) > max_tokens and current:
            chunks.append(current.strip())
            current = word
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _split_at_sentences(text: str, max_tokens: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if _estimate_tokens(sentence) > max_tokens:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_at_words(sentence, max_tokens))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if _estimate_tokens(candidate) > max_tokens and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_text(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    segments: list[str] = []
    for para in paragraphs:
        if _estimate_tokens(para) > TARGET_MAX_TOKENS:
            segments.extend(_split_at_sentences(para, TARGET_MAX_TOKENS))
        else:
            segments.append(para)

    result: list[str] = []
    buffer = ""
    for segment in segments:
        if not buffer:
            buffer = segment
        else:
            candidate = f"{buffer}\n\n{segment}"
            if _estimate_tokens(candidate) <= TARGET_MAX_TOKENS:
                buffer = candidate
            else:
                result.append(buffer.strip())
                buffer = segment
        if _estimate_tokens(buffer) >= TARGET_MIN_TOKENS:
            result.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        result.append(buffer.strip())
    return result
