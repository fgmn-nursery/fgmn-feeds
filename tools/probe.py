import json,re,collections
C=json.load(open('raw/catalog.json'))['products']
def txt(h): return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',h or '')).strip()
print("== products whose variants use non-size axes ==")
for p in C:
    names={so['name'] for v in p['variants']['nodes'] for so in v['selectedOptions']}
    if names & {'Shipping Options','Make It A Gift Set?','Purchase Options','Select Your Foliage Predators','Select Your Substrate Security','Add In Support Against The Adults','Rove Beetles','Trap Type'}:
        print(f"  {p['status']:8s} {p['title'][:55]:55s} nvar={len(p['variants']['nodes']):3d} opts={sorted(names)}")
print("\n== sample custom metafield values (pest-control product) ==")
p=[x for x in C if x['handle']=='treat-fungus-gnats-steinernema-feltiae'][0]
for m in p['metafields']['nodes']:
    print(f"  {m['key']} ({m['type']}): {m['value'][:200]}")
print("\n== sample custom metafield values (rare plant product) ==")
pl=[x for x in C if x['productType']=='Philodendron' and x['metafields']['nodes']]
if pl:
    q=pl[0]; print("  TITLE:",q['title'],"| status",q['status'],"| price",[v['price'] for v in q['variants']['nodes']])
    for m in q['metafields']['nodes']: print(f"  {m['key']} ({m['type']}): {m['value'][:160]}")
    print("  DESC:",txt(q['descriptionHtml'])[:300])
print("\n== ACTIVE + published products by type ==")
act=[p for p in C if p['status']=='ACTIVE' and p['onlineStoreUrl']]
print(len(act),"feedable products,",sum(len(p['variants']['nodes']) for p in act),"variants")
print(dict(collections.Counter(p['productType'] for p in act)))
print("\n== title vs seo title (rewrite source) ==")
for p in act[:12]:
    print(f"  T: {p['title'][:60]}\n  S: {(p['seo']['title'] or 'NONE')[:80]}")
print("\n== brand check: how many say FGMN in title ==")
print(sum(1 for p in act if 'fgmn' in p['title'].lower()),"of",len(act))
print("\n== target_pests values across catalog ==")
tp=collections.Counter()
for p in C:
    for m in p['metafields']['nodes']:
        if m['key']=='target_pests':
            try: tp.update(json.loads(m['value']))
            except: pass
print(dict(tp))
