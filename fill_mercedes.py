"""Fill Mercedes catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Classe C 2 (W203) — missing base model
    'mercedes-classe-c-2':              (2005, 2007),
    'mercedes-classe-c-2-sw':           (2005, 2007),
    # Classe C 3 (W204) — missing base model 2007-2014
    'mercedes-classe-c-3':              (2007, 2014),
    'mercedes-classe-c-3-sw':           (2007, 2014),
    'mercedes-classe-c-3-coupe':        (2011, 2015),
    'mercedes-classe-c-3-cabriolet':    (2011, 2015),
    # Classe A 2 (W169) & A4 hatch
    'mercedes-classe-a-2':              (2005, 2012),
    'mercedes-classe-a-4':              (2018, 2026),
    # Classe S 6 (W221) — missing base model 2005-2013
    'mercedes-classe-s-6':              (2005, 2013),
    # Classe G 3 base (W463)
    'mercedes-classe-g-3':              (2005, 2018),
    # CLK 2
    'mercedes-clk-2':                   (2005, 2009),
    'mercedes-clk-2-cabriolet':         (2005, 2009),
    # SL 2/3
    'mercedes-sl-2':                    (2005, 2012),
    'mercedes-sl-3':                    (2012, 2014),
    # Gap fills for existing AMG/variants
    'mercedes-classe-c-3-amg':          (2009, 2012),
    'mercedes-classe-c-3-sw-amg':       (2009, 2012),
    'mercedes-classe-c-3-coupe-amg':    (2012, 2012),
    'mercedes-classe-e-4-amg':          (2012, 2012),
    'mercedes-classe-e-4-break-amg':    (2012, 2012),
    'mercedes-classe-s-6-amg':          (2008, 2011),
    'mercedes-sl-2-amg':                (2005, 2007),
    'mercedes-sl-4-amg':                (2020, 2021),
    'mercedes-cla-2-shooting-brake':    (2021, 2021),
    'mercedes-vito-minibus':            (2007, 2013),
}

SLUG_NAME = {
    'mercedes-classe-c-2': 'Classe C 2',
    'mercedes-classe-c-2-sw': 'Classe C 2 Sw',
    'mercedes-classe-c-3': 'Classe C 3',
    'mercedes-classe-c-3-sw': 'Classe C 3 Sw',
    'mercedes-classe-c-3-coupe': 'Classe C 3 Coupe',
    'mercedes-classe-c-3-cabriolet': 'Classe C 3 Cabriolet',
    'mercedes-classe-a-2': 'Classe A 2',
    'mercedes-classe-a-4': 'Classe A 4',
    'mercedes-classe-s-6': 'Classe S 6',
    'mercedes-classe-g-3': 'Classe G 3',
    'mercedes-clk-2': 'Clk 2',
    'mercedes-clk-2-cabriolet': 'Clk 2 Cabriolet',
    'mercedes-sl-2': 'Sl 2',
    'mercedes-sl-3': 'Sl 3',
    # Gap fills — must match exact catalog names
    'mercedes-classe-c-3-amg': 'Classe C 3 Amg',
    'mercedes-classe-c-3-sw-amg': 'Classe C 3 Sw Amg',
    'mercedes-classe-c-3-coupe-amg': 'Classe C 3 Coupe Amg',
    'mercedes-classe-e-4-amg': 'Classe E 4 Amg',
    'mercedes-classe-e-4-break-amg': 'Classe E 4 Break Amg',
    'mercedes-classe-s-6-amg': 'Classe S 6 Amg',
    'mercedes-sl-2-amg': 'Sl 2 Amg',
    'mercedes-sl-4-amg': 'Sl 4 Amg',
    'mercedes-cla-2-shooting-brake': 'Cla 2 Shooting Brake',
    'mercedes-vito-minibus': 'Vito Minibus',
}

def parse(html, slug):
    base = f'/fiches-techniques/modele--{slug}/'
    entry = {f: [] for f in FUEL_KEYS}
    seen = set()
    for m in re.finditer(rf'href="({re.escape(base)}[^"?]+)"[^>]*>([^<]{{3,}})<', html):
        href, text = m.group(1).rstrip('/'), m.group(2).strip()
        after = href[len(base):]
        parts = after.split('/')
        if len(parts) < 2 or not parts[1]:
            continue
        v = clean_version(text)
        if not v or len(v) < 3 or v in seen:
            continue
        seen.add(v)
        entry[detect_fuel(v)].append(v)
    return entry

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

async def fetch(session, slug, year, sem):
    async with sem:
        try:
            async with session.get(
                f'{BASE}/fiches-techniques/modele--{slug}/{year}/',
                headers=HEADERS, proxy=PROXY,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status != 200:
                    return slug, year, None
                return slug, year, parse(await r.text(), slug)
        except:
            return slug, year, None

async def main():
    tasks = [(sl, str(y)) for sl, (first, last) in SLUGS.items() for y in range(first, last+1)]
    print(f"Fetching {len(tasks)} URLs...")
    sem = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx, limit=10)) as s:
        return await asyncio.gather(*[fetch(s, sl, yr, sem) for sl, yr in tasks])

results = asyncio.run(main())

OUTPUT = Path(__file__).parent / 'vm_catalog.json'
with open(OUTPUT, encoding='utf-8') as f:
    catalog = json.load(f)

def has_data(v):
    return isinstance(v, dict) and any(v.get(k) for k in FUEL_KEYS)

BRAND = 'MERCEDES'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('mercedes-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
