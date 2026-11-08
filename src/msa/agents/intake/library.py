"""Finding and reading manuals.

`find_manual` stands in for "search the manufacturer's site": it looks in the makerspace's
manual library folder (data/makerspace/manuals) by brand and model. A real deployment would
add a web lookup here; whatever it finds is still only a draft source until staff approve.
Uploaded PDFs are converted to text with `[p.N]` page markers so citations can point at pages.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from msa.core.config import DATA_DIR

LIBRARY = DATA_DIR / "manuals"


def slug(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def find_manual(brand: str, model_number: str, library: Path | None = None) -> Path | None:
    lib = library or LIBRARY
    if not lib.is_dir():
        return None
    brand_s = slug(brand.split()[0]) if brand else ""
    model_s = slug(model_number)
    exact = lib / f"{brand_s}-{model_s}.md"
    if exact.exists():
        return exact
    for f in sorted(lib.glob("*.md")):
        if model_s and model_s in f.stem and (not brand_s or f.stem.startswith(brand_s)):
            return f
    return None


def read_upload(filename: str, data: bytes) -> str:
    """Text of an uploaded manual. PDFs get one [p.N] marker per page."""
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(f"[p.{i}]\n{_pdf_text_to_markdown(text)}")
        return "\n\n".join(pages)
    return data.decode("utf-8", errors="replace")


def _pdf_text_to_markdown(text: str) -> str:
    """Best effort: short title-case lines become headings, '•' lines become bullets."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("•", "●", "▪", "-", "*")):
            out.append("- " + line.lstrip("•●▪-* ").strip())
        elif len(line) < 60 and not line.endswith((".", ":", ",")) and line[:1].isupper() \
                and len(line.split()) <= 6:
            out.append(f"## {line}")
        else:
            out.append(line)
    return "\n".join(out)
