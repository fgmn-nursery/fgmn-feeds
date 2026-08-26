#!/usr/bin/env python3
"""
Assemble the GitHub Pages site from out/.

The feed files are the point; the index page exists so a human can confirm at a
glance that the last run worked, and so the URLs to paste into each channel are
somewhere findable instead of in a chat log.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
SITE = os.path.join(ROOT, "site")

FEEDS = [
    ("google_shopping.xml", "Google Merchant Center", "Full catalog"),
    ("openai_products.tsv", "OpenAI / ChatGPT", "Full catalog"),
    ("microsoft_shopping.xml", "Microsoft / Bing", "Full catalog"),
    ("pinterest_catalog.csv", "Pinterest", "Full catalog"),
    ("google_shopping_beneficials.xml", "Google Merchant Center", "Beneficials only"),
    ("openai_products_beneficials.tsv", "OpenAI / ChatGPT", "Beneficials only"),
    ("microsoft_shopping_beneficials.xml", "Microsoft / Bing", "Beneficials only"),
    ("pinterest_catalog_beneficials.csv", "Pinterest", "Beneficials only"),
]


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


def main() -> int:
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(SITE)

    rows = []
    for filename, channel, segment in FEEDS:
        src = os.path.join(OUT, filename)
        if not os.path.exists(src):
            continue
        shutil.copy2(src, os.path.join(SITE, filename))
        size = os.path.getsize(src)
        rows.append((filename, channel, segment, size))

    # The run reports make failures diagnosable without opening the Actions log.
    for report in ("run_report.json", "run_report_beneficials.json"):
        p = os.path.join(OUT, report)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(SITE, report))

    counts = {}
    for report in ("run_report.json", "run_report_beneficials.json"):
        p = os.path.join(OUT, report)
        if os.path.exists(p):
            r = json.load(open(p))
            for ch, c in r["channels"].items():
                counts[(ch, r["segment"])] = c["offers"]

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tr = "\n".join(
        f"      <tr><td><a href='{f}'>{f}</a></td><td>{c}</td><td>{s}</td>"
        f"<td class='n'>{human_bytes(sz)}</td></tr>"
        for f, c, s, sz in rows
    )
    total = sum(counts.get(("google", seg), 0) for seg in ("all", "beneficials"))

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FGMN Nursery product feeds</title>
<style>
 :root{{color-scheme:light dark}}
 body{{font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
      max-width:820px;margin:48px auto;padding:0 24px}}
 h1{{font-size:1.3rem;margin:0 0 4px}}
 p.sub{{opacity:.65;margin:0 0 28px}}
 table{{border-collapse:collapse;width:100%;font-size:.86rem}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #8884}}
 th{{font-weight:600;opacity:.6;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums}}
 a{{color:inherit}}
 footer{{margin-top:28px;opacity:.55;font-size:.78rem}}
</style></head><body>
 <h1>FGMN Nursery — product feeds</h1>
 <p class="sub">Generated from Shopify. Do not edit by hand; change
 <code>config/rules.yaml</code> instead.</p>
 <table>
  <thead><tr><th>File</th><th>Channel</th><th>Segment</th><th class="n">Size</th></tr></thead>
  <tbody>
{tr}
  </tbody>
 </table>
 <footer>Last build {stamp} · {total} total offers ·
 <a href="run_report.json">run_report.json</a> ·
 <a href="run_report_beneficials.json">run_report_beneficials.json</a></footer>
</body></html>
"""
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    # GitHub Pages runs Jekyll by default, which ignores files it doesn't
    # recognise. .nojekyll turns that off so every feed file is served as-is.
    open(os.path.join(SITE, ".nojekyll"), "w").close()

    print(f"  site/ assembled — {len(rows)} feed files + index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
