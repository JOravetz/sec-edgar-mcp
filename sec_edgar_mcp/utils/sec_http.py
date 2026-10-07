"""SEC fair-access HTTP: a declared User-Agent, one process-wide rate limit, working URLs.

SEC policy (https://www.sec.gov/os/accessing-edgar-data): every request declares
who is asking ("Name email@domain"), and a client stays under 10 requests per
second. All direct fetches here go through edgartools' ``get_with_retry``, whose
throttler is process-global (8/s), so this server and edgartools share one budget.

sec.gov answers 403 for the short filing-index URL
``/Archives/edgar/data/<cik>/<accession>-index.html`` that edgartools 4.4.5
builds; the same page under the accession folder is served. ``install_url_fixes``
points edgartools at the served form.
"""

from __future__ import annotations

import os
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net")


def declared_user_agent(value: str | None = None) -> str:
    """SEC_EDGAR_USER_AGENT, checked to name a real contact ("Name email@domain")."""
    ua = (value if value is not None else os.getenv("SEC_EDGAR_USER_AGENT", "")).strip()
    match = _EMAIL.search(ua)
    if not match:
        raise ValueError(
            "SEC_EDGAR_USER_AGENT must declare a contact, e.g. 'Jane Doe jane@company.com' (SEC fair-access policy)."
        )
    if match.group(0).lower().endswith(_PLACEHOLDER_DOMAINS):
        raise ValueError(f"SEC_EDGAR_USER_AGENT uses a placeholder address: {match.group(0)}")
    return ua


def filing_index_url(cik: int | str, accession: str) -> str:
    """The filing index page sec.gov serves (inside the accession folder)."""
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.html"


def sec_get_text(url: str) -> str:
    """GET *url* through edgartools' throttled, identity-bearing client; raise on non-2xx."""
    from edgar.httprequests import get_with_retry

    response = get_with_retry(url)
    response.raise_for_status()
    return response.text


def install_url_fixes() -> None:
    """Make edgartools use the served filing-index URL (idempotent)."""
    from edgar._filings import Filing

    def homepage_url(self: Filing) -> str:
        return filing_index_url(self.cik, self.accession_no)

    Filing.homepage_url = property(homepage_url)  # type: ignore[assignment,method-assign]


def companyfacts_url(cik: int | str) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"


class RawFacts:
    """SEC companyfacts JSON by taxonomy (``data["us-gaap"][concept]["units"]``).

    companyfacts holds only undimensioned facts, so every value is a company total.
    edgartools 4.4.5's EntityFacts has no ``.data``; this is what the tools expected.
    """

    def __init__(self, data: dict) -> None:
        self.data = data

    def __bool__(self) -> bool:
        return bool(self.data)


def company_facts(cik: int | str) -> RawFacts:
    import json

    return RawFacts(json.loads(sec_get_text(companyfacts_url(cik))).get("facts", {}))
