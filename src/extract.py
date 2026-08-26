#!/usr/bin/env python3
"""
Shopify Admin GraphQL -> raw/catalog.json

  SHOPIFY_STORE=8a2744-3d.myshopify.com \
  SHOPIFY_TOKEN=shpat_xxx \
  python3 src/extract.py

Needs a custom app token with read_products and read_inventory.
Paginates by product; retries on throttle using Shopify's own cost extensions
rather than a blind sleep.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-07")
PAGE_SIZE = int(os.environ.get("SHOPIFY_PAGE_SIZE", "50"))

QUERY = """
query($cursor: String, $n: Int!) {
  products(first: $n, after: $cursor, sortKey: ID) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id legacyResourceId title handle productType vendor tags status
      createdAt updatedAt publishedAt totalInventory
      descriptionHtml onlineStoreUrl
      category { id fullName }
      seo { title description }
      media(first: 10) { nodes { ... on MediaImage { image { url altText width height } } } }
      metafields(first: 25, namespace: "custom") { nodes { key value type } }
      googleCat: metafield(namespace: "mm-google-shopping", key: "google_product_category") { value }
      fbCat: metafield(namespace: "mc-facebook", key: "google_product_category") { value }
      customProduct: metafield(namespace: "mm-google-shopping", key: "custom_product") { value }
      ratingVal: metafield(namespace: "reviews", key: "rating") { value }
      ratingCount: metafield(namespace: "reviews", key: "rating_count") { value }
      jmData: metafield(namespace: "judgeme", key: "review_widget_data") { value }
      variants(first: 25) {
        nodes {
          id legacyResourceId title sku barcode price compareAtPrice
          inventoryQuantity availableForSale inventoryPolicy taxable
          selectedOptions { name value }
          image { url }
          inventoryItem {
            requiresShipping tracked
            measurement { weight { unit value } }
          }
        }
      }
    }
  }
}
"""


def call(store: str, token: str, cursor: str | None) -> dict:
    url = f"https://{store}/admin/api/{API_VERSION}/graphql.json"
    body = json.dumps({"query": QUERY, "variables": {"cursor": cursor, "n": PAGE_SIZE}})
    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise
        if payload.get("errors"):
            throttled = any(
                (e.get("extensions") or {}).get("code") == "THROTTLED"
                for e in payload["errors"]
            )
            if throttled and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(json.dumps(payload["errors"], indent=2))
        # Stay polite: if the leaky bucket is low, wait for it to refill.
        cost = (payload.get("extensions") or {}).get("cost", {})
        st = cost.get("throttleStatus", {})
        if st and st.get("currentlyAvailable", 1000) < cost.get("requestedQueryCost", 0) * 2:
            time.sleep(1.0)
        return payload["data"]["products"]
    raise RuntimeError("exhausted retries")


def main() -> int:
    store = os.environ.get("SHOPIFY_STORE")
    token = os.environ.get("SHOPIFY_TOKEN")
    if not store or not token:
        print("SHOPIFY_STORE and SHOPIFY_TOKEN are required", file=sys.stderr)
        return 2

    out, cursor, pages = [], None, 0
    while True:
        page = call(store, token, cursor)
        out.extend(page["nodes"])
        pages += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(root, "raw"), exist_ok=True)
    path = os.path.join(root, "raw/catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"products": out}, fh)

    variants = sum(len(p["variants"]["nodes"]) for p in out)
    print(f"  extracted {len(out)} products / {variants} variants in {pages} pages")

    # A truncated extract is worse than no extract: it silently delists products.
    floor = int(os.environ.get("MIN_EXPECTED_PRODUCTS", "100"))
    if len(out) < floor:
        print(f"  ! only {len(out)} products, expected >= {floor} — refusing to publish",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
