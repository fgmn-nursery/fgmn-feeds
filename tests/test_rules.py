"""
Regression tests for the rules that are expensive to get wrong.

Run: python3 -m pytest tests/ -q
"""
import json
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from model import plain_text, rich_text_to_plain, truncate_words  # noqa: E402
from transform import Transformer, variant_qualifier  # noqa: E402
from validate import RISK_TERMS, _is_affirmative, validate_offers  # noqa: E402


@pytest.fixture(scope="module")
def rules():
    return yaml.safe_load(open(os.path.join(ROOT, "config/rules.yaml"), encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return json.load(open(os.path.join(ROOT, "raw/catalog.json"), encoding="utf-8"))["products"]


@pytest.fixture(scope="module")
def offers(rules, catalog):
    return Transformer(rules, root=ROOT).build(catalog, channel="google")


# --------------------------------------------------------------------------- #
# text handling
# --------------------------------------------------------------------------- #

def test_truncate_never_splits_a_word():
    s = "Beneficial nematodes for fungus gnat control in houseplants"
    assert not truncate_words(s, 30).endswith("gna")
    assert len(truncate_words(s, 30)) <= 30


def test_plain_text_strips_markup_and_entities():
    assert plain_text("<p>Safe &amp; organic</p><br>Use&nbsp;weekly") == "Safe & organic Use weekly"


def test_rich_text_metafield_ast_is_flattened():
    ast = json.dumps({"type": "root", "children": [
        {"type": "paragraph", "children": [{"type": "text", "value": "Apply at dusk."}]}]})
    assert rich_text_to_plain(ast) == "Apply at dusk."


def test_rich_text_falls_back_on_non_json():
    assert rich_text_to_plain("<p>plain html</p>") == "plain html"


# --------------------------------------------------------------------------- #
# variant handling
# --------------------------------------------------------------------------- #

def test_variant_qualifier_prefers_the_parenthetical():
    v = {"selectedOptions": [{"name": "Size",
                              "value": "Small — Up to 30 Plants (10 Million Nematodes)"}],
         "title": "Small"}
    assert variant_qualifier(v) == "10 Million Nematodes"


def test_variant_qualifier_ignores_checkout_axes():
    v = {"selectedOptions": [{"name": "Shipping Options", "value": "Heat Pack"}],
         "title": "Heat Pack"}
    assert variant_qualifier(v) == ""


def test_shipping_axis_variants_are_collapsed(rules, catalog):
    tx = Transformer(rules, root=ROOT)
    tx.build(catalog, channel="google")
    collapsed = [d for d in tx.dropped if d.rule_id == "shipping_axis_variant"]
    assert collapsed, "the collapse rule should fire on this catalog"
    # nothing should be collapsed away that was the only offer for its product
    assert all("/" in d.title for d in collapsed)


# --------------------------------------------------------------------------- #
# the things that silently cost money
# --------------------------------------------------------------------------- #

def test_no_duplicate_offer_ids(offers):
    ids = [o.offer_id for o in offers]
    assert len(ids) == len(set(ids))


def test_every_offer_has_the_google_required_attributes(offers):
    for o in offers:
        assert o.offer_id and o.title and o.description and o.brand
        assert o.link.startswith("http")
        assert o.images, f"{o.offer_id} has no image"
        assert o.price > 0


def test_titles_fit_every_channel_cap(rules, offers):
    cap = min(c["title_max"] for c in rules["channels"].values())
    assert all(len(o.title) <= cap for o in offers)


def test_gift_cards_and_service_addons_never_reach_a_feed(offers):
    blob = " ".join(o.title.lower() for o in offers)
    assert "gift card" not in blob
    assert "shipping protection" not in blob


def test_draft_products_are_excluded(rules, catalog, offers):
    drafts = {p["title"] for p in catalog if p["status"] != "ACTIVE"}
    assert not any(any(d in o.title for d in drafts) for o in offers if drafts)


def test_availability_matches_the_storefront_not_the_stock_count(offers):
    """An offer that is sellable must not be declared out_of_stock — Google
    resolves availability against the landing page and disapproves mismatches."""
    for o in offers:
        if o.stock_posture == "oversold":
            assert o.availability == "in_stock"


def test_oversold_offers_are_labelled_for_bid_control(offers):
    over = [o for o in offers if o.stock_posture == "oversold"]
    assert over, "this catalog has oversold offers"
    assert all(o.custom_labels["custom_label_2"] == "oversold" for o in over)


def test_no_biological_control_product_lands_in_the_animals_branch(offers):
    """Google restricts the transportation of live animals. Every live-organism
    SKU must sit under Home & Garden, never Animals & Pet Supplies."""
    for o in offers:
        assert "Animals & Pet Supplies" not in o.google_category_path


def test_highlights_are_absent_or_at_least_two(offers):
    """Google rejects a lone product_highlight."""
    assert all(len(o.highlights) != 1 for o in offers)


def test_weights_are_always_positive(offers):
    assert all(o.weight_lb > 0 for o in offers)


def test_sale_price_is_always_below_price(offers):
    assert all(o.sale_price < o.price for o in offers if o.sale_price is not None)


# --------------------------------------------------------------------------- #
# channel dialects
# --------------------------------------------------------------------------- #

def test_microsoft_and_pinterest_use_spaced_availability(rules):
    for ch in ("microsoft", "pinterest"):
        assert rules["channels"][ch]["availability_map"]["in_stock"] == "in stock"


def test_openai_preorder_uses_an_underscore(rules):
    assert rules["channels"]["openai"]["availability_map"]["preorder"] == "pre_order"


def test_google_preorder_has_no_underscore(rules):
    assert rules["channels"]["google"]["availability_map"]["preorder"] == "preorder"


def test_openai_checkout_requires_search_eligibility(rules):
    c = rules["channels"]["openai"]
    if c["is_eligible_checkout"]:
        assert c["is_eligible_search"], "checkout requires is_eligible_search=true"


def test_microsoft_sets_an_expiration_inside_the_30_day_window(rules):
    assert 0 < rules["channels"]["microsoft"]["set_expiration_date_days"] < 30


# --------------------------------------------------------------------------- #
# policy classifier
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,flagged", [
    ("Kills pests with a powerful pesticide", True),
    ("They reduce pesticide use across the greenhouse", False),
    ("A pesticide-free approach to thrips", False),
    ("Incompatible with broad-spectrum insecticides", False),
    ("We ship live animals overnight", True),
    ("Unlike chemical insecticides, predators keep working", False),
])
def test_risk_term_detection_ignores_negated_usage(text, flagged):
    hits = [m for m in RISK_TERMS.finditer(text) if _is_affirmative(text, m)]
    assert bool(hits) is flagged


def test_generated_feed_has_zero_blocking_errors(rules, offers):
    errors = [f for f in validate_offers(offers, rules["channels"]["google"])
              if f.severity == "error"]
    assert not errors, f"{len(errors)} blocking errors: {errors[:5]}"
