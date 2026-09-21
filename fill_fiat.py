"""Fill Fiat catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Panda generations
    'fiat-panda-2':                 (2005, 2011),
    'fiat-panda-2-4x4':             (2005, 2011),
    'fiat-panda-2-commerciale':     (2005, 2011),
    'fiat-panda-3':                 (2011, 2026),
    'fiat-panda-3-4x4':             (2011, 2020),
    'fiat-panda-3-commerciale':     (2011, 2026),
    # Grande Punto / Punto 3
    'fiat-grande-punto':            (2005, 2018),
    'fiat-grande-punto-commerciale':(2005, 2018),
    'fiat-punto-3':                 (2011, 2018),
    'fiat-punto-3-societe':         (2011, 2018),
    'fiat-punto-2-societe':         (2005, 2010),
    # Stilo
    'fiat-stilo':                   (2005, 2008),
    'fiat-stilo-multiwagon':        (2005, 2008),
    'fiat-stilo-abarth':            (2005, 2007),
    'fiat-stilo-commerciale':       (2005, 2008),
    # Bravo 2
    'fiat-bravo-2':                 (2007, 2014),
    'fiat-bravo-2-societe':         (2007, 2014),
    # Doblo generations
    'fiat-doblo':                   (2005, 2015),
    'fiat-doblo-2':                 (2015, 2022),
    # Ducato generations
    'fiat-ducato-3':                (2005, 2014),
    'fiat-ducato-4':                (2014, 2026),
    # Fiorino
    'fiat-fiorino-4-fourgon':       (2007, 2020),
    # Qubo
    'fiat-qubo':                    (2008, 2020),
    # Scudo 3
    'fiat-scudo-3':                 (2021, 2026),
    # gaps
    'fiat-punto':                   (2007, 2011),
    'fiat-doblo-cargo':             (2016, 2018),
    'fiat-ducato-3-minibus':        (2014, 2015),
    'fiat-fiorino-2-combi':         (2011, 2011),
    'fiat-scudo-minibus':           (2010, 2010),
}

SLUG_NAME = {
    'fiat-panda-2': 'Panda 2',
    'fiat-panda-2-4x4': 'Panda 2 4X4',
    'fiat-panda-2-commerciale': 'Panda 2 Commerciale',
    'fiat-panda-3': 'Panda 3',
    'fiat-panda-3-4x4': 'Panda 3 4X4',
    'fiat-panda-3-commerciale': 'Panda 3 Commerciale',
    'fiat-grande-punto': 'Grande Punto',
    'fiat-grande-punto-commerciale': 'Grande Punto Commerciale',
    'fiat-punto-3': 'Punto 3',
    'fiat-punto-3-societe': 'Punto 3 Societe',
    'fiat-punto-2-societe': 'Punto 2 Societe',
    'fiat-stilo': 'Stilo',
    'fiat-stilo-multiwagon': 'Stilo Multiwagon',
    'fiat-stilo-abarth': 'Stilo Abarth',
    'fiat-stilo-commerciale': 'Stilo Commerciale',
    'fiat-bravo-2': 'Bravo 2',
    'fiat-bravo-2-societe': 'Bravo 2 Societe',
    'fiat-doblo': 'Doblo',
    'fiat-doblo-2': 'Doblo 2',
    'fiat-ducato-3': 'Ducato 3',
    'fiat-ducato-4': 'Ducato 4',
    'fiat-fiorino-4-fourgon': 'Fiorino 4 Fourgon',
    'fiat-qubo': 'Qubo',
    'fiat-scudo-3': 'Scudo 3',
    'fiat-punto': 'Punto',
    'fiat-doblo-cargo': 'Doblo Cargo',
    'fiat-ducato-3-minibus': 'Ducato 3 Minibus',
    'fiat-fiorino-2-combi': 'Fiorino 2 Combi',
    'fiat-scudo-minibus': 'Scudo Minibus',
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

BRAND = 'FIAT'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('fiat-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
