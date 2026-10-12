"""sec.gov documents outside EDGAR filings: staff reports, rules, speeches, press releases.

Filings are read by accession (get_filing_content, get_filing_sections, get_xbrl_concepts);
this tool reads the rest of www.sec.gov, which had no route (AREX §13.1 item 11: run
arex-20261012T005648Z-148211 could read the SEC's GameStop staff report only through
news summaries).

Every request goes through ``sec_http.sec_get_once``: the declared User-Agent and the
shared 8/s throttle, one hop at a time, so each redirect target is re-checked (https,
www.sec.gov or sec.gov, not an EDGAR filing path). A body over MAX_BYTES is refused,
never truncated. PDFs come back PAGES_PER_CALL pages at a time, any text MAX_CHARS
characters at a time; ``find`` searches the whole document. With SEC_EDGAR_RAW_DIR set
(AREX sets it to the run's raw directory), the exact bytes are stored as
``<sha256>.<ext>`` with an ``index.jsonl`` line, as AREX's fetch_raw stores them, and a
PDF's extracted text beside them as ``<sha256>.txt``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..utils import sec_http

MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 30_000
PAGES_PER_CALL = 20
MAX_PAGES = 1000  # a document with more pages is refused, not read in part
MAX_REDIRECTS = 5
MAX_FIND_TERMS = 10
MAX_FIND_MATCHES = 5
FIND_CONTEXT = 150
_HOSTS = ("www.sec.gov", "sec.gov")
_ACCESSION_TOOLS = "get_filing_content, get_filing_sections or get_xbrl_concepts (by accession number)"
_EXTENSIONS = {"application/pdf": "pdf", "text/html": "html", "application/xhtml+xml": "html", "text/plain": "txt"}


class _Refused(Exception):
    """The document cannot be read; the message says why."""


def _check(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise _Refused(f"{url}: only https URLs are read")
    if (parts.hostname or "").lower() not in _HOSTS:
        raise _Refused(f"{url}: this tool reads www.sec.gov or sec.gov only")
    if parts.path.lower().startswith("/archives/edgar/"):
        raise _Refused(f"{url} is an EDGAR filing: read it with {_ACCESSION_TOOLS}")


@dataclass(frozen=True)
class _Document:
    requested: str
    final: str
    media: str
    body: bytes
    pages: list[str] | None  # a PDF's text per page; None for other documents
    text: str


def _fetch(url: str) -> tuple[str, sec_http.Hop]:
    _check(url)
    target = url
    for _ in range(MAX_REDIRECTS + 1):
        hop = sec_http.sec_get_once(target, MAX_BYTES)
        if hop.location is None:
            return target, hop
        nxt = urljoin(target, hop.location)
        try:
            _check(nxt)
        except _Refused as exc:
            raise _Refused(f"{target} answered a redirect that is refused: {exc}") from None
        target = nxt
    raise _Refused(f"{url}: more than {MAX_REDIRECTS} redirects")


def _media(content_type: str, body: bytes) -> str:
    media = content_type.split(";", 1)[0].strip().lower()
    if body.startswith(b"%PDF"):
        return "application/pdf"
    return media or "application/octet-stream"


def _pdf_pages(body: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(body))
        if len(reader.pages) > MAX_PAGES:
            raise _Refused(f"the PDF has {len(reader.pages)} pages, more than {MAX_PAGES}")
        return [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise _Refused(f"the PDF could not be read: {exc}") from None


def _html_text(body: bytes, content_type: str) -> str:
    from bs4 import BeautifulSoup

    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    soup = BeautifulSoup(body.decode(match.group(1) if match else "utf-8", errors="replace"), "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def _read(url: str) -> _Document:
    final, hop = _fetch(url)
    if not 200 <= hop.status < 300 or hop.body is None:
        raise _Refused(f"{final} answered HTTP {hop.status}")
    media = _media(hop.content_type, hop.body)
    if media == "application/pdf":
        pages = _pdf_pages(hop.body)
        return _Document(url, final, media, hop.body, pages, "\n\n".join(pages))
    if media in ("text/html", "application/xhtml+xml"):
        return _Document(url, final, media, hop.body, None, _html_text(hop.body, hop.content_type))
    if media.startswith("text/"):
        return _Document(url, final, media, hop.body, None, hop.body.decode("utf-8", errors="replace"))
    raise _Refused(f"{final} is {media}, not a text, HTML or PDF document")


def _store(doc: _Document, digest: str) -> str | None:
    """The exact bytes as <sha256>.<ext> plus an index line (and a PDF's text); None if off."""
    raw = os.getenv("SEC_EDGAR_RAW_DIR")
    if not raw or not Path(raw).is_absolute():
        return None
    directory = Path(raw)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{digest}.{_EXTENSIONS.get(doc.media, 'bin')}"
    if not (directory / name).exists():
        (directory / name).write_bytes(doc.body)
    if doc.pages is not None and not (directory / f"{digest}.txt").exists():
        (directory / f"{digest}.txt").write_text(doc.text, encoding="utf-8")
    entry = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "requested_url": doc.requested,
        "final_url": doc.final,
        "status": 200,
        "content_type": doc.media,
        "bytes": len(doc.body),
        "sha256": digest,
        "file": name,
        "tool": "get_sec_document",
    }
    with (directory / "index.jsonl").open("a", encoding="utf-8") as index:
        index.write(json.dumps(entry) + "\n")
    return name


def _find(doc: _Document, terms: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Up to MAX_FIND_MATCHES case-insensitive matches per term across the whole document.

    Whitespace is compared as single spaces: a PDF's text keeps its line breaks, so a
    phrase the page prints across two lines still matches.
    """
    raw_chunks = list(enumerate(doc.pages, start=1)) if doc.pages is not None else [(None, doc.text)]
    chunks = [(page, " ".join(text.split())) for page, text in raw_chunks]
    out: dict[str, list[dict[str, Any]]] = {}
    for term in terms:
        pattern = re.compile(r"\s+".join(re.escape(word) for word in term.split()), re.I)
        hits: list[dict[str, Any]] = []
        for page, text in chunks:
            for match in pattern.finditer(text):
                start, end = max(match.start() - FIND_CONTEXT, 0), match.end() + FIND_CONTEXT
                hits.append({"page": page, "context": text[start:end]})
                if len(hits) >= MAX_FIND_MATCHES:
                    break
            if len(hits) >= MAX_FIND_MATCHES:
                break
        out[term] = hits
    return out


class DocumentTools:
    def get_sec_document(self, url: str, find: str | None = None, offset: int = 0, page: int = 1) -> dict[str, Any]:
        """Read one sec.gov document outside EDGAR filings (see the module docstring)."""
        try:
            doc = _read(url.strip())
        except (_Refused, sec_http.TooLarge) as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — a network or parse failure is the caller's error
            return {"success": False, "error": f"{url} could not be read: {type(exc).__name__}: {exc}"}
        digest = hashlib.sha256(doc.body).hexdigest()
        stored = _store(doc, digest)
        result: dict[str, Any] = {
            "success": True,
            "url": doc.final,
            "requested_url": doc.requested,
            "content_type": doc.media,
            "bytes": len(doc.body),
            "sha256": digest,
            "stored": stored,
        }
        if doc.pages is not None:
            first = max(1, int(page))
            last = min(first + PAGES_PER_CALL - 1, len(doc.pages))
            if first > len(doc.pages):
                return {**result, "success": False, "error": f"page {first} is past the last page, {len(doc.pages)}"}
            window = "\n\n".join(f"[page {n}]\n{doc.pages[n - 1]}" for n in range(first, last + 1))
            result.update(
                total_pages=len(doc.pages),
                pages=[first, last],
                next_page=last + 1 if last < len(doc.pages) else None,
            )
        else:
            window = doc.text
        start = max(0, int(offset))
        result.update(
            text=window[start : start + MAX_CHARS],
            offset=start,
            next_offset=start + MAX_CHARS if start + MAX_CHARS < len(window) else None,
            total_chars=len(window),
        )
        if find:
            terms = [t.strip() for t in find.split("|") if t.strip()][:MAX_FIND_TERMS]
            result["find"] = _find(doc, terms)
        return result
