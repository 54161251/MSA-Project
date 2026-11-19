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


def _contains_run(parts: list[str], run: list[str]) -> bool:
    n = len(run)
    return n > 0 and any(parts[i:i + n] == run for i in range(len(parts) - n + 1))


def find_manual(brand: str, model_number: str, library: Path | None = None) -> Path | None:
    """Match on whole name parts: model "TS10" finds kestrel-ts10.md, but model "10" does not
    (a wrong manual would mean a wrong SOP). Several matches -> None, so staff upload it."""
    lib = library or LIBRARY
    if not lib.is_dir():
        return None
    brand_s = slug(brand.split()[0]) if brand and brand.split() else ""
    model_s = slug(model_number)
    if not model_s:
        return None
    exact = lib / (f"{brand_s}-{model_s}.md" if brand_s else f"{model_s}.md")
    if exact.exists():
        return exact
    hits = []
    for f in sorted(lib.glob("*.md")):
        parts = f.stem.split("-")
        if brand_s and parts[0] != brand_s:
            continue
        if _contains_run(parts[1:] if brand_s else parts, model_s.split("-")):
            hits.append(f)
    return hits[0] if len(hits) == 1 else None


def read_upload(filename: str, data: bytes) -> str:
    """Text of an uploaded manual. PDFs get one [p.N] marker per page."""
    name = filename.lower()
    if not data:
        raise ValueError("The uploaded file is empty.")
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                pages.append(f"[p.{i}]\n{_pdf_text_to_markdown(text)}")
        except Exception as e:  # pypdf raises many types for damaged files
            raise ValueError(f"Could not read this PDF ({type(e).__name__}).") from e
        text = "\n\n".join(pages)
        if not re.search(r"[A-Za-z]{3}", re.sub(r"\[p\.\d+\]", "", text)):
            raise ValueError("This PDF has no text layer (is it a scan?). Upload a text PDF, "
                             ".txt or .md version of the manual.")
        return text
    if not name.endswith((".md", ".txt", ".markdown")) and b"\x00" in data[:2048]:
        raise ValueError("Upload the manual as PDF, .txt or .md.")
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        raise ValueError("The uploaded file is empty.")
    return text


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
