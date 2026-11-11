"""Pure text helpers (no heavy imports, easy to unit-test)."""
from __future__ import annotations

from .models import Sentence, Word

_ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "jr.", "sr.", "etc.", "e.g.", "i.e."}
_TERMINAL = (".", "?", "!")


def _is_fragment(prev: str, text: str) -> bool:
    """Whisper sometimes splits one word into tokens: "$100" + ",000", "50" + "%", "it" + "'s"."""
    if text[:1] in ",." and text[1:2].isdigit() and prev[-1:].isdigit():
        return True
    return text[:1] == "%" or text.lower() in {"'s", "'t", "'re", "'ve", "'ll", "'d", "'m"}


def merge_fragments(words: list[Word]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        if out and _is_fragment(out[-1].text, w.text):
            p = out[-1]
            out[-1] = Word(text=p.text + w.text, start=p.start, end=w.end, prob=round(min(p.prob, w.prob), 3))
        else:
            out.append(w)
    return out


def _ends_sentence(text: str) -> bool:
    t = text.lower().rstrip('"\')')
    return t.endswith(_TERMINAL) and t not in _ABBREV


def group_sentences(words: list[Word], max_words: int = 30, max_gap_s: float = 1.0) -> list[Sentence]:
    """Split a word stream into sentence-like units.

    Breaks on terminal punctuation, on long pauses, and (for run-ons) on a comma
    once past 60% of max_words, or hard at max_words.
    """
    sentences: list[Sentence] = []
    cur: list[Word] = []

    def flush() -> None:
        if cur:
            sentences.append(
                Sentence(
                    id=len(sentences),
                    start=cur[0].start,
                    end=cur[-1].end,
                    text=" ".join(w.text for w in cur),
                    words=list(cur),
                )
            )
            cur.clear()

    for w in words:
        if cur and w.start - cur[-1].end > max_gap_s:
            flush()
        cur.append(w)
        n = len(cur)
        if (
            _ends_sentence(w.text)
            or n >= max_words
            or (n >= 0.6 * max_words and w.text.endswith((",", ";", ":")))
        ):
            flush()
    flush()
    return sentences
