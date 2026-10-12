"""get_sec_document: sec.gov documents outside EDGAR filings (staff reports, rules, speeches).

Offline: sec_http.sec_get_once is replaced, so no test touches the network except the
opt-in live one (SEC_EDGAR_LIVE=1), which reads the SEC staff report on the January 2021
GameStop episode.
"""

import hashlib
import json
import os

import pytest

from sec_edgar_mcp.tools import documents
from sec_edgar_mcp.tools.documents import MAX_BYTES, PAGES_PER_CALL, DocumentTools
from sec_edgar_mcp.utils import sec_http

STAFF_REPORT = "https://www.sec.gov/files/staff-report-equity-options-market-struction-conditions-early-2021.pdf"


def pdf(*pages: str) -> bytes:
    """A minimal valid PDF, one text line per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content.decode()}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def serve(monkeypatch, responses: dict[str, sec_http.Hop]) -> list[str]:
    asked: list[str] = []

    def fake(url: str, max_bytes: int) -> sec_http.Hop:
        asked.append(url)
        return responses[url]

    monkeypatch.setattr(sec_http, "sec_get_once", fake)
    return asked


def ok(body: bytes, content_type: str) -> sec_http.Hop:
    return sec_http.Hop(status=200, content_type=content_type, body=body, location=None)


def call(**args) -> dict:
    return DocumentTools().get_sec_document(**args)


# ── what may be read ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://www.sec.gov/files/x.pdf", "https"),
        ("https://data.sec.gov/files/x.pdf", "www.sec.gov or sec.gov"),
        ("https://www.sec.gov.evil.example/files/x.pdf", "www.sec.gov or sec.gov"),
        ("https://example.com/files/x.pdf", "www.sec.gov or sec.gov"),
    ],
)
def test_only_https_sec_gov_is_read(url, message):
    r = call(url=url)
    assert r["success"] is False and message in r["error"]


def test_an_edgar_filing_path_names_the_accession_tools():
    r = call(url="https://www.sec.gov/Archives/edgar/data/1326380/000132638021000006/x.htm")
    assert r["success"] is False
    assert "get_filing_content" in r["error"] and "get_filing_sections" in r["error"]


def test_a_redirect_off_sec_gov_is_refused(monkeypatch):
    serve(
        monkeypatch,
        {
            "https://www.sec.gov/a": sec_http.Hop(
                status=302, content_type="", body=None, location="https://evil.example/a"
            )
        },
    )
    r = call(url="https://www.sec.gov/a")
    assert r["success"] is False and "redirect" in r["error"] and "evil.example" in r["error"]


def test_a_relative_redirect_on_sec_gov_is_followed_and_reported(monkeypatch):
    asked = serve(
        monkeypatch,
        {
            "https://www.sec.gov/old": sec_http.Hop(status=301, content_type="", body=None, location="/new"),
            "https://www.sec.gov/new": ok(b"<html><body><p>Final text</p></body></html>", "text/html"),
        },
    )
    r = call(url="https://www.sec.gov/old")
    assert r["success"] is True and asked == ["https://www.sec.gov/old", "https://www.sec.gov/new"]
    assert (r["requested_url"], r["url"]) == ("https://www.sec.gov/old", "https://www.sec.gov/new")


def test_too_many_redirects_are_refused(monkeypatch):
    loop = {
        f"https://www.sec.gov/{i}": sec_http.Hop(status=302, content_type="", body=None, location=f"/{i + 1}")
        for i in range(10)
    }
    serve(monkeypatch, loop)
    r = call(url="https://www.sec.gov/0")
    assert r["success"] is False and "redirects" in r["error"]


def test_an_oversized_document_is_an_error_not_a_truncation(monkeypatch):
    def too_big(url: str, max_bytes: int) -> sec_http.Hop:
        assert max_bytes == MAX_BYTES
        raise sec_http.TooLarge(f"{url} is larger than {max_bytes} bytes")

    monkeypatch.setattr(sec_http, "sec_get_once", too_big)
    r = call(url="https://www.sec.gov/files/huge.pdf")
    assert r["success"] is False and "larger than" in r["error"]


def test_a_non_2xx_answer_is_an_error(monkeypatch):
    serve(
        monkeypatch,
        {"https://www.sec.gov/x": sec_http.Hop(status=404, content_type="text/html", body=b"", location=None)},
    )
    r = call(url="https://www.sec.gov/x")
    assert r["success"] is False and "HTTP 404" in r["error"]


# ── what comes back ──────────────────────────────────────────────────────────


def test_a_pdf_is_read_page_by_page_with_find_and_char_paging(monkeypatch, tmp_path):
    pages = [f"Page {i} text about short covering" if i == 3 else f"Page {i} filler" for i in range(1, 46)]
    body = pdf(*pages)
    serve(monkeypatch, {STAFF_REPORT: ok(body, "application/pdf")})
    monkeypatch.setenv("SEC_EDGAR_RAW_DIR", str(tmp_path))
    r = call(url=STAFF_REPORT, find="short covering | not there")
    assert r["success"] is True and r["content_type"] == "application/pdf"
    assert (r["total_pages"], r["pages"], r["next_page"]) == (45, [1, PAGES_PER_CALL], PAGES_PER_CALL + 1)
    assert "Page 3 text about short covering" in r["text"]
    assert r["find"]["short covering"][0]["page"] == 3 and r["find"]["not there"] == []
    # Raw bytes stored by their sha256, with an index line and the extracted text beside them.
    digest = hashlib.sha256(body).hexdigest()
    assert r["sha256"] == digest and (tmp_path / f"{digest}.pdf").read_bytes() == body
    assert "short covering" in (tmp_path / f"{digest}.txt").read_text()
    (entry,) = [json.loads(line) for line in (tmp_path / "index.jsonl").read_text().splitlines()]
    assert entry["sha256"] == digest and entry["final_url"] == STAFF_REPORT
    # The next window.
    nxt = call(url=STAFF_REPORT, page=r["next_page"])
    assert nxt["pages"] == [PAGES_PER_CALL + 1, 2 * PAGES_PER_CALL] and "Page 21 filler" in nxt["text"]


def test_long_text_pages_by_characters(monkeypatch):
    html = (
        "<html><body>" + "".join(f"<p>paragraph {i} {'x' * 200}</p>" for i in range(400)) + "</body></html>"
    ).encode()
    serve(monkeypatch, {"https://www.sec.gov/news/speech/x": ok(html, "text/html; charset=utf-8")})
    first = call(url="https://www.sec.gov/news/speech/x")
    assert (
        first["success"] and len(first["text"]) == documents.MAX_CHARS and first["next_offset"] == documents.MAX_CHARS
    )
    second = call(url="https://www.sec.gov/news/speech/x", offset=first["next_offset"])
    assert second["offset"] == documents.MAX_CHARS and second["text"] != first["text"]


def test_html_comes_back_as_visible_text(monkeypatch):
    html = b"<html><head><script>var x=1;</script><style>p{}</style></head><body><h1>Rule</h1><p>Text</p></body></html>"
    serve(monkeypatch, {"https://www.sec.gov/rules/x": ok(html, "text/html")})
    r = call(url="https://www.sec.gov/rules/x")
    assert "Rule" in r["text"] and "Text" in r["text"] and "var x" not in r["text"]


def test_nothing_is_stored_without_a_raw_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_EDGAR_RAW_DIR", raising=False)
    serve(monkeypatch, {"https://www.sec.gov/rules/x": ok(b"<p>t</p>", "text/html")})
    r = call(url="https://www.sec.gov/rules/x")
    assert r["success"] and r["stored"] is None and r["sha256"]


@pytest.mark.skipif(os.getenv("SEC_EDGAR_LIVE") != "1", reason="live: set SEC_EDGAR_LIVE=1")
def test_live_the_gamestop_staff_report(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", os.getenv("SEC_EDGAR_USER_AGENT", "Joe O (jjoravet@yahoo.com)"))
    monkeypatch.setenv("SEC_EDGAR_RAW_DIR", str(tmp_path))
    r = call(url=STAFF_REPORT, find="short covering | gamma")
    assert r["success"] is True, r
    assert r["total_pages"] > PAGES_PER_CALL and r["next_page"] == PAGES_PER_CALL + 1
    assert r["find"]["short covering"], "find returned no match for 'short covering'"
    later = call(url=STAFF_REPORT, page=r["next_page"])
    assert later["success"] and later["pages"][0] == PAGES_PER_CALL + 1 and later["text"]


def test_find_matches_a_phrase_broken_across_lines(monkeypatch):
    # PDF text keeps its line breaks; the staff report's "a small fraction of overall buy
    # volume" spans two printed lines.
    html = b"<html><body><p>covering was a small fraction of overall\nbuy volume</p></body></html>"
    serve(monkeypatch, {"https://www.sec.gov/files/x": ok(html, "text/plain")})
    r = call(url="https://www.sec.gov/files/x", find="small fraction of overall buy volume")
    assert r["find"]["small fraction of overall buy volume"]
