"""Tiny BM25 keyword search used to retrieve approved document passages.

No embeddings service needed; good enough for a corpus of a few hundred passages.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

STOP = set(
    "a an and are as at be by can do does for from how i if in is it its me my of on or "
    "should so that the their there this to use using was what when where which who why "
    "will with you your".split()
)


def stem(w: str) -> str:
    """Very light stemming so "soldering", "solders" and "solder" match."""
    for suf, keep in (("ing", 3), ("ers", 4), ("ed", 4), ("es", 4), ("s", 4)):
        if w.endswith(suf) and len(w) - len(suf) >= keep and not (suf == "s" and w.endswith("ss")):
            w = w[: -len(suf)]
            break
    if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiouls":  # "cutt" -> "cut"
        w = w[:-1]
    return w


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP:
            continue
        out.append(stem(w))
    return out


@dataclass
class Index:
    docs: list[dict[str, Any]] = field(default_factory=list)
    k1: float = 1.4
    b: float = 0.75

    def __post_init__(self):
        self._tf = [Counter(tokens(d["text"])) for d in self.docs]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self.docs) or 1
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[float, dict[str, Any]]]:
        q = tokens(query)
        scored = []
        for i, tf in enumerate(self._tf):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self._idf[t] * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * self._len[i] / self._avg)
                )
            if s > 0:
                scored.append((s, self.docs[i]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]
