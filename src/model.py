"""
Canonical product model + rules engine.

The whole design rests on one idea: build ONE truthful, fully-enriched record
per sellable variant, then let each channel project it into its own dialect.
Channels never touch Shopify data directly, so a Bing quirk can never corrupt
the Google feed, and adding a channel is a ~100-line projection, not a rewrite.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def plain_text(raw: str | None) -> str:
    """HTML -> clean plain text. Feeds must never carry markup."""
    if not raw:
        return ""
    s = raw.replace("</p>", "</p> ").replace("<br>", " ").replace("<br/>", " ")
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace(" ", " ")
    return _WS.sub(" ", s).strip()


def rich_text_to_plain(raw: str | None) -> str:
    """Shopify rich_text_field metafields are a nested JSON AST, not HTML."""
    if not raw:
        return ""
    try:
        node = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return plain_text(raw)

    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for c in n:
                walk(c)
            return
        if not isinstance(n, dict):
            return
        if n.get("type") == "text":
            out.append(n.get("value", ""))
        for c in n.get("children", []) or []:
            walk(c)
        if n.get("type") in ("paragraph", "list-item", "heading"):
            out.append(" ")

    walk(node)
    return _WS.sub(" ", "".join(out)).strip()


def truncate_words(s: str, limit: int) -> str:
    """Trim to a character cap on a word boundary. Never cut mid-word."""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:-–—|")


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "none"


# --------------------------------------------------------------------------- #
# canonical record
# --------------------------------------------------------------------------- #


@dataclass
class Offer:
    """One sellable variant, fully enriched, channel-agnostic."""

    # identity
    offer_id: str = ""
    item_group_id: str = ""
    sku: str = ""
    gtin: str = ""
    mpn: str = ""
    identifier_exists: bool = True

    # content
    title: str = ""
    short_title: str = ""
    description: str = ""
    link: str = ""
    images: list[str] = field(default_factory=list)

    # classification
    brand: str = ""
    google_category_id: int = 0
    google_category_path: str = ""
    product_type_path: str = ""
    condition: str = "new"

    # commerce
    price: float = 0.0
    sale_price: float | None = None
    currency: str = "USD"
    availability: str = "in_stock"       # canonical: in_stock|out_of_stock|preorder|backorder
    stock_posture: str = "healthy"
    quantity: int = 0
    min_handling_days: int = 1
    max_handling_days: int = 3
    min_transit_days: int = 1
    max_transit_days: int = 4
    weight_lb: float = 0.0
    weight_estimated: bool = False

    # segmentation
    custom_labels: dict[str, str] = field(default_factory=dict)

    # enrichment — the part generic feed tools throw away
    highlights: list[str] = field(default_factory=list)
    details: list[tuple[str, str, str]] = field(default_factory=list)  # section, name, value
    qa: list[tuple[str, str]] = field(default_factory=list)
    variant_options: list[tuple[str, str]] = field(default_factory=list)
    star_rating: float | None = None
    review_count: int | None = None
    primary_pest: str = ""
    color: str = ""
    size: str = ""
    age_group: str = ""
    gender: str = ""
    target_pests: list[str] = field(default_factory=list)

    # provenance — every generated field can be traced back to its rule
    trace: dict[str, str] = field(default_factory=dict)


@dataclass
class Dropped:
    product_id: str
    variant_id: str
    title: str
    rule_id: str
    reason: str


# --------------------------------------------------------------------------- #
# rules engine primitives
# --------------------------------------------------------------------------- #


def match_condition(cond: dict, ctx: dict) -> bool:
    """Evaluate a single {field, op, value} predicate against a context dict."""
    f = cond.get("field")
    op = cond.get("op")
    v = cond.get("value")
    actual = ctx.get(f)

    if op == "is_empty":
        return not actual
    if op == "is_not_empty":
        return bool(actual)
    if op == "eq":
        return actual == v
    if op == "ne":
        return actual != v
    if op == "in":
        return actual in (v or [])
    if op == "not_in":
        return actual not in (v or [])
    if op == "lte":
        return actual is not None and float(actual) <= float(v)
    if op == "gte":
        return actual is not None and float(actual) >= float(v)
    if op == "contains_any":
        pool = actual or []
        return any(x in pool for x in (v or []))
    if op == "contains":
        return bool(actual) and str(v).lower() in str(actual).lower()
    raise ValueError(f"unknown operator: {op!r}")


def match_rule(rule: dict, ctx: dict) -> bool:
    """A rule is `when` (single), `any` (OR list), or `all` (AND list)."""
    if "when" in rule:
        return match_condition(rule["when"], ctx)
    if "any" in rule:
        return any(match_condition(c, ctx) for c in rule["any"])
    if "all" in rule:
        return all(match_condition(c, ctx) for c in rule["all"])
    return False


_VAR = re.compile(r"\{([a-z_]+)\}")


def render_template(tmpl: str, vars_: dict[str, str]) -> str | None:
    """Render `{name}` placeholders. Returns None if any placeholder is empty."""
    missing = [m for m in _VAR.findall(tmpl) if not vars_.get(m)]
    if missing:
        return None
    out = _VAR.sub(lambda m: str(vars_.get(m.group(1), "")), tmpl)
    return _WS.sub(" ", out).strip()
