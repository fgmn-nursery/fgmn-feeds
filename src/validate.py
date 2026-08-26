"""
Pre-flight validation. This is the piece hosted feed tools do worst: they tell
you a feed was *generated*, not whether it will be *accepted*. Every check here
maps to a real disapproval reason, and the run fails loudly rather than
publishing a feed that quietly loses coverage.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from xml.etree import ElementTree

from model import Offer

# Terms that trip Google's live-animal transportation-safety classifier or read
# as a registered chemical pesticide. Neither is what this merchant sells.
# Only affirmative usage is a risk. "reduces pesticide use", "pesticide-free"
# and "incompatible with insecticides" are selling points or safety guidance —
# flagging them buries the real signal. Negation is checked in a word window
# rather than with lookbehinds, which cannot express variable-length context.
RISK_TERMS = re.compile(
    r"\b(live animals?|livestock|pesticides?|insecticides?|poison|venom)\b", re.I
)
_NEGATORS = {
    "no", "non", "not", "without", "free", "reduce", "reduces", "reducing",
    "avoid", "avoids", "instead", "unlike", "never", "chemical", "chemicals",
    "synthetic", "systemic", "systemics", "broad-spectrum", "residue", "residues",
    "most", "some", "compatibility", "harsh", "conventional", "traditional",
}


def _is_affirmative(text: str, match: re.Match) -> bool:
    """True only when the risk term is used to describe THIS product."""
    before = re.findall(r"[a-zA-Z-]+", text[max(0, match.start() - 60):match.start()])[-4:]
    # Strip a leading hyphen so "pesticide-free" reads as the word "free".
    after = [w.lstrip("-") for w in
             re.findall(r,[a-zA-Z-]+", text[match.end():match.end() + 30])[:2]]
    if any(w.lower() in _NEGATORS for w in before):
        return False
    if after and after[0].lower() in {"free", "use", "reduction", "compatibility"}:
        return False
    return True


@dataclass
class Finding:
    severity: str      # error | warn | info
    code: str
    offer_id: str
    message: str


def validate_offers(offers: list[Offer], cfg: dict) -> list[Finding]:
    f: list[Finding] = []
    seen: Counter[str] = Counter()

    for o in offers:
        seen[o.offer_id] += 1

        if not o.offer_id:
            f.append(Finding("error", "missing_id", "", "offer has no id"))
        if len(o.offer_id) > cfg["id_max"]:
            f.append(Finding("error", "id_too_long", o.offer_id,
                             f"id is {len(o.offer_id)} chars, max {cfg['id_max']}"))
        if not o.title:
            f.append(Finding("error", "missing_title", o.offer_id, "no title"))
        elif len(o.title) > cfg["title_max"]:
            f.append(Finding("error", "title_too_long", o.offer_id,
                             f"title is {len(o.title)} chars, max {cfg['title_max']}"))
        elif len(o.title) < 25:
            f.append(Finding("warn", "title_thin", o.offer_id,
                             f"title only {len(o.title)} chars — weak query coverage"))
        if not o.description:
            f.append(Finding("error", "missing_description", o.offer_id, "no description"))
        elif len(o.description) < 150:
            f.append(Finding("warn", "description_thin", o.offer_id,
                             f"description only {len(o.description)} chars"))
        if not o.link.startswith("http"):
            f.append(Finding("error", "bad_link", o.offer_id, f"link {o.link!r}"))
        if not o.images:
            f.append(Finding("error", "missing_image", o.offer_id, "no image_link"))
        elif len(o.images) == 1:
            f.append(Finding("info", "single_image", o.offer_id,
                             "only one image — additional images lift CTR"))
        if o.price <= 0:
            f.append(Finding("error", "bad_price", o.offer_id, f"price {o.price}"))
        if o.sale_price is not None and o.sale_price >= o.price:
            f.append(Finding("error", "bad_sale_price", o.offer_id,
                             "sale_price must be below price"))
        if not o.brand:
            f.append(Finding("error", "missing_brand", o.offer_id, "no brand"))
        if not o.google_category_id:
            f.append(Finding("warn", "missing_category", o.offer_id,
                             "no google_product_category"))
        if not o.gtin and not o.mpn and o.identifier_exists:
            f.append(Finding("error", "identifier_conflict", o.offer_id,
                             "no gtin/mpn but identifier_exists is not 'no'"))
        if o.weight_estimated:
            f.append(Finding("info", "weight_estimated", o.offer_id,
                             f"weight {o.weight_lb}lb is a fallback — Shopify has none"))
        if o.stock_posture == "oversold":
            f.append(Finding("warn", "oversold", o.offer_id,
                             f"on-hand is {o.quantity} but the offer is live — "
                             "advertising committed inventory"))
        if "Apparel" in o.google_category_path and any(
            w in o.title.lower() for w in ("mug", "tumbler", "tote", "sticker",
                                           "bottle", "hat", "cap", "poster")
        ):
            f.append(Finding("warn", "category_suspect", o.offer_id,
                             "matched the apparel tag rule but is not apparel — "
                             "needs its own taxonomy node in rules.yaml"))
        elif "Apparel" in o.google_category_path:
            for attr in ("color", "size", "age_group", "gender"):
                if not getattr(o, attr, ""):
                    f.append(Finding("warn", f"apparel_missing_{attr}", o.offer_id,
                                     f"{attr} required for Apparel in the US — "
                                     f"add a row to config/overrides.csv or an option in Shopify"))
        if 0 < len(o.highlights) < 2:
            f.append(Finding("warn", "highlight_count", o.offer_id,
                             "Google requires 2-100 product_highlight values"))
        blob = f"{o.title} {o.description}"
        for hit in RISK_TERMS.finditer(blob):
            if _is_affirmative(blob, hit):
                sev = "error" if hit.start() < len(o.title) else "warn"
                f.append(Finding(sev, "policy_risk_term", o.offer_id,
                                 f"affirmative use of {hit.group(0)!r} — live-animal / "
                                 "pesticide classifier risk"))
                break

    for oid, n in seen.items():
        if n > 1:
            f.append(Finding("error", "duplicate_id", oid, f"appears {n} times"))
    return f


def validate_xml(text: str) -> list[Finding]:
    try:
        ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        return [Finding("error", "malformed_xml", "", str(e))]
    return []


def summarize(findings: list[Finding]) -> dict:
    by = Counter(f.severity for f in findings)
    codes = Counter(f.code for f in findings)
    return {"errors": by["error"], "warnings": by["warn"], "info": by["info"],
            "by_code": dict(codes.most_common())}
