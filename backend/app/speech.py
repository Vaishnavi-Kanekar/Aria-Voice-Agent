"""Text helpers for the voice path: sentence chunking for streaming TTS and making text speakable."""
from __future__ import annotations

import re

_SENT_END = re.compile(r"[.!?।]+[\"')\]]*\s+")  # sentence punctuation followed by whitespace
_CLAUSE = re.compile(r"[,;:—–]\s+")
_MARKDOWN = re.compile(r"[*_`#>~]+")


def strip_markdown(text: str) -> str:
    return _MARKDOWN.sub("", text)


class SentenceSplitter:
    """Turns a token stream into speakable chunks.

    We only cut after punctuation + whitespace, so '3.5' or '2,500' are never split.
    The first chunk of a reply may also be cut at a comma once it is long enough, so
    the customer hears audio sooner.
    """

    def __init__(self, first_chunk_min_chars: int = 28) -> None:
        self.buf = ""
        self.first_done = False
        self.min_first = first_chunk_min_chars

    def _next_cut(self) -> int | None:
        sentence = _SENT_END.search(self.buf)
        if not self.first_done:
            clause = _CLAUSE.search(self.buf, self.min_first)
            if clause and (sentence is None or clause.end() < sentence.end()):
                return clause.end()
        return sentence.end() if sentence else None

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while (cut := self._next_cut()) is not None:
            piece = self.buf[:cut].strip()
            self.buf = self.buf[cut:]
            if piece:
                out.append(piece)
                self.first_done = True
        return out

    def flush(self) -> str | None:
        rest = self.buf.strip()
        self.buf = ""
        if rest:
            self.first_done = True
        return rest or None


def speakable(text: str) -> str:
    """Rewrite text so a TTS engine reads it the way a person would."""
    t = strip_markdown(text)
    t = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)", r"\1 rupees", t)  # rupee sign -> 'rupees'
    t = re.sub(r"\bRs\.?\s?([\d,]+)", r"\1 rupees", t)
    t = re.sub(r"\b(?:order\s+)?ORD-(\d+)\b", lambda m: "order " + " ".join(m.group(1)), t, flags=re.I)
    t = re.sub(
        r"\b([A-Z]{2,4})-(\d{3,})\b",  # tracking IDs: BD-982103 -> 'B D 9 8 2 1 0 3'
        lambda m: " ".join(m.group(1)) + " " + " ".join(m.group(2)),
        t,
    )
    t = re.sub(r"\bCOD\b", "cash on delivery", t)
    return re.sub(r"\s+", " ", t).strip()
