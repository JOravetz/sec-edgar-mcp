"""compare_periods: every value carries its own period, filing and kind; growth uses annual periods only."""

import pytest

from sec_edgar_mcp.tools import financial
from sec_edgar_mcp.tools.financial import FinancialTools
from sec_edgar_mcp.utils import sec_http

CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"


def fact(val, start, end, fy, fp, form, accn, filed):
    return {"val": val, "start": start, "end": end, "fy": fy, "fp": fp, "form": form, "accn": accn, "filed": filed}


A22 = ("2021-09-26", "2022-09-24")
A23 = ("2022-09-25", "2023-09-30")
A24 = ("2023-10-01", "2024-09-28")
A25 = ("2024-09-29", "2025-09-27")
K24 = ("0000320193-24-000123", "2024-11-01")
K25 = ("0000320193-25-000079", "2025-10-31")

# Shaped like Apple's companyfacts: each 10-K repeats two prior years, all tagged with
# the filing's fy, so fy=2024 alone carries three different annual revenues.
APPLE = [
    fact(394328e6, *A22, 2024, "FY", "10-K", *K24),
    fact(383285e6, *A23, 2024, "FY", "10-K", *K24),
    fact(391035e6, *A24, 2024, "FY", "10-K", *K24),
    fact(383285e6, *A23, 2025, "FY", "10-K", *K25),
    fact(391035e6, *A24, 2025, "FY", "10-K", *K25),
    fact(416161e6, *A25, 2025, "FY", "10-K", *K25),
    fact(102466e6, "2025-06-29", "2025-09-27", 2025, "FY", "10-K", *K25),  # Q4, tagged in the 10-K
    fact(94036e6, "2025-03-30", "2025-06-28", 2025, "Q3", "10-Q", "0000320193-25-000073", "2025-08-01"),
]


class _Company:
    cik = 320193
    name = "Apple Inc."


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Joe O (jjoravet@yahoo.com)")
    tools = FinancialTools()

    def call(facts, start_year=2024, end_year=2025):
        data = {"us-gaap": {CONCEPT: {"units": {"USD": facts}}}}
        monkeypatch.setattr(tools.client, "get_company", lambda identifier: _Company())
        monkeypatch.setattr(financial, "company_facts", lambda cik: sec_http.RawFacts(data))
        return tools.compare_periods("AAPL", CONCEPT, start_year, end_year)

    return call


def test_each_period_appears_once_with_its_own_dates_and_latest_filing(run):
    r = run(APPLE)
    annual = [p for p in r["period_data"] if p["period_type"] == "annual"]
    assert [(p["period_start"], p["period_end"], p["value"]) for p in annual] == [
        (*A24, 391035e6),
        (*A25, 416161e6),
    ]
    # The FY2024 value is reported by both 10-Ks; the later filing is cited.
    assert (annual[0]["accession_number"], annual[0]["filed"], annual[0]["form"]) == (
        "0000320193-25-000079",
        "2025-10-31",
        "10-K",
    )
    assert annual[0]["fiscal_year_of_filing"] == 2025


def test_years_select_by_period_end_not_by_the_filings_fiscal_year(run):
    ends = {p["period_end"] for p in run(APPLE)["period_data"]}
    assert "2023-09-30" not in ends and "2022-09-24" not in ends
    assert {p["period_end"] for p in run(APPLE, 2022, 2023)["period_data"] if p["period_type"] == "annual"} == {
        "2022-09-24",
        "2023-09-30",
    }


def test_quarters_are_labeled_and_kept_out_of_the_growth(run):
    r = run(APPLE)
    kinds = {(p["period_end"], p["period_type"]) for p in r["period_data"]}
    assert ("2025-09-27", "quarter") in kinds and ("2025-06-28", "quarter") in kinds
    a = r["analysis"]
    assert a["basis"] == "annual periods"
    assert (a["start_value"], a["end_value"]) == (391035e6, 416161e6)
    assert (a["start_period_end"], a["end_period_end"]) == ("2024-09-28", "2025-09-27")
    assert a["total_growth_percent"] == pytest.approx(6.43, abs=0.005)


def test_a_restated_value_is_shown_with_the_value_it_replaced(run):
    restated = APPLE + [fact(391000e6, *A24, 2024, "FY", "10-K/A", "0000320193-24-000200", "2024-12-15")]
    (fy24,) = [p for p in run(restated)["period_data"] if p["period_end"] == "2024-09-28"]
    assert fy24["value"] == 391035e6
    assert fy24["earlier_values"] == [
        {"value": 391000e6, "accession_number": "0000320193-24-000200", "filed": "2024-12-15", "form": "10-K/A"}
    ]


def test_fewer_than_two_annual_periods_give_no_growth(run):
    a = run(APPLE[-2:])["analysis"]
    assert a["total_growth_percent"] is None and a["cagr_percent"] is None
