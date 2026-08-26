#!/usr/bin/env python3
"""
FGMN Nursery feed pipeline — entrypoint.

  python3 src/generate.py                    # build every enabled channel
  python3 src/generate.py --channel google
  python3 src/generate.py --strict            # non-zero exit on any error finding

Reads raw/catalog.json (produced by src/extract.py against the Shopify Admin
API) and config/rules.yaml. Writes out/ plus a run report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

from channels import google, microsoft, openai_feed, pinterest  # noqa: E402
from transform import Transformer  # noqa: E402
from validate import summarize, validate_offers, validate_xml  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RENDERERS = {
    "google": (google.render, "google_shopping.xml", "xml"),
    "openai": (openai_feed.render, "openai_products.tsv", "tsv"),
    "microsoft": (microsoft.render, "microsoft_shopping.xml", "xml"),
    "pinterest": (pinterest.render, "pinterest_catalog.csv", "csv"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", help="limit to these channels")
    ap.add_argument("--catalog", default=os.path.join(ROOT, "raw/catalog.json"))
    ap.add_argument("--rules", default=os.path.join(ROOT, "config/rules.yaml"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--segment", default="all",
                    help="named subset from rules.yaml (e.g. beneficials)")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    rules = yaml.safe_load(open(args.rules, encoding="utf-8"))
    products = json.load(open(args.catalog, encoding="utf-8"))["products"]
    os.makedirs(args.out, exist_ok=True)

    wanted = args.channel or [c for c, v in rules["channels"].items() if v.get("enabled")]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_products": len(products),
        "segment": args.segment,
        "channels": {},
    }
    exit_code = 0

    for name in wanted:
        cfg = rules["channels"].get(name)
        if not cfg or name not in RENDERERS:
            print(f"  ! unknown or disabled channel: {name}")
            continue

        tx = Transformer(rules, root=ROOT)
        offers = tx.build(products, channel=name, segment=args.segment)
        render, filename, kind = RENDERERS[name]
        if args.segment != "all":
            stem, _, ext = filename.rpartition(".")
            filename = f"{stem}_{args.segment}.{ext}"
        text = render(offers, cfg, rules["store"])

        path = os.path.join(args.out, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

        findings = validate_offers(offers, cfg)
        if kind == "xml":
            findings += validate_xml(text)
        s = summarize(findings)
        if s["errors"]:
            exit_code = 1

        report["channels"][name] = {
            "file": filename,
            "bytes": len(text.encode()),
            "offers": len(offers),
            "dropped": len(tx.dropped),
            "validation": s,
            "title_rules_used": _count(o.trace.get("title") for o in offers),
            "availability": _count(o.availability for o in offers),
            "stock_posture": _count(o.stock_posture for o in offers),
        }

        suffix = "" if args.segment == "all" else f"_{args.segment}"
        with open(os.path.join(args.out, f"{name}{suffix}_findings.json"), "w") as fh:
            json.dump([f.__dict__ for f in findings], fh, indent=2)
        with open(os.path.join(args.out, f"{name}{suffix}_dropped.json"), "w") as fh:
            json.dump([d.__dict__ for d in tx.dropped], fh, indent=2)

        print(f"  {name:11s} {len(offers):4d} offers  {len(tx.dropped):4d} dropped  "
              f"{s['errors']:3d} errors  {s['warnings']:3d} warnings  -> {filename}")

    rname = "run_report.json" if args.segment == "all" else f"run_report_{args.segment}.json"
    with open(os.path.join(args.out, rname), "w") as fh:
        json.dump(report, fh, indent=2)

    return exit_code if args.strict else 0


def _count(vals) -> dict:
    from collections import Counter

    return dict(Counter(v for v in vals if v).most_common())


if __name__ == "__main__":
    sys.exit(main())
