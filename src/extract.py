#!/usr/bin/env python3
"""
Shopify Admin GraphQL -> raw/catalog.json

  SHOPIFY_STORE=8a2744-3d.myshopify.com \
  SHOPIFY_CLIENT_ID=... SHOPIFY_CLIENT_SECRET=... \
  python3 src/extract.py

Auth is the client credentials grant: the app exchanges its own client ID and
secret for a 24-hour access token, with no redirect flow and no long-lived
`shpat_` token to leak. It only works because the app and the store belong to
the same Shopify organisation - which is exactly the case here.

Shopify retired admin-created custom apps, so the one-time token this pipeline
originally assumed no longer exists. A stored SHOPIFY_TOKEN is still honoured
for anyone running against an older app.

Paginates by product; retries on throttle using Shopify's own cost extensions
rather than a blind sleep.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
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


def mint_token(store: str, client_id: str, client_secret: str) -> str:
    """Exchange client credentials for a 24-hour Admin API access token."""
    url = f"https://{store}/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            # 401 here means the secret was rotated or the app was uninstalled.
            # Say so plainly rather than surfacing a bare HTTP error.
            detail = e.read().decode(errors="replace")[:200]
            raise RuntimeError(
                f"token request failed ({e.code}). Check SHOPIFY_CLIENT_ID / "
                f"SHOPIFY_CLIENT_SECRET and that the app is still installed "
                f"on {store}. Response: {detail}"
            ) from None
    else:
        raise RuntimeError("exhausted retries minting an access token")

    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"no access_token in response: {payload}")

    granted = set(filter(None, (payload.get("scope") or "").split(",")))
    missing = {"read_products", "read_inventory"} - granted
    if missing:
        # Not fatal - the API call itself will fail informatively - but this
        # points at the real cause: a scope was never released on the version.
        print(f"  ! token is missing scope(s): {', '.join(sorted(missing))}",
              file=sys.stderr)
    print(f"  minted access token, valid {payload.get('expires_in', '?')}s, "
          f"scopes: {payload.get('scope', '?')}")
    return token


def resolve_token(store: str) -> str:
    """Prefer a pre-issued token if one is set; otherwise mint one."""
    token = os.environ.get("SHOPIFY_TOKEN")
    if token:
        return token
    cid = os.environ.get("SHOPIFY_CLIENT_ID")
    secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET (or a legacy "
            "SHOPIFY_TOKEN)"
        )
    return mint_token(store, cid, secret)


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
    if not store:
        print("SHOPIFY_STORE is required", file=sys.stderr)
        return 2
    try:
        token = resolve_token(store)
    except RuntimeError as e:
        print(f"  ! {e}", file=sys.stderr)
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
