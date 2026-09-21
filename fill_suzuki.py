"""Fill Suzuki catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Swift generations
    'suzuki-swift-2':           (2005, 2011),
    'suzuki-swift-3':           (2010, 2017),
    'suzuki-swift-4':           (2017, 2023),
    'suzuki-swift-5':           (2023, 2026),
    # SX4
    'suzuki-sx4':               (2006, 2014),
    'suzuki-sx4-utilitaire':    (2006, 2014),
    # Grand Vitara
    'suzuki-grand-vitara':      (2005, 2012),
    'suzuki-grand-vitara-2':    (2005, 2015),
    'suzuki-grand-vitara-utilitaire': (2005, 2012),
    # Jimny
    'suzuki-jimny':             (2005, 2018),
    'suzuki-jimny-utilitaire':  (2005, 2018),
    'suzuki-jimny-2':           (2018, 2026),
    'suzuki-jimny-2-utilitaire':(2018, 2026),
    # Splash
    'suzuki-splash':            (2008, 2015),
    'suzuki-splash-utilitaire': (2008, 2015),
    # Alto
    'suzuki-alto':              (2009, 2015),
    'suzuki-alto-2':            (2014, 2020),
    'suzuki-alto-societe':      (2009, 2015),
    # Liana
    'suzuki-liana':             (2005, 2007),
    # Ignis generations
    'suzuki-ignis-2':           (2016, 2020),
    'suzuki-ignis-3':           (2020, 2026),
    'suzuki-ignis-utilitaire':  (2005, 2009),
    # Baleno
    'suzuki-baleno-2':          (2022, 2026),
    'suzuki-baleno-break':      (2005, 2009),
    # Vitara generations
    'suzuki-vitara-2':          (2015, 2026),
    'suzuki-vitara-4':          (2022, 2026),
    # S Cross
    'suzuki-s-cross':           (2013, 2022),
    'suzuki-s-cross-2':         (2021, 2026),
    # Across
    'suzuki-across-2':          (2023, 2026),
    # Kizashi
    'suzuki-kizashi':           (2010, 2015),
    # gaps in existing
    'suzuki-swift-3-sport':     (2012, 2012),
    'suzuki-swift-utilitaire':  (2008, 2008),
}

SLUG_NAME = {
    'suzuki-swift-2': 'Swift 2',
    'suzuki-swift-3': 'Swift 3',
    'suzuki-swift-4': 'Swift 4',
    'suzuki-swift-5': 'Swift 5',
    'suzuki-sx4': 'Sx4',
    'suzuki-sx4-utilitaire': 'Sx4 Utilitaire',
    'suzuki-grand-vitara': 'Grand Vitara',
    'suzuki-grand-vitara-2': 'Grand Vitara 2',
    'suzuki-grand-vitara-utilitaire': 'Grand Vitara Utilitaire',
    'suzuki-jimny': 'Jimny',
    'suzuki-jimny-utilitaire': 'Jimny Utilitaire',
    'suzuki-jimny-2': 'Jimny 2',
    'suzuki-jimny-2-utilitaire': 'Jimny 2 Utilitaire',
    'suzuki-splash': 'Splash',
    'suzuki-splash-utilitaire': 'Splash Utilitaire',
    'suzuki-alto': 'Alto',
    'suzuki-alto-2': 'Alto 2',
    'suzuki-alto-societe': 'Alto Societe',
    'suzuki-liana': 'Liana',
    'suzuki-ignis-2': 'Ignis 2',
    'suzuki-ignis-3': 'Ignis 3',
    'suzuki-ignis-utilitaire': 'Ignis Utilitaire',
    'suzuki-baleno-2': 'Baleno 2',
    'suzuki-baleno-break': 'Baleno Break',
    'suzuki-vitara-2': 'Vitara 2',
    'suzuki-vitara-4': 'Vitara 4',
    'suzuki-s-cross': 'S Cross',
    'suzuki-s-cross-2': 'S Cross 2',
    'suzuki-across-2': 'Across 2',
    'suzuki-kizashi': 'Kizashi',
    'suzuki-swift-3-sport': 'Swift 3 Sport',
    'suzuki-swift-utilitaire': 'Swift Utilitaire',
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

BRAND = 'SUZUKI'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('suzuki-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
