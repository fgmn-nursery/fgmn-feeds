"""
OpenAI / Agentic Commerce product feed — flat column model.

Per developers.openai.com/commerce/specs/file-upload/products this is the
representation OpenAI Ads reads, and the one carrying the eligibility flags.
Delivery: the Ads Manager offers Hosted URL, SFTP, or manual upload. This
pipeline targets Hosted URL — the same file the other channels read. Whichever
is used, OpenAI expires products 14 days after their last update, so the feed
must be refreshed well inside that window.

Two traps encoded here that a Google-format feed will get wrong:
  * availability preorder is `pre_order` (underscore), not Google's `preorder`
  * the eligibility flags are `is_eligible_search` / `is_eligible_checkout` /
    `is_ads_eligible` — `enable_search` and `is_ads_enabled` are silently ignored
"""
from __future__ import annotations

import csv
import io
import json

from model import Offer, truncate_words

COLUMNS = [
    # eligibility
    "is_eligible_search", "is_eligible_checkout", "is_ads_eligible",
    # basic
    "item_id", "title", "description", "url", "gtin", "mpn",
    # item info
    "brand", "condition", "product_category", "weight", "item_weight_unit",
    # media
    "image_url", "additional_image_urls",
    # price
    "price", "sale_price",
    # availability
    "availability", "availability_date",
    # variants
    "group_id", "listing_has_variations", "variant_dict", "item_group_title",
    "size", "offer_id",
    # fulfillment
    "shipping", "is_digital",
    # merchant
    "seller_name", "seller_url", "seller_privacy_policy", "seller_tos",
    # returns
    "accepts_returns", "return_deadline_in_days", "accepts_exchanges", "return_policy",
    # reviews
    "review_count", "star_rating", "q_and_a",
    # geo
    "target_countries",
    # ads segmentation
    "ads_metadata",
]


def render(offers: list[Offer], cfg: dict, store: dict) -> str:
    amap = cfg["availability_map"]
    pol = store["policies"]
    ret = store["returns"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter="\t",
                       lineterminator="\n", extrasaction="ignore",
                       quoting=csv.QUOTE_MINIMAL)
    w.writeheader()

    for o in offers:
        variant_dict = {name: val for name, val in o.variant_options}
        row = {
            "is_eligible_search": str(cfg["is_eligible_search"]).lower(),
            "is_eligible_checkout": str(cfg["is_eligible_checkout"]).lower(),
            "is_ads_eligible": str(cfg["is_ads_eligible"]).lower(),

            "item_id": truncate_words(o.offer_id, cfg["id_max"]),
            "title": truncate_words(o.title, cfg["title_max"]),
            "description": truncate_words(o.description, cfg["description_max"]),
            "url": o.link,
            "gtin": o.gtin,
            "mpn": truncate_words(o.mpn, 70),

            "brand": truncate_words(o.brand, 70),
            "condition": o.condition,
            "product_category": o.google_category_path,
            "weight": f"{o.weight_lb:.2f}",
            "item_weight_unit": "lb",

            "image_url": o.images[0] if o.images else "",
            "additional_image_urls": ",".join(o.images[1:11]),

            "price": f"{o.price:.2f} {o.currency}",
            "sale_price": f"{o.sale_price:.2f} {o.currency}" if o.sale_price is not None else "",

            "availability": amap[o.availability],
            "availability_date": "",

            "group_id": o.item_group_id,
            "listing_has_variations": str(bool(o.variant_options)).lower(),
            "variant_dict": json.dumps(variant_dict, ensure_ascii=False) if variant_dict else "",
            "item_group_title": truncate_words(o.short_title, 150),
            "size": next((v for k, v in o.variant_options if k.lower() == "size"), ""),
            "offer_id": o.offer_id,

            # country:region:service:price:minhandle:maxhandle:mintransit:maxtransit
            "shipping": (
                f"{store['country']}::Standard::"
                f"{o.min_handling_days}:{o.max_handling_days}:"
                f"{o.min_transit_days}:{o.max_transit_days}"
            ),
            "is_digital": "false",

            "seller_name": truncate_words(store["name"], 70),
            "seller_url": store["url"],
            "seller_privacy_policy": pol["privacy"],
            "seller_tos": pol["terms"],

            "accepts_returns": str(ret["accepts_returns"]).lower(),
            "return_deadline_in_days": ret["return_deadline_days"],
            "accepts_exchanges": str(ret["accepts_exchanges"]).lower(),
            "return_policy": pol["refund"],

            "review_count": o.review_count if o.review_count else "",
            "star_rating": f"{o.star_rating:.2f}" if o.star_rating else "",
            "q_and_a": (
                json.dumps([{"q": q, "a": a} for q, a in o.qa], ensure_ascii=False)
                if cfg.get("include_q_and_a") and o.qa else ""
            ),

            "target_countries": store["country"],
            "ads_metadata": json.dumps(
                {
                    "price_band": o.custom_labels.get("custom_label_0", ""),
                    "product_line": o.custom_labels.get("custom_label_1", ""),
                    "stock_posture": o.custom_labels.get("custom_label_2", ""),
                    "primary_pest": o.custom_labels.get("custom_label_3", ""),
                },
                ensure_ascii=False,
            ),
        }
        # Tabs and newlines inside values would break TSV parsing outright.
        w.writerow({k: str(v).replace("\t", " ").replace("\n", " ") for k, v in row.items()})
    return buf.getvalue()
