"""Fill Mazda catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Mazda 5 gen 1
    'mazda-5':                              (2005, 2010),
    # Mazda 5 gen 2 gap fill
    'mazda-5-2e-generation':               (2010, 2018),
    # CX-3
    'mazda-cx-3':                          (2015, 2022),
    # CX-5 gen 1
    'mazda-cx-5':                          (2012, 2017),
    # CX-7
    'mazda-cx-7':                          (2007, 2013),
    # CX-9
    'mazda-cx-9':                          (2007, 2016),
    # CX-30
    'mazda-cx-30':                         (2019, 2026),
    # CX-60
    'mazda-cx-60':                         (2022, 2026),
    # RX-8
    'mazda-rx-8':                          (2005, 2011),
    # BT-50
    'mazda-bt-50':                         (2007, 2015),
    'mazda-bt-50-2':                       (2020, 2026),
    # Gap fills for existing models
    'mazda-2-2e-generation':               (2005, 2006),
    'mazda-3-3e-generation':               (2013, 2021),
    'mazda-3-3e-generation-berline':       (2013, 2021),
    'mazda-3-4e-generation':               (2025, 2025),
    'mazda-6-3e-generation':               (2015, 2021),
    'mazda-6-3e-generation-fastwagon':     (2015, 2021),
    'mazda-6-fastwagon':                   (2006, 2006),
    'mazda-mx':                            (2020, 2025),
    'mazda-mx5-4e-generation':             (2023, 2023),
    'mazda-mx5-4e-generation-rf':          (2016, 2025),
}

SLUG_NAME = {
    'mazda-5': '5',
    'mazda-5-2e-generation': '5 2E Generation',
    'mazda-cx-3': 'Cx 3',
    'mazda-cx-5': 'Cx 5',
    'mazda-cx-7': 'Cx 7',
    'mazda-cx-9': 'Cx 9',
    'mazda-cx-30': 'Cx 30',
    'mazda-cx-60': 'Cx 60',
    'mazda-rx-8': 'Rx 8',
    'mazda-bt-50': 'Bt 50',
    'mazda-bt-50-2': 'Bt 50 2',
    # Gap fills — exact catalog names
    'mazda-2-2e-generation': '2 2E Generation',
    'mazda-3-3e-generation': '3 3E Generation',
    'mazda-3-3e-generation-berline': '3 3E Generation Berline',
    'mazda-3-4e-generation': '3 4E Generation',
    'mazda-6-3e-generation': '6 3E Generation',
    'mazda-6-3e-generation-fastwagon': '6 3E Generation Fastwagon',
    'mazda-6-fastwagon': '6 Fastwagon',
    'mazda-mx': 'Mx',
    'mazda-mx5-4e-generation': 'Mx5 4E Generation',
    'mazda-mx5-4e-generation-rf': 'Mx5 4E Generation Rf',
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

BRAND = 'MAZDA'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('mazda-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
