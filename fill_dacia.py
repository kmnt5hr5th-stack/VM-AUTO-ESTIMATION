"""Fill Dacia catalog"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence', 'diesel', 'hybride', 'electrique', 'gpl']

SLUGS = {
    'dacia-sandero':          (2008, 2012),
    'dacia-sandero-2':        (2012, 2020),
    'dacia-sandero-3':        (2021, 2026),
    'dacia-sandero-stepway':  (2009, 2012),
    'dacia-sandero-stepway-2':(2013, 2020),
    'dacia-sandero-stepway-3':(2021, 2026),
    'dacia-logan':            (2005, 2012),
    'dacia-logan-2':          (2012, 2020),
    'dacia-logan-3':          (2021, 2026),
    'dacia-logan-2-societe':  (2013, 2020),
    'dacia-duster':           (2010, 2017),
    'dacia-duster-2':         (2018, 2026),
    'dacia-dokker':           (2012, 2021),
    'dacia-dokker-societe':   (2012, 2021),
    'dacia-lodgy':            (2012, 2022),
    'dacia-lodgy-societe':    (2012, 2022),
    'dacia-spring':           (2021, 2026),
    'dacia-jogger':           (2022, 2026),
    'dacia-jogger-societe':   (2022, 2026),
}

SLUG_NAME = {
    'dacia-sandero':           'Sandero',
    'dacia-sandero-2':         'Sandero',
    'dacia-sandero-3':         'Sandero',
    'dacia-sandero-stepway':   'Sandero Stepway',
    'dacia-sandero-stepway-2': 'Sandero Stepway',
    'dacia-sandero-stepway-3': 'Sandero Stepway',
    'dacia-logan':             'Logan',
    'dacia-logan-2':           'Logan',
    'dacia-logan-3':           'Logan',
    'dacia-logan-2-societe':   'Logan Societe',
    'dacia-duster':            'Duster',
    'dacia-duster-2':          'Duster',
    'dacia-dokker':            'Dokker',
    'dacia-dokker-societe':    'Dokker Societe',
    'dacia-lodgy':             'Lodgy',
    'dacia-lodgy-societe':     'Lodgy Societe',
    'dacia-spring':            'Spring',
    'dacia-jogger':            'Jogger',
    'dacia-jogger-societe':    'Jogger Societe',
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

BRAND = 'DACIA'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('dacia-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
