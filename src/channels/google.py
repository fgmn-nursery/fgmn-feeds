"""Google Merchant Center — RSS 2.0 with the g: namespace."""
from __future__ import annotations

from xml.sax.saxutils import escape

from model import Offer, truncate_words

NS = 'xmlns:g="http://base.google.com/ns/1.0"'


def _t(tag: str, val, indent: str = "      ") -> str:
    if val in (None, "", []):
        return ""
    return f"{indent}<g:{tag}>{escape(str(val))}</g:{tag}>\n"


def render(offers: list[Offer], cfg: dict, store: dict) -> str:
    amap = cfg["availability_map"]
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<rss version="2.0" {NS}>\n  <channel>\n',
        f"    <title>{escape(store['name'])} — Product Feed</title>\n",
        f"    <link>{escape(store['url'])}</link>\n",
        "    <description>Primary Shopping feed. Generated from Shopify by the "
        "FGMN feed pipeline; do not edit by hand.</description>\n",
    ]

    for o in offers:
        x = ["    <item>\n"]
        x.append(_t("id", truncate_words(o.offer_id, cfg["id_max"])))
        x.append(_t("title", truncate_words(o.title, cfg["title_max"])))
        x.append(_t("description", truncate_words(o.description, cfg["description_max"])))
        x.append(_t("link", o.link))
        if o.images:
            x.append(_t("image_link", o.images[0]))
            for img in o.images[1:11]:
                x.append(_t("additional_image_link", img))
        if cfg.get("include_short_title"):
            x.append(_t("short_title", o.short_title))

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
        x.append(_t("color", o.color))
        x.append(_t("size", o.size))
        x.append(_t("age_group", o.age_group))
        x.append(_t("gender", o.gender))
        x.append(_t("google_product_category", o.google_category_id))
        x.append(_t("product_type", truncate_words(o.product_type_path, 750)))
        x.append(_t("item_group_id", o.item_group_id))

        for name, val in o.variant_options[:30]:
            x.append(
                "      <g:variant_option>\n"
                f"        <g:name>{escape(name)}</g:name>\n"
                f"        <g:value>{escape(val)}</g:value>\n"
                "      </g:variant_option>\n"
            )

        # shipping: weight + handling/transit. Price omitted so Merchant Center
        # account-level rates apply — the feed should not hardcode carrier cost.
        x.append(
            "      <g:shipping>\n"
            f"        <g:country>{store['country']}</g:country>\n"
            "      </g:shipping>\n"
        )
        x.append(_t("shipping_weight", f"{o.weight_lb:.2f} lb"))
        x.append(_t("min_handling_time", o.min_handling_days))
        x.append(_t("max_handling_time", o.max_handling_days))
        x.append(_t("min_transit_time", o.min_transit_days))
        x.append(_t("max_transit_time", o.max_transit_days))

        if cfg.get("include_product_highlight") and len(o.highlights) >= 2:
            for h in o.highlights[:100]:
                x.append(_t("product_highlight", h))
        if cfg.get("include_product_detail"):
            for section, name, val in o.details:
                x.append(
                    "      <g:product_detail>\n"
                    f"        <g:section_name>{escape(truncate_words(section, 140))}</g:section_name>\n"
                    f"        <g:attribute_name>{escape(truncate_words(name, 140))}</g:attribute_name>\n"
                    f"        <g:attribute_value>{escape(truncate_words(val, 1000))}</g:attribute_value>\n"
                    "      </g:product_detail>\n"
                )
        if cfg.get("include_question_and_answer"):
            for q, a in o.qa:
                x.append(
                    "      <g:question_and_answer>\n"
                    f"        <g:question>{escape(truncate_words(q, 500))}</g:question>\n"
                    f"        <g:answer>{escape(truncate_words(a, 1000))}</g:answer>\n"
                    "      </g:question_and_answer>\n"
                )

        for i in range(5):
            x.append(_t(f"custom_label_{i}", o.custom_labels.get(f"custom_label_{i}")))
        x.append("    </item>\n")
        out.append("".join(x))

    out.append("  </channel>\n</rss>\n")
    return "".join(out)
