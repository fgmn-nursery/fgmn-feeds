import json, re, collections
C=json.load(open('raw/catalog.json'))['products']
def txt(h): return re.sub(r'<[^>]+>','',h or '').strip()
rows=[]
issues=collections.Counter()
for p in C:
    vs=p['variants']['nodes']
    mf={m['key']:m['value'] for m in p['metafields']['nodes']}
    for v in vs:
        rows.append((p,v,mf))
print("="*70); print("CATALOG SHAPE"); print("="*70)
print("products:",len(C),"variants:",len(rows))
print("\nstatus:",dict(collections.Counter(p['status'] for p in C)))
print("vendor:",dict(collections.Counter(p['vendor'] for p in C).most_common(12)))
print("productType:",dict(collections.Counter(p['productType'] for p in C).most_common(20)))
print("shopify category:",dict(collections.Counter((p['category'] or {}).get('fullName','NONE') for p in C).most_common(20)))
print("\n"+"="*70); print("FEED BLOCKERS"); print("="*70)
def chk(name,pred,scope='v'):
    if scope=='v': bad=[(p,v) for p,v,m in rows if pred(p,v)]
    n=len(bad); print(f"{n:4d}/{len(rows)}  {name}")
    return bad
noimg=[p for p in C if not p['media']['nodes']]
print(f"{len(noimg):4d}/{len(C)}  products with NO image")
oneimg=[p for p in C if len(p['media']['nodes'])==1]
print(f"{len(oneimg):4d}/{len(C)}  products with only 1 image")
smallimg=[(p['title'],i['image']['width'],i['image']['height']) for p in C for i in p['media']['nodes'] if i.get('image') and (i['image']['width'] or 0)<500 or (i.get('image') and (i['image']['height'] or 0)<500)]
print(f"{len(smallimg):4d}       images under 500x500 (Google enforces 31 Jan 2027)")
nourl=[p for p in C if not p['onlineStoreUrl']]
print(f"{len(nourl):4d}/{len(C)}  products with NO onlineStoreUrl (unpublished)")
chk("variants missing barcode/GTIN", lambda p,v: not v['barcode'])
chk("variants missing SKU", lambda p,v: not v['sku'])
chk("variants zero/absent weight", lambda p,v: not ((v['inventoryItem']['measurement'] or {}).get('weight') or {}).get('value'))
chk("variants NEGATIVE inventory but availableForSale", lambda p,v: (v['inventoryQuantity'] or 0)<0 and v['availableForSale'])
chk("variants qty<=0 but availableForSale=true", lambda p,v: (v['inventoryQuantity'] or 0)<=0 and v['availableForSale'])
chk("variants compareAtPrice set (sale price opportunity)", lambda p,v: v['compareAtPrice'])
badcat=[p for p in C if not p.get('googleCat')]
print(f"{len(badcat):4d}/{len(C)}  products with NO google_product_category metafield")
gc=collections.Counter(p.get('googleCat') and json.dumps(p['googleCat']) or 'NONE' for p in C)
print("   google cat values:",dict(gc.most_common(15)))
shortdesc=[p for p in C if len(txt(p['descriptionHtml']))<200]
print(f"{len(shortdesc):4d}/{len(C)}  products with description under 200 chars")
longtitle=[p for p in C if len(p['title'])>150]
shorttitle=[p for p in C if len(p['title'])<30]
print(f"{len(longtitle):4d}/{len(C)}  titles over 150 chars (Google/OpenAI cap)")
print(f"{len(shorttitle):4d}/{len(C)}  titles under 30 chars (weak keyword coverage)")
print("\n"+"="*70); print("TITLE LENGTH DISTRIBUTION"); print("="*70)
L=sorted(len(p['title']) for p in C)
print("min",L[0],"p25",L[len(L)//4],"median",L[len(L)//2],"p75",L[3*len(L)//4],"max",L[-1])
print("\nshortest 15 titles:")
for p in sorted(C,key=lambda p:len(p['title']))[:15]:
    print(f"  {len(p['title']):3d}  {p['title']!r}  [{p['productType']}] status={p['status']}")
print("\n"+"="*70); print("PRICE RANGE"); print("="*70)
pr=sorted(float(v['price']) for p,v,m in rows)
print("min",pr[0],"median",pr[len(pr)//2],"max",pr[-1])
print("\n"+"="*70); print("CUSTOM METAFIELD KEYS (enrichment sources)"); print("="*70)
k=collections.Counter(m['key'] for p in C for m in p['metafields']['nodes'])
for key,n in k.most_common(30): print(f"  {n:4d}  custom.{key}")
print("\n"+"="*70); print("TAGS"); print("="*70)
t=collections.Counter(t for p in C for t in p['tags'])
print(len(t),"unique tags")
for tag,n in t.most_common(40): print(f"  {n:4d}  {tag}")
print("\n"+"="*70); print("OPTION NAMES"); print("="*70)
o=collections.Counter(so['name'] for p,v,m in rows for so in v['selectedOptions'])
print(dict(o))
print("\n"+"="*70); print("NON-FGMN VENDOR / LIKELY EXCLUSIONS"); print("="*70)
for p in C:
    if p['vendor']!='FGMN Nursery' or p['status']!='ACTIVE' or not p['onlineStoreUrl']:
        print(f"  [{p['status']:8s}] vendor={p['vendor']!r:20s} url={'Y' if p['onlineStoreUrl'] else 'N'}  {p['title']!r}")
