"""Inline-XBRL fact selection that respects contexts.

A 10-K tags the same concept many times: the company total (a context with no
dimensions) and its breakdowns (contexts whose segment carries explicit
members, e.g. ProductOrServiceAxis=ProductMember). Taking the first tag in the
document returns whichever comes first, often a segment line. ``select_concept``
returns the undimensioned total for the latest, longest period, and lists the
dimensioned facts for that same period separately, each labeled by its members.
It also lists the earlier totals of the same length, the filing's comparative
columns (the prior fiscal year beside the current one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

_CONTEXT = re.compile(r"<xbrli:context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:context>", re.S | re.I)
_START = re.compile(r"<xbrli:startDate>\s*([^<\s]+)\s*</xbrli:startDate>", re.I)
_END = re.compile(r"<xbrli:endDate>\s*([^<\s]+)\s*</xbrli:endDate>", re.I)
_INSTANT = re.compile(r"<xbrli:instant>\s*([^<\s]+)\s*</xbrli:instant>", re.I)
_EXPLICIT = re.compile(r"<xbrldi:explicitMember\b[^>]*\bdimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.I)
_TYPED = re.compile(r"<xbrldi:typedMember\b[^>]*\bdimension=\"([^\"]+)\"[^>]*>(.*?)</xbrldi:typedMember>", re.S | re.I)
_NON_FRACTION = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_ZERO_TEXT = {"", "-", "—", "–", "--"}
# 52/53-week fiscal years differ by 7 days; a quarter is never within this of a year.
_SAME_LENGTH_DAYS = 7


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"\b{name}=\"([^\"]*)\"", attrs, re.I)
    return m.group(1) if m else None


@dataclass(frozen=True)
class Context:
    id: str
    start: str | None
    end: str | None
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def days(self) -> int:
        if not self.start or not self.end:
            return 0
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days


@dataclass(frozen=True)
class Fact:
    concept: str
    value: float
    raw_value: str
    scale: int
    context: Context

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "raw_value": self.raw_value,
            "scale": self.scale,
            "period": self.context.end,
            "period_start": self.context.start,
            "context_ref": self.context.id,
            "dimensions": dict(self.context.dimensions),
        }


def parse_contexts(content: str) -> dict[str, Context]:
    contexts = {}
    for cid, body in _CONTEXT.findall(content):
        start = _START.search(body)
        end = _END.search(body) or _INSTANT.search(body)
        dims = {d: m for d, m in _EXPLICIT.findall(body)}
        dims.update({d: _TAGS.sub("", v).strip() for d, v in _TYPED.findall(body)})
        contexts[cid] = Context(cid, start.group(1) if start else None, end.group(1) if end else None, dims)
    return contexts


def _number(attrs: str, inner: str) -> tuple[float, str, int] | None:
    raw = _TAGS.sub("", inner).strip()
    scale = int(_attr(attrs, "scale") or 0)
    fmt = (_attr(attrs, "format") or "").lower()
    if raw in _ZERO_TEXT or "zerodash" in fmt or "fixed-zero" in fmt:
        number = 0.0
    else:
        digits = re.sub(r"[^0-9.,]", "", raw)
        if "comma-decimal" in fmt or "numcommadecimal" in fmt:
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
        try:
            number = float(digits)
        except ValueError:
            return None
    if _attr(attrs, "sign") == "-":
        number = -number
    return number * 10**scale, raw, scale


def concept_facts(content: str, concept: str, contexts: dict[str, Context] | None = None) -> list[Fact]:
    """Every numeric fact tagged exactly *concept* (any namespace prefix), deduplicated."""
    contexts = contexts if contexts is not None else parse_contexts(content)
    wanted = concept.split(":")[-1].lower()
    seen, facts = set(), []
    for attrs, inner in _NON_FRACTION.findall(content):
        name = (_attr(attrs, "name") or "").split(":")[-1].lower()
        ctx = contexts.get(_attr(attrs, "contextRef") or "")
        if name != wanted or ctx is None:
            continue
        parsed = _number(attrs, inner)
        if parsed is None:
            continue
        value, raw, scale = parsed
        if (ctx.id, value) in seen:
            continue
        seen.add((ctx.id, value))
        facts.append(Fact(concept, value, raw, scale, ctx))
    return facts


def _prior_periods(totals: list[Fact], best: Fact) -> list[dict]:
    """Earlier undimensioned totals as long as *best*, one per period, latest first."""
    by_period: dict[tuple, Fact] = {}
    for f in totals:
        if not f.context.end or not best.context.end or f.context.end >= best.context.end:
            continue
        if abs(f.context.days - best.context.days) > _SAME_LENGTH_DAYS:
            continue
        by_period.setdefault((f.context.start, f.context.end), f)
    return [f.as_dict() for f in sorted(by_period.values(), key=lambda f: f.context.end or "", reverse=True)]


def select_concept(content: str, concept: str) -> dict | None:
    """The undimensioned total for the latest, longest period, plus that period's members.

    None when the concept is not tagged at all. When only dimensioned facts exist,
    ``value`` is None and ``total_found`` is False, so a member is never passed off
    as the total. ``prior_periods`` holds the earlier totals of the same length
    (the comparative columns), latest first.
    """
    facts = concept_facts(content, concept)
    if not facts:
        return None
    totals = [f for f in facts if not f.context.dimensions]
    pick_from = totals or facts
    best = max(pick_from, key=lambda f: (f.context.end or "", f.context.days))
    period = (best.context.start, best.context.end)
    members = [
        {**f.as_dict(), "member": ", ".join(f"{d}={m}" for d, m in sorted(f.context.dimensions.items()))}
        for f in facts
        if f.context.dimensions and (f.context.start, f.context.end) == period
    ]
    result = {
        **(best.as_dict() if totals else {k: None for k in best.as_dict()}),
        "total_found": bool(totals),
        "members": members,
        "prior_periods": _prior_periods(totals, best) if totals else [],
        "source": "xbrl_direct_extraction",
    }
    if not totals:
        result.update(period=best.context.end, period_start=best.context.start)
    return result
