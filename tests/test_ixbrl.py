"""Inline-XBRL selection: undimensioned totals win; dimensioned members are labeled, never the total."""

import pytest

from sec_edgar_mcp.tools.financial import FinancialTools
from sec_edgar_mcp.utils.ixbrl import concept_facts, select_concept
from sec_edgar_mcp.utils import sec_http

CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"


def ctx(cid, start, end, member=None):
    seg = (
        '<xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">'
        f"{member}</xbrldi:explicitMember></xbrli:segment>"
        if member
        else ""
    )
    return (
        f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">0000320193'
        f"</xbrli:identifier>{seg}</xbrli:entity><xbrli:period><xbrli:startDate>{start}"
        f"</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>"
    )


def fact(cid, text, name=f"us-gaap:{CONCEPT}", extra=""):
    return (
        f'<ix:nonFraction unitRef="usd" contextRef="{cid}" decimals="-6" name="{name}" '
        f'format="ixt:num-dot-decimal" scale="6" {extra}>{text}</ix:nonFraction>'
    )


# Shaped like Apple's FY2025 10-K: the Products line is tagged before the total.
APPLE_LIKE = "".join(
    [
        ctx("c-1", "2024-09-29", "2025-09-27"),
        ctx("c-2", "2023-10-01", "2024-09-28"),
        ctx("c-12", "2024-09-29", "2025-09-27", "us-gaap:ProductMember"),
        ctx("c-13", "2024-09-29", "2025-09-27", "us-gaap:ServiceMember"),
        ctx("c-30", "2025-06-29", "2025-09-27"),
        fact("c-12", "307,003"),
        fact("c-13", "109,158"),
        fact("c-1", "416,161"),
        fact("c-2", "391,035"),
        fact("c-30", "102,466"),
        fact("c-1", "416,161"),  # the same fact tagged again in another table
        fact("c-1", "1", name=f"us-gaap:{CONCEPT}Other"),  # not this concept
    ]
)


def test_total_is_the_undimensioned_fact_for_the_latest_longest_period():
    r = select_concept(APPLE_LIKE, CONCEPT)
    assert r["total_found"] is True
    assert r["value"] == 416_161_000_000
    assert (r["context_ref"], r["period_start"], r["period"]) == ("c-1", "2024-09-29", "2025-09-27")
    assert r["dimensions"] == {}


def test_members_of_that_period_are_labeled_and_kept_apart():
    members = {m["member"]: m["value"] for m in select_concept(APPLE_LIKE, CONCEPT)["members"]}
    assert members == {
        "srt:ProductOrServiceAxis=us-gaap:ProductMember": 307_003_000_000,
        "srt:ProductOrServiceAxis=us-gaap:ServiceMember": 109_158_000_000,
    }


def test_exact_concept_names_only_and_duplicates_collapse():
    facts = concept_facts(APPLE_LIKE, CONCEPT)
    assert len(facts) == 5
    assert all(f.concept == CONCEPT for f in facts)


def test_only_dimensioned_facts_never_pose_as_the_total():
    content = ctx("c-12", "2024-09-29", "2025-09-27", "us-gaap:ProductMember") + fact("c-12", "307,003")
    r = select_concept(content, CONCEPT)
    assert r["total_found"] is False and r["value"] is None
    assert r["members"][0]["value"] == 307_003_000_000


def test_sign_and_zero_dash():
    content = ctx("c-1", "2024-09-29", "2025-09-27") + fact("c-1", "1,234", extra='sign="-"')
    assert select_concept(content, CONCEPT)["value"] == -1_234_000_000
    dash = ctx("c-1", "2024-09-29", "2025-09-27") + fact("c-1", "—").replace("ixt:num-dot-decimal", "ixt:fixed-zero")
    assert select_concept(dash, CONCEPT)["value"] == 0


def test_prior_periods_are_the_comparative_totals_of_the_same_length():
    r = select_concept(APPLE_LIKE, CONCEPT)
    # the prior fiscal year is listed; the quarter (c-30) and the members are not
    assert [(p["context_ref"], p["period_start"], p["period"], p["value"]) for p in r["prior_periods"]] == [
        ("c-2", "2023-10-01", "2024-09-28", 391_035_000_000)
    ]


def test_prior_periods_keep_53_week_years_and_order_latest_first():
    content = "".join(
        [
            ctx("c-1", "2024-09-29", "2025-09-27"),
            ctx("c-2", "2023-09-24", "2024-09-28"),  # a 53-week year
            ctx("c-3", "2022-09-25", "2023-09-30"),
            fact("c-3", "383,285"),
            fact("c-1", "416,161"),
            fact("c-2", "391,035"),
        ]
    )
    priors = select_concept(content, CONCEPT)["prior_periods"]
    assert [p["context_ref"] for p in priors] == ["c-2", "c-3"]


def test_prior_periods_for_instants_are_earlier_instants():
    def inst(cid, day):
        return (
            f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>'
            f"</xbrli:entity><xbrli:period><xbrli:instant>{day}</xbrli:instant></xbrli:period></xbrli:context>"
        )

    content = (
        inst("i-1", "2025-09-27")
        + inst("i-0", "2024-09-28")
        + fact("i-1", "10", name="us-gaap:Assets")
        + fact("i-0", "9", name="us-gaap:Assets")
    )
    r = select_concept(content, "Assets")
    assert r["period"] == "2025-09-27"
    assert [(p["period"], p["value"]) for p in r["prior_periods"]] == [("2024-09-28", 9_000_000)]


def test_only_dimensioned_facts_have_no_prior_periods():
    content = ctx("c-12", "2024-09-29", "2025-09-27", "us-gaap:ProductMember") + fact("c-12", "307,003")
    assert select_concept(content, CONCEPT)["prior_periods"] == []


def test_untagged_concept_is_none():
    assert select_concept(APPLE_LIKE, "NetIncomeLoss") is None


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Joe O (jjoravet@yahoo.com)")
    return FinancialTools()


def test_xbrl_extraction_through_the_tool_returns_the_total(tools):
    r = tools._extract_xbrl_concept_value(APPLE_LIKE, CONCEPT)
    assert r["value"] == 416_161_000_000 and len(r["members"]) == 2
    assert [p["value"] for p in r["prior_periods"]] == [391_035_000_000]


class _Company:
    cik = 320193
    name = "Apple Inc."


def test_key_metrics_reads_companyfacts(tools, monkeypatch):
    from sec_edgar_mcp.tools import financial

    data = {
        "us-gaap": {
            CONCEPT: {
                "units": {
                    "USD": [
                        {"val": 391035000000, "end": "2024-09-28", "fy": 2024, "fp": "FY", "form": "10-K"},
                        {"val": 416161000000, "end": "2025-09-27", "fy": 2025, "fp": "FY", "form": "10-K"},
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(tools.client, "get_company", lambda identifier: _Company())
    monkeypatch.setattr(financial, "company_facts", lambda cik: sec_http.RawFacts(data))
    r = tools.get_key_metrics("AAPL", [CONCEPT])
    assert r["success"] is True
    assert r["metrics"][CONCEPT]["value"] == 416_161_000_000


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    @property
    def iloc(self):
        return self.rows


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def by_concept(self, name):
        return self

    def to_dataframe(self):
        return _Frame(self.rows)


class _XbrlIgnoringTheFilter:
    """edgartools' query("concept=X") is not a filter: it returns every fact, cover page first."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, _):
        return _Query(self.rows)


def test_fallback_never_reports_another_concept_as_the_requested_one(tools):
    cover = {"concept": "dei:DocumentFiscalPeriodFocus", "value": "FY", "period_end": "2026-06-30"}
    assert tools._get_xbrl_concept_fallback(_XbrlIgnoringTheFilter([cover]), "Revenues") is None


def test_fallback_returns_the_exact_concept_when_present(tools):
    cover = {"concept": "dei:DocumentFiscalPeriodFocus", "value": "FY", "period_end": "2026-06-30"}
    rev = {"concept": "us-gaap:Revenues", "value": 5.0, "period_end": "2026-06-30"}
    r = tools._get_xbrl_concept_fallback(_XbrlIgnoringTheFilter([cover, rev]), "Revenues")
    assert (r["concept"], r["value"]) == ("us-gaap:Revenues", 5.0)
