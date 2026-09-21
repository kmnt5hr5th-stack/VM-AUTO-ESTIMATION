"""Fill Mitsubishi catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Colt
    'mitsubishi-colt-7':                (2005, 2012),
    'mitsubishi-colt-czc':              (2006, 2012),
    'mitsubishi-colt-societe':          (2005, 2012),
    'mitsubishi-colt-8':                (2023, 2026),
    # ASX
    'mitsubishi-asx-2':                 (2022, 2026),
    # Outlander
    'mitsubishi-outlander-2':           (2006, 2012),
    'mitsubishi-outlander-3':           (2012, 2021),
    'mitsubishi-outlander-4':           (2021, 2026),
    # Lancer
    'mitsubishi-lancer':                (2007, 2017),
    'mitsubishi-lancer-evolution':      (2005, 2015),
    # L200
    'mitsubishi-l200-4':                (2005, 2015),
    'mitsubishi-l200-6':                (2019, 2026),
    # Pajero
    'mitsubishi-pajero-3':              (2005, 2020),
    'mitsubishi-pajero-societe':        (2005, 2020),
    # Eclipse Cross 2
    'mitsubishi-eclipse-cross-2':       (2021, 2026),
    # Space Star
    'mitsubishi-space-star':            (2005, 2012),
    'mitsubishi-space-star-2':          (2012, 2026),
    'mitsubishi-space-star-societe':    (2012, 2020),
    # Grandis
    'mitsubishi-grandis':               (2005, 2011),
    # i-MiEV
    'mitsubishi-i-miev':                (2010, 2018),
    # gap Grandis
    'mitsubishi-grandis-2':             (2005, 2012),
}

SLUG_NAME = {
    'mitsubishi-colt-7': 'Colt 7',
    'mitsubishi-colt-czc': 'Colt Czc',
    'mitsubishi-colt-societe': 'Colt Societe',
    'mitsubishi-colt-8': 'Colt 8',
    'mitsubishi-asx-2': 'Asx 2',
    'mitsubishi-outlander-2': 'Outlander 2',
    'mitsubishi-outlander-3': 'Outlander 3',
    'mitsubishi-outlander-4': 'Outlander 4',
    'mitsubishi-lancer': 'Lancer',
    'mitsubishi-lancer-evolution': 'Lancer Evolution',
    'mitsubishi-l200-4': 'L200 4',
    'mitsubishi-l200-6': 'L200 6',
    'mitsubishi-pajero-3': 'Pajero 3',
    'mitsubishi-pajero-societe': 'Pajero Societe',
    'mitsubishi-eclipse-cross-2': 'Eclipse Cross 2',
    'mitsubishi-space-star': 'Space Star',
    'mitsubishi-space-star-2': 'Space Star 2',
    'mitsubishi-space-star-societe': 'Space Star Societe',
    'mitsubishi-grandis': 'Grandis',
    'mitsubishi-i-miev': 'I Miev',
    'mitsubishi-grandis-2': 'Grandis 2',
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

OUTPUT = Path(__file__).parent / 'caradisiac_catalog.json'
with open(OUTPUT, encoding='utf-8') as f:
    catalog = json.load(f)

def has_data(v):
    return isinstance(v, dict) and any(v.get(k) for k in FUEL_KEYS)

BRAND = 'MITSUBISHI'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('mitsubishi-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
