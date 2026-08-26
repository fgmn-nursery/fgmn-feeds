"""
Pinterest Catalogs — CSV. Google attribute names, but the availability enum
uses spaces, custom labels allow 511 chars, and `average_review_rating` has no
Google equivalent. Pinterest has no backorder state.
"""
from __future__ import annotations

import csv
import io

from model import Offer, truncate_words

COLUMNS = [
    "id", "title", "description", "link", "image_link", "additional_image_link",
    "price", "sale_price", "availability", "condition", "brand",
    "google_product_category", "product_type", "item_group_id",
    "gtin", "mpn", "average_review_rating", "free_shipping_label",
    "custom_label_0", "custom_label_1", "custom_label_2", "custom_label_3",
    "custom_label_4",
]


def render(offers: list[Offer], cfg: dict, store: dict) -> str:
    amap = cfg["availability_map"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n",
                       extrasaction="ignore")
    w.writeheader()
    for o in offers:
        w.writerow({
            "id": truncate_words(o.offer_id, cfg["id_max"]),
            "title": truncate_words(o.title, cfg["title_max"]),
            "description": truncate_words(o.description, cfg["description_max"]),
            "link": o.link,
            "image_link": o.images[0] if o.images else "",
            "additional_image_link": ",".join(o.images[1:11]),
            "price": f"{o.price:.2f} {o.currency}",
            "sale_price": f"{o.sale_price:.2f} {o.currency}" if o.sale_price is not None else "",
            "availability": amap[o.availability],
            "condition": o.condition,
            "brand": truncate_words(o.brand, 1000),
            "google_product_category": truncate_words(o.google_category_path, 750),
            "product_type": truncate_words(o.product_type_path, 750),
            "item_group_id": o.item_group_id,
            "gtin": o.gtin,
            "mpn": o.mpn,
            "average_review_rating": f"{o.star_rating:.2f}" if (
                cfg.get("include_average_review_rating") and o.star_rating) else "",
            "free_shipping_label": "",
            **{f"custom_label_{i}": truncate_words(
                o.custom_labels.get(f"custom_label_{i}", ""), 511) for i in range(5)},
        })
    return buf.getvalue()
