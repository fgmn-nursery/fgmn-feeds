"""
Microsoft (Bing) Merchant Center — Google-shaped RSS with three differences
that break silently if you just point Bing at the Google file:

  1. availability uses SPACES: "in stock" / "out of stock". Underscores fail.
  2. MMC has no `backorder` state — backordered items map to "in stock" with
     the handling time carrying the lead-time signal.
  3. products auto-expire 30 days after last update. We stamp expiration_date
     explicitly so a stalled pipeline surfaces as an error, not a silent delist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from model import Offer, truncate_words

NS = 'xmlns:g="http://base.google.com/ns/1.0"'


def _t(tag: str, val, indent: str = "      ") -> str:
    if val in (None, "", []):
        return ""
    return f"{indent}<g:{tag}>{escape(str(val))}</g:{tag}>\n"


def render(offers: list[Offer], cfg: dict, store: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    expires = (now + timedelta(days=cfg["set_expiration_date_days"])).strftime("%Y-%m-%d")
    amap = cfg["availability_map"]

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<rss version="2.0" {NS}>\n  <channel>\n',
        f"    <title>{escape(store['name'])} — Microsoft Shopping Feed</title>\n",
        f"    <link>{escape(store['url'])}</link>\n",
        "    <description>Microsoft Merchant Center feed.</description>\n",
    ]
    for o in offers:
        x = ["    <item>\n"]
        x.append(_t("id", truncate_words(o.offer_id, cfg["id_max"])))
        x.append(_t("title", truncate_words(o.title, cfg["title_max"])))
        x.append(_t("description", truncate_words(o.description, cfg["description_max"])))
        x.append(_t("link", o.link))
        x.append(_t("image_link", o.images[0] if o.images else ""))
        x.append(_t("availability", amap[o.availability]))
        x.append(_t("price", f"{o.price:.2f} {o.currency}"))
        if o.sale_price is not None:
            x.append(_t("sale_price", f"{o.sale_price:.2f} {o.currency}"))
        x.append(_t("brand", o.brand))
        if o.gtin:
            x.append(_t("gtin", o.gtin))
        if o.mpn:
            x.append(_t("mpn", o.mpn))
        if not o.identifier_exists:
            x.append(_t("identifier_exists", "no"))
        x.append(_t("condition", o.condition))
        # MMC caps googleProductCategory at 255 chars and prefers the path
        x.append(_t("google_product_category", truncate_words(o.google_category_path, 255)))
        x.append(_t("product_type", truncate_words(o.product_type_path, 750)))
        x.append(_t("item_group_id", o.item_group_id))
        x.append(_t("channel", cfg["channel_value"]))          # MMC-only, required
        x.append(_t("expiration_date", expires))               # defeats silent 30-day expiry
        x.append(_t("shipping_weight", f"{o.weight_lb:.2f} lb"))
        for i in range(5):
            x.append(_t(f"custom_label_{i}", o.custom_labels.get(f"custom_label_{i}")))
        x.append("    </item>\n")
        out.append("".join(x))
    out.append("  </channel>\n</rss>\n")
    return "".join(out)
