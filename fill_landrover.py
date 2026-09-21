"""Fill Land Rover catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Defender generations
    'land-rover-defender-3':                    (2005, 2016),
    'land-rover-defender-4':                    (2019, 2026),
    # Discovery generations
    'land-rover-discovery-3':                   (2005, 2009),
    'land-rover-discovery-4':                   (2009, 2017),
    'land-rover-discovery-5':                   (2017, 2026),
    # Freelander
    'land-rover-freelander':                    (2005, 2006),
    'land-rover-freelander-2':                  (2006, 2014),
    # Range Rover generations
    'land-rover-range-rover-3':                 (2005, 2012),
    'land-rover-range-rover-4':                 (2012, 2021),
    'land-rover-range-rover-5':                 (2021, 2026),
    # Range Rover Sport
    'land-rover-range-rover-sport-2':           (2013, 2022),
    'land-rover-range-rover-sport-3':           (2022, 2026),
    # Range Rover Evoque variants
    'land-rover-range-rover-evoque-2':          (2019, 2026),
    'land-rover-range-rover-evoque-cabriolet':  (2016, 2019),
    'land-rover-range-rover-evoque-coupe':      (2011, 2018),
    # gaps in existing
    'land-rover-defender-3-utilitaire':         (2008, 2013),
    'land-rover-defender-3-utilitaire-pick-up': (2008, 2019),
}

SLUG_NAME = {
    'land-rover-defender-3': 'Defender 3',
    'land-rover-defender-4': 'Defender 4',
    'land-rover-discovery-3': 'Discovery 3',
    'land-rover-discovery-4': 'Discovery 4',
    'land-rover-discovery-5': 'Discovery 5',
    'land-rover-freelander': 'Freelander',
    'land-rover-freelander-2': 'Freelander 2',
    'land-rover-range-rover-3': 'Range Rover 3',
    'land-rover-range-rover-4': 'Range Rover 4',
    'land-rover-range-rover-5': 'Range Rover 5',
    'land-rover-range-rover-sport-2': 'Range Rover Sport 2',
    'land-rover-range-rover-sport-3': 'Range Rover Sport 3',
    'land-rover-range-rover-evoque-2': 'Range Rover Evoque 2',
    'land-rover-range-rover-evoque-cabriolet': 'Range Rover Evoque Cabriolet',
    'land-rover-range-rover-evoque-coupe': 'Range Rover Evoque Coupe',
    'land-rover-defender-3-utilitaire': 'Defender 3 Utilitaire',
    'land-rover-defender-3-utilitaire-pick-up': 'Defender 3 Utilitaire Pick Up',
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

BRAND = 'LAND ROVER'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('land-rover-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
