# FGMN Nursery — Product Feed Pipeline

A bespoke replacement for DataFeedWatch. Pulls the live Shopify catalog, applies
a declarative rules layer, and emits validated feeds for Google Merchant Center,
OpenAI/ChatGPT, Microsoft (Bing), and Pinterest.

```
Shopify Admin GraphQL
        │
        ▼
   raw/catalog.json          ← extract.py (paginated, throttle-aware)
        │
        ▼
  ┌─────────────────┐
  │  rules engine   │        ← config/rules.yaml + config/overrides.csv
  │  transform.py   │           exclusions · categories · titles ·
  └─────────────────┘           availability · labels · enrichment
        │
        ▼
   canonical Offer[]          one truthful record per sellable variant
        │
   ┌────┼────┬────────┬──────────┐
   ▼    ▼    ▼        ▼          ▼
 google openai microsoft pinterest  (channel projections)
   │      │      │        │
   ▼      ▼      ▼        ▼
        validate.py               ← 20 checks, each mapped to a real
        │                            disapproval reason
        ▼
   out/ + run_report.json
```

## Why this shape

Every channel gets its own dialect from **one** canonical record. A Bing quirk
can't corrupt the Google feed, and adding a channel is a ~100-line projection
rather than a rewrite. All business logic lives in `config/rules.yaml`, so a
change to a title template is a pull request with a diff and a test run — not an
undocumented click in a SaaS admin panel.

## Quick start

```bash
pip install pyyaml pytest

export SHOPIFY_STORE=8a2744-3d.myshopify.com
export SHOPIFY_TOKEN=shpat_…            # custom app: read_products, read_inventory

python3 src/extract.py                  # → raw/catalog.json
python3 -m pytest tests/ -q             # rules regression suite
python3 src/generate.py                 # → out/
python3 src/generate.py --channel google --strict
```

## Layout

| Path | What it is |
|---|---|
| `config/rules.yaml` | **The product.** Exclusions, category map, title templates, availability policy, custom labels, per-channel settings. |
| `config/overrides.csv` | Per-SKU manual overrides. One row beats every rule. Version-controlled, so you can always see who changed what. |
| `config/margin_bands.csv` | Optional `sku,band` lookup feeding `custom_label_4`. |
| `src/extract.py` | Shopify Admin GraphQL → `raw/catalog.json`. Refuses to write a truncated extract. |
| `src/model.py` | The canonical `Offer` record and the rules-engine primitives. |
| `src/transform.py` | Shopify → `Offer[]`. Every decision reads from the YAML. |
| `src/channels/*.py` | One projection per channel. |
| `src/validate.py` | Pre-flight checks. Errors block publication; warnings become the fix list. |
| `src/generate.py` | CLI entrypoint; writes feeds, findings, and `run_report.json`. |
| `tests/test_rules.py` | 30 regression tests over the rules that are expensive to get wrong. |

## Channel differences this handles

These are the traps that make "just point Bing at the Google file" quietly lose
you coverage:

| | Google | OpenAI | Microsoft | Pinterest |
|---|---|---|---|---|
| in stock | `in_stock` | `in_stock` | `in stock` | `in stock` |
| preorder | `preorder` | `pre_order` | `preorder` | `preorder` |
| backorder | `backorder` | `backorder` | *(none — maps to in stock)* | *(none — maps to out of stock)* |
| id field | `id` | `item_id` | `id` | `id` |
| id max | 50 | 100 | 50 | 127 |
| title max | 150 | 150 | 150 | 500 |
| description max | 5,000 | 5,000 | 10,000 | 10,000 |
| variant group | `item_group_id` | `group_id` | `item_group_id` | `item_group_id` |
| required extra | — | `is_eligible_search`, `is_eligible_checkout`, `is_ads_eligible` | `channel`, `expiration_date` | — |
| delivery | hosted URL | hosted URL *(also accepts SFTP or manual upload)* | hosted URL | hosted URL |

Microsoft silently expires products 30 days after last update, so the pipeline
stamps `expiration_date` at +29 days: a stalled pipeline becomes a visible error
instead of a slow delisting. OpenAI expires them after **14 days** and offers no
equivalent attribute — only a refresh resets the clock, which is why the build runs
every 3 hours.

OpenAI's published spec describes SFTP delivery only, but the Ads Manager UI also offers
Hosted URL and manual upload. Hosted URL is what this pipeline targets.

## Deployment

GitHub Actions builds every 3 hours and on any change to `config/` or `src/`
(`.github/workflows/feeds.yml`), then publishes `site/` to **GitHub Pages**. All four
channels read their feed from the resulting HTTPS URLs — no other vendor involved.

```
https://<user>.github.io/<repo>/openai_products_beneficials.tsv
https://<user>.github.io/<repo>/google_shopping.xml
https://<user>.github.io/<repo>/microsoft_shopping.xml
https://<user>.github.io/<repo>/pinterest_catalog.csv
```

`https://<user>.github.io/<repo>/` itself serves an index listing every feed, its size,
and the last build time — the fastest way to confirm a run actually worked.

Required secrets:

```
SHOPIFY_STORE       e.g. 8a2744-3d.myshopify.com
SHOPIFY_TOKEN       custom app token: read_products, read_inventory
MARGIN_BANDS_CSV    optional — the contents of a sku,band CSV
```

Margin data never enters the repo. `config/margin_bands.csv` is gitignored and written
at build time from `MARGIN_BANDS_CSV`, so the repo can be public (which is what makes
Pages free) without publishing your margins.

Three guards run before anything is served: `extract.py` refuses to write a catalog
under `MIN_EXPECTED_PRODUCTS`, the test suite must pass, and the workflow aborts if the
full feed drops below 300 offers or the beneficials feed below 150. A feed that quietly
loses a third of the catalog is worse than no feed, because channels delist whatever
disappears.

### Feed expiry — why the schedule matters

Two channels silently expire products that stop being refreshed:

| Channel | Expires after | Mitigation |
|---|---|---|
| OpenAI | **14 days** since last update | 3-hour build cadence |
| Microsoft | 30 days since last update | `expiration_date` stamped at +29 days |

Neither surfaces as an error until traffic has already stopped. The 3-hour schedule
means a build would have to fail ~112 times in a row before OpenAI expiry became a risk,
and Actions emails on workflow failure.

## Segments

A segment is a named subset of the catalog, published as its own feed:

```bash
python3 src/generate.py                          # all → *_products.tsv
python3 src/generate.py --segment beneficials    # → *_products_beneficials.tsv
```

| Segment | Contents | Offers |
|---|---|---|
| `all` | Everything eligible | 482 |
| `beneficials` | Live biological control organisms only | 238 |
| `merch` | Apparel and branded goods | — |

`beneficials` selects on the `custom.target_pests` metafield, which turns out to be an
exact signal: populated on every living control agent, empty on sticky traps and Physan.
Product type is a fallback for the few that predate the metafield.

## Editing the rules

**Add a title template** — `config/rules.yaml` → `titles.templates`. Templates
are tried in order; the first whose `requires` fields are all non-empty wins, so
put the most specific first.

**Exclude something** — `exclusions`. Each rule carries a `reason` that lands in
`out/<channel>_dropped.json`, so "why isn't this product in Google?" is always
answerable.

**Override one SKU** — add a row to `config/overrides.csv`. Any `Offer` field
name works as a column.

**Change a category** — `categories.rules`, ordered, first match wins.

## Policy notes for this catalog

Google has no live-animal advertising ban, but it does restrict *"the
transportation of live animals"* under Local legal requirements and safety
standards, and that classifier keys off the `Animals & Pet Supplies` branch of
the taxonomy. Every biological-control SKU is therefore mapped under
`Home & Garden > Household Supplies > Pest Control`, and a test asserts nothing
ever lands in the Animals branch.

Macrobial agents — predatory insects and mites, entomopathogenic nematodes — are
exempt from EPA pesticide registration; only microbial agents (Bt, *Beauveria*)
are registered pesticides. The validator flags affirmative use of "pesticide" /
"insecticide" / "live animals" in titles as an error and in descriptions as a
warning, while ignoring negated usage like "reduces pesticide use" or
"pesticide-free".
