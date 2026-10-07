"""SEC fair-access HTTP: declared User-Agent, shared throttle, the filing-index URL sec.gov serves."""

import pytest

from sec_edgar_mcp import config
from sec_edgar_mcp.utils import sec_http


@pytest.mark.parametrize("ua", ["Joe O (jjoravet@yahoo.com)", "Acme Research ops@acme.io"])
def test_declared_user_agent_accepts_a_named_contact(ua):
    assert sec_http.declared_user_agent(ua) == ua


@pytest.mark.parametrize("ua", ["", "SEC EDGAR MCP/1.0", "python-requests/2.32"])
def test_declared_user_agent_rejects_no_contact(ua):
    with pytest.raises(ValueError, match="must declare a contact"):
        sec_http.declared_user_agent(ua)


def test_declared_user_agent_rejects_placeholder_addresses():
    with pytest.raises(ValueError, match="placeholder"):
        sec_http.declared_user_agent("claude-code-mcp-client developer@example.com")


def test_config_requires_the_env_var(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    with pytest.raises(ValueError):
        config.initialize_config()
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Joe O (jjoravet@yahoo.com)")
    assert config.initialize_config() == "Joe O (jjoravet@yahoo.com)"


def test_filing_index_url_is_inside_the_accession_folder():
    assert sec_http.filing_index_url("0000320193", "0000320193-25-000079") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.html"
    )


def test_edgartools_homepage_url_uses_the_served_form():
    from edgar._filings import Filing

    filing = Filing(
        cik=320193,
        company="Apple Inc.",
        form="10-K",
        filing_date="2025-10-31",
        accession_no="0000320193-25-000079",
    )
    sec_http.install_url_fixes()
    sec_http.install_url_fixes()  # idempotent
    assert filing.homepage_url == sec_http.filing_index_url(320193, "0000320193-25-000079")


class _Response:
    def __init__(self, status: int, text: str = "body"):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_sec_get_text_goes_through_the_shared_throttled_client(monkeypatch):
    import edgar.httprequests as hr

    calls = []
    monkeypatch.setattr(hr, "get_with_retry", lambda url: calls.append(url) or _Response(200, "ok"))
    assert sec_http.sec_get_text("https://www.sec.gov/x") == "ok"
    assert calls == ["https://www.sec.gov/x"]


def test_sec_get_text_raises_on_403(monkeypatch):
    import edgar.httprequests as hr

    monkeypatch.setattr(hr, "get_with_retry", lambda url: _Response(403))
    with pytest.raises(RuntimeError, match="403"):
        sec_http.sec_get_text("https://www.sec.gov/x")


def test_edgartools_throttle_is_within_sec_limit():
    import edgar.httprequests as hr

    assert hr.max_requests_per_second <= 10
