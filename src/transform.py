"""
Shopify catalog -> canonical Offers, driven entirely by config/rules.yaml.

Nothing in here is FGMN-specific. Every business decision lives in the YAML,
which is the point: the rules are reviewable in a pull request instead of
buried in a SaaS admin panel that nobody can diff.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from typing import Any

from model import (
    Dropped,
    Offer,
    plain_text,
    render_template,
    rich_text_to_plain,
    slug,
    truncate_words,
)

# --------------------------------------------------------------------------- #
# Shopify shredding
# --------------------------------------------------------------------------- #

# Variant option axes that are NOT product attributes — they are checkout
# choices. Feeding them produces near-duplicate offers that Google dedupes
# against you.
NON_ATTRIBUTE_AXES = {
    "Shipping Options",
    "Make It A Gift Set?",
    "Purchase Options",
    "Choose your exact plant",
}

# Marketing size labels ("X-Small — Up to 15 Plants (5 Million Nematodes)")
# carry the real differentiator inside the parenthetical or after the dash.
_SIZE_DETAIL = re.compile(r"—\s*(.+)$")
_PARENS = re.compile(r"\(([^)]+)\)")

RARITY_LABELS = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Very rare", 5: "Collector"}


def metafields(product: dict) -> dict[str, dict]:
    return {m["key"]: m for m in product.get("metafields", {}).get("nodes", [])}


def dimension(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        d = json.loads(raw)
        unit = {"INCHES": '"', "CENTIMETERS": " cm", "FEET": " ft"}.get(d.get("unit"), "")
        val = d.get("value")
        if val is None:
            return ""
        n = int(val) if float(val).is_integer() else val
        return f"{n}{unit}"
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""


def variant_qualifier(variant: dict) -> str:
    """The human-meaningful part of a variant title: size, count, colour."""
    parts = []
    for so in variant.get("selectedOptions", []):
        if so["name"] in NON_ATTRIBUTE_AXES or so["value"] in ("Default Title", "Title"):
            continue
        parts.append(so["value"])
    if not parts:
        # Every axis was a checkout choice, not a product attribute. The variant
        # title would just echo it ("Heat Pack"), so there is no qualifier.
        raw = "" if variant.get("selectedOptions") else variant.get("title", "")
    else:
        raw = " / ".join(parts)
    if raw in ("Default Title", "Title", ""):
        return ""
    # "X-Small — Up to 15 Plants (5 Million Nematodes)" -> "5 Million Nematodes"
    m = _PARENS.search(raw)
    if m:
        return m.group(1).strip()
    m = _SIZE_DETAIL.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def extract_qa(mf: dict[str, dict]) -> list[tuple[str, str]]:
    """The store keeps 6 FAQ pairs per product in metafields with inconsistent
    key naming (`faq_question_1` pairs with `faq_question_answer_1`, but
    `faq_question_2` pairs with `faq_question_2_answer`). Handle both."""
    out = []
    for i in range(1, 9):
        q = mf.get(f"faq_question_{i}")
        a = mf.get(f"faq_question_{i}_answer") or mf.get(f"faq_question_answer_{i}")
        if q and a:
            qt = plain_text(q["value"])
            at = rich_text_to_plain(a["value"])
            if qt and at:
                out.append((qt, at))
    return out


# --------------------------------------------------------------------------- #
# rule application
# --------------------------------------------------------------------------- #


class Transformer:
    def __init__(self, rules: dict, root: str = "."):
        self.r = rules
        self.root = root
        self.dropped: list[Dropped] = []
        self.margin_bands = self._load_lookup()
        self.overrides = self._load_overrides()

    def _load_overrides(self) -> dict[str, dict[str, str]]:
        """Per-SKU field overrides — the manual escape hatch every feed needs.
        One CSV row wins over every rule, and because it lives in git you can
        always see who changed what and why."""
        path = os.path.join(self.root, "config/overrides.csv")
        if not os.path.exists(path):
            return {}
        with open(path, newline="", encoding="utf-8") as fh:
            out: dict[str, dict[str, str]] = {}
            for row in csv.DictReader(fh):
                key = (row.get("sku") or "").strip()
                if not key:
                    continue
                out[key] = {k: v.strip() for k, v in row.items()
                            if k != "sku" and v and v.strip()}
        return out

    def apply_overrides(self, o: Offer) -> None:
        for key in (o.sku, o.offer_id):
            for fname, val in (self.overrides.get(key) or {}).items():
                if not hasattr(o, fname):
                    continue
                cur = getattr(o, fname)
                setattr(o, fname, type(cur)(val) if isinstance(cur, (int, float)) else val)
                o.trace[fname] = "override"

    def _load_lookup(self) -> dict[str, str]:
        spec = self.r["custom_labels"].get("custom_label_4", {})
        path = os.path.join(self.root, spec.get("file", ""))
        if spec.get("type") != "lookup" or not os.path.exists(path):
            return {}
        with open(path, newline="", encoding="utf-8") as fh:
            return {
                row[spec["key"]]: row[spec["column"]]
                for row in csv.DictReader(fh)
                if row.get(spec["key"])
            }

    # ---- 1. exclusions ---------------------------------------------------- #

    def product_context(self, p: dict) -> dict:
        return {
            "status": p.get("status"),
            "online_store_url": p.get("onlineStoreUrl"),
            "product_type": p.get("productType") or "",
            "vendor": p.get("vendor") or "",
            "tags": p.get("tags") or [],
            "title": p.get("title") or "",
        }

    def collapse_variants(self, p: dict) -> list[dict]:
        """Apply the shipping-axis collapse rule: variants that differ only on a
        non-attribute axis become one offer (the cheapest)."""
        variants = p.get("variants", {}).get("nodes", [])
        rule = next(
            (e for e in self.r["exclusions"] if e.get("collapse_on_option")), None
        )
        if not rule:
            return variants
        axes = set(rule["collapse_on_option"])

        def key(v):
            return tuple(
                (so["name"], so["value"])
                for so in v.get("selectedOptions", [])
                if so["name"] not in axes
            )

        groups: dict[tuple, list[dict]] = defaultdict(list)
        for v in variants:
            groups[key(v)].append(v)

        kept = []
        for k, grp in groups.items():
            if len(grp) == 1:
                kept.append(grp[0])
                continue
            grp.sort(key=lambda v: float(v.get("price") or 0))
            kept.append(grp[0])
            for v in grp[1:]:
                self.dropped.append(
                    Dropped(p["id"], v["id"], f"{p['title']} / {v['title']}",
                            rule["id"], rule["reason"])
                )
        return kept

    def excluded(self, p: dict, v: dict, channel: str) -> tuple[str, str] | None:
        ctx = self.product_context(p)
        ctx.update(
            {
                "price": float(v.get("price") or 0),
                "available_for_sale": v.get("availableForSale"),
            }
        )
        for rule in self.r["exclusions"]:
            if rule.get("collapse_on_option"):
                continue
            chans = rule.get("channels")
            if chans and channel not in chans:
                continue
            if ("when" in rule or "any" in rule or "all" in rule):
                from model import match_rule

                if match_rule(rule, ctx):
                    return rule["id"], rule["reason"]
        return None

    # ---- 2. category ------------------------------------------------------ #

    def category(self, p: dict) -> tuple[int, str, str]:
        cats = self.r["categories"]
        title = (p.get("title") or "").lower()
        ptype = p.get("productType") or ""
        tags = set(p.get("tags") or [])
        for rule in cats["rules"]:
            m = rule["match"]
            if "title_contains" in m and any(t.lower() in title for t in m["title_contains"]):
                return rule["to"]["id"], rule["to"]["path"], "title_contains"
            if "product_type" in m and ptype in m["product_type"]:
                return rule["to"]["id"], rule["to"]["path"], "product_type"
            if "tags" in m and tags & set(m["tags"]):
                return rule["to"]["id"], rule["to"]["path"], "tags"
        d = cats["default"]
        return d["id"], d["path"], "default"

    # ---- 3. title --------------------------------------------------------- #

    def title(self, p: dict, v: dict, tvars: dict) -> tuple[str, str]:
        cfg = self.r["titles"]
        ptype = p.get("productType") or ""
        for tmpl in cfg["templates"]:
            applies = tmpl.get("applies_to")
            if applies and ptype not in applies.get("product_type", []):
                continue
            if any(not tvars.get(f) for f in tmpl["requires"]):
                continue
            out = render_template(tmpl["template"], tvars)
            if out:
                return self.sanitize(out, cfg), tmpl["id"]
        return self.sanitize(p.get("title", ""), cfg), "raw_title"

    def sanitize(self, s: str, cfg: dict) -> str:
        for bad, good in (cfg.get("banned_terms") or {}).items():
            s = re.sub(re.escape(bad), good, s, flags=re.I)
        # collapse the double-brand case ("FGMN Nursery X | FGMN Nursery")
        brand = self.r["store"]["brand"]
        if s.count(brand) > 1:
            head, _, tail = s.rpartition(f" | {brand}")
            s = head + tail if head else s
        return truncate_words(_squash(s), cfg["max_length"])

    # ---- 4. availability -------------------------------------------------- #

    def availability(self, v: dict) -> tuple[str, str, int, int]:
        cfg = self.r["availability"]
        qty = v.get("inventoryQuantity") or 0
        cont = v.get("inventoryPolicy") == "CONTINUE"
        avail = v.get("availableForSale")
        max_handling = self.r["shipping"]["max_handling_days"]

        backorder_days = next(
            (p.get("handling_days", max_handling) for p in cfg["policy"]
             if p.get("posture") == "backorder"),
            max_handling,
        )
        if not avail:
            return "out_of_stock", "unavailable", qty, max_handling
        # Explicit continue-selling with no stock is a genuine backorder.
        if qty <= cfg["oversold_threshold"] and cont:
            return "backorder", "backorder", qty, backorder_days
        # 156 feedable variants sit at negative on-hand with policy DENY yet are
        # still sellable. Declaring backorder here would contradict the storefront,
        # which Google resolves against the landing page and disapproves as an
        # availability mismatch. So we mirror the storefront and expose the risk
        # through custom_label_2 + a validator warning instead of lying either way.
        if qty <= cfg["oversold_threshold"]:
            return "in_stock", "oversold", qty, backorder_days
        if qty <= cfg["low_stock_threshold"]:
            return "in_stock", "low", qty, max_handling
        if qty >= cfg["deep_stock_threshold"]:
            return "in_stock", "deep", qty, max_handling
        return "in_stock", "healthy", qty, max_handling

    # ---- 5. custom labels ------------------------------------------------- #

    def labels(self, p: dict, o: Offer) -> dict[str, str]:
        out = {}
        for name, spec in self.r["custom_labels"].items():
            t = spec["type"]
            if t == "bucket":
                val = getattr(o, spec["field"])
                for b in spec["buckets"]:
                    if b["max"] is None or val <= b["max"]:
                        out[name] = b["label"]
                        break
            elif t == "map":
                key = p.get("productType") or ""
                lab = spec["map"].get(key)
                if not lab:
                    for frag, dflt in (spec.get("default_by_category") or {}).items():
                        if frag in o.google_category_path:
                            lab = dflt
                            break
                out[name] = lab or spec.get("default", "other")
            elif t == "computed":
                out[name] = o.stock_posture
            elif t == "field":
                out[name] = getattr(o, spec["field"], "") or spec.get("default", "")
            elif t == "lookup":
                out[name] = self.margin_bands.get(o.sku, spec.get("default", ""))
        return out

    # ---- 6. weight -------------------------------------------------------- #

    def weight(self, p: dict, v: dict, cat_path: str) -> tuple[float, bool]:
        meas = (v.get("inventoryItem") or {}).get("measurement") or {}
        w = (meas.get("weight") or {}) or {}
        val, unit = w.get("value"), w.get("unit")
        if val:
            lb = float(val)
            if unit == "KILOGRAMS":
                lb *= 2.20462
            elif unit == "GRAMS":
                lb *= 0.00220462
            elif unit == "OUNCES":
                lb /= 16
            if lb > 0:
                return round(lb, 2), False
        fb = self.r["shipping"]["weight_fallbacks_lb"]
        if "Potted Houseplants" in cat_path:
            return float(fb["potted_plant"]), True
        return float(fb.get(p.get("productType") or "", fb["default"])), True

    # ---- main ------------------------------------------------------------- #

    def in_segment(self, p: dict, segment: str) -> bool:
        """Segments select which products a feed covers. They stack on top of the
        exclusion rules — a segment can never smuggle an ineligible product in."""
        spec = (self.r.get("segments") or {}).get(segment)
        if not spec or not spec.get("include_any"):
            return True
        from model import match_condition

        ctx = self.product_context(p)
        keys = {m["key"] for m in p.get("metafields", {}).get("nodes", [])}
        for cond in spec["include_any"]:
            if "has_metafield" in cond:
                if cond["has_metafield"] in keys:
                    return True
            elif match_condition(cond, ctx):
                return True
        return False

    def build(self, products: list[dict], channel: str = "google",
              segment: str = "all") -> list[Offer]:
        offers: list[Offer] = []
        store = self.r["store"]
        brand = store["brand"]
        common = self.r["titles"]["common_names"]

        for p in products:
            if not self.in_segment(p, segment):
                continue
            mf = metafields(p)
            cat_id, cat_path, cat_src = self.category(p)
            images = [
                n["image"]["url"]
                for n in p.get("media", {}).get("nodes", [])
                if n.get("image", {}).get("url")
            ]
            desc_base = plain_text(p.get("descriptionHtml"))
            qa = extract_qa(mf)

            target_pests = []
            if "target_pests" in mf:
                try:
                    target_pests = [
                        x for x in json.loads(mf["target_pests"]["value"])
                        if x and x.lower() != "others"
                    ]
                except (json.JSONDecodeError, TypeError):
                    pass
            primary_pest = target_pests[0] if target_pests else ""

            plant_h = dimension(mf.get("height", {}).get("value"))
            leaf = dimension(mf.get("leaf_size", {}).get("value"))
            rarity = ""
            if "rarity" in mf:
                try:
                    rarity = RARITY_LABELS.get(int(mf["rarity"]["value"]), "")
                except (TypeError, ValueError):
                    pass

            rating, rcount = None, None
            if p.get("ratingVal"):
                try:
                    rating = float(json.loads(p["ratingVal"]["value"])["value"])
                    rcount = int(p["ratingCount"]["value"]) if p.get("ratingCount") else None
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass

            for v in self.collapse_variants(p):
                drop = self.excluded(p, v, channel)
                if drop:
                    self.dropped.append(
                        Dropped(p["id"], v["id"], f"{p['title']} / {v['title']}", *drop)
                    )
                    continue

                o = Offer()
                o.offer_id = v.get("sku") or f"shopify-{v['legacyResourceId']}"
                o.item_group_id = str(p["legacyResourceId"])
                o.sku = v.get("sku") or ""
                o.gtin = (v.get("barcode") or "").strip()
                o.mpn = o.sku
                o.identifier_exists = bool(o.gtin or o.mpn)
                o.brand = brand
                o.google_category_id = cat_id
                o.google_category_path = cat_path
                o.product_type_path = " > ".join(
                    x for x in [p.get("productType"), variant_qualifier(v)] if x
                )
                o.link = p.get("onlineStoreUrl") or f"{store['url']}/products/{p['handle']}"
                if o.sku:
                    o.link += f"?variant={v['legacyResourceId']}"
                o.images = ([v["image"]["url"]] if v.get("image") else []) + [
                    i for i in images if not v.get("image") or i != v["image"]["url"]
                ]
                o.price = float(v.get("price") or 0)
                cap = v.get("compareAtPrice")
                if cap and float(cap) > o.price:
                    o.sale_price, o.price = o.price, float(cap)
                o.currency = store["currency"]
                o.target_pests = target_pests
                o.primary_pest = primary_pest

                qual = variant_qualifier(v)
                tvars = {
                    "brand": brand,
                    "title": p.get("title", ""),
                    "seo_title": (p.get("seo") or {}).get("title") or "",
                    "common_name": common.get(p.get("title", ""), p.get("title", "")),
                    "primary_pest": primary_pest,
                    "variant_qualifier": qual,
                    "plant_height": plant_h,
                }
                o.title, rule_id = self.title(p, v, tvars)
                o.trace["title"] = rule_id
                o.trace["category"] = cat_src
                o.short_title = truncate_words(
                    tvars["common_name"] or p.get("title", ""), 65
                )

                o.availability, o.stock_posture, o.quantity, o.max_handling_days = (
                    self.availability(v)
                )
                sh = self.r["shipping"]
                o.min_handling_days = sh["min_handling_days"]
                o.min_transit_days = sh["min_transit_days"]
                o.max_transit_days = sh["max_transit_days"]
                o.weight_lb, o.weight_estimated = self.weight(p, v, cat_path)

                # description + appended structured facts
                bits = [desc_base] if desc_base else []
                extras = {
                    "target_pests": ", ".join(target_pests),
                    "plant_height": plant_h,
                    "leaf_size": leaf,
                    "rarity_label": rarity,
                }
                for spec in self.r["descriptions"]["append_fields"]:
                    val = extras.get(spec["when_present"])
                    if val:
                        bits.append(spec["format"].format(value=val))
                if qual:
                    bits.append(f"Size: {qual}.")
                if o.stock_posture == "backorder":
                    bits.append(
                        "Cultured to order and shipped fresh — allow "
                        f"{o.max_handling_days} business days before dispatch."
                    )
                o.description = truncate_words(
                    _squash(" ".join(bits)) or
                    self.r["descriptions"]["fallback"].format(
                        title=p.get("title", ""), brand=brand, category_path=cat_path
                    ),
                    self.r["descriptions"]["max_length"],
                )

                # highlights — Google wants 2-100, each <=150 chars
                hi = []
                if target_pests:
                    hi.append(f"Controls {', '.join(target_pests[:4])}")
                if qual:
                    hi.append(f"Coverage: {qual}")
                if "Pest Control" in cat_path:
                    hi += [
                        "Biological control — no synthetic residues",
                        "Safe around people and pets when used as directed",
                        "Shipped live with cold-pack protection",
                    ]
                if plant_h:
                    hi.append(f"Ships at approximately {plant_h} tall")
                if rarity:
                    hi.append(f"{rarity} collector specimen")
                hi = [truncate_words(h, 150) for h in hi][:100]
                # Google rejects a single highlight; 2 is the floor.
                o.highlights = hi if len(hi) >= 2 else []

                # details — structured attributes, the AI-surface fuel
                det: list[tuple[str, str, str]] = []
                if target_pests:
                    det.append(("Pest control", "Target pests", ", ".join(target_pests)))
                for key, label in (
                    ("application_and_rates", "Application & rates"),
                    ("environmental_requirements", "Environmental requirements"),
                    ("shipping_storage", "Shipping & storage"),
                    ("when_to_use", "When to use"),
                ):
                    if key in mf:
                        txt = rich_text_to_plain(mf[key]["value"])
                        if txt:
                            det.append(("Usage", label, truncate_words(txt, 1000)))
                if plant_h:
                    det.append(("Specimen", "Height", plant_h))
                if leaf:
                    det.append(("Specimen", "Leaf size", leaf))
                if rarity:
                    det.append(("Specimen", "Rarity", rarity))
                o.details = det[:100]
                o.qa = qa[:10]
                o.variant_options = [
                    (so["name"], so["value"])
                    for so in v.get("selectedOptions", [])
                    if so["name"] not in NON_ATTRIBUTE_AXES
                    and so["value"] not in ("Default Title",)
                ]
                if "Apparel" in cat_path:
                    opts = {k.lower(): v for k, v in o.variant_options}
                    o.color = opts.get("color", "")
                    o.size = opts.get("size", "")
                    o.age_group = "adult"
                    o.gender = "unisex"
                o.star_rating, o.review_count = rating, rcount
                o.primary_pest_slug = slug(primary_pest) if primary_pest else ""
                o.custom_labels = self.labels(p, o)
                self.apply_overrides(o)
                offers.append(o)
        return offers


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# attach the dynamic attribute the label engine reads
Offer.primary_pest_slug = ""  # type: ignore[attr-defined]
