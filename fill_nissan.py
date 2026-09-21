"""Fill Nissan catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Micra generations
    'nissan-micra-3':                    (2003, 2010),
    'nissan-micra-4':                    (2010, 2017),
    'nissan-micra-5':                    (2017, 2022),
    # Juke 2
    'nissan-juke-2':                     (2019, 2026),
    # Qashqai 2 & 3
    'nissan-qashqai-2':                  (2017, 2021),
    'nissan-qashqai-3':                  (2021, 2026),
    # Leaf gen1
    'nissan-leaf':                       (2011, 2017),
    # Note generations
    'nissan-note':                       (2006, 2013),
    'nissan-note-2':                     (2013, 2021),
    # 350Z / 370Z
    'nissan-350z':                       (2005, 2010),
    'nissan-370z':                       (2009, 2021),
    # Pathfinder
    'nissan-pathfinder-2':               (2005, 2014),
    'nissan-pathfinder-3':               (2014, 2022),
    # Navara
    'nissan-navara':                     (2005, 2015),
    'nissan-navara-2':                   (2015, 2022),
    # Primera gen3
    'nissan-primera-3':                  (2005, 2008),
    # Pixo
    'nissan-pixo':                       (2009, 2014),
    # Pulsar
    'nissan-pulsar':                     (2014, 2018),
    # Cube
    'nissan-cube':                       (2009, 2014),
    # Gap fills
    # X-Trail 2 (2007-2013 generation)
    'nissan-x-trail-2':                  (2007, 2014),
    # Primastar Combi 2013-2023
    'nissan-primastar-combi-2':          (2013, 2024),
    # Primastar Minibus gaps (2006, 2009)
    'nissan-primastar-minibus':          (2005, 2011),
    # Interstar Minibus 2005-2006
    'nissan-interstar-minibus':          (2005, 2007),
    # X-Trail Entreprise 2008
    'nissan-x-trail-entreprise':         (2007, 2010),
}

SLUG_NAME = {
    'nissan-micra-3': 'Micra 3',
    'nissan-micra-4': 'Micra 4',
    'nissan-micra-5': 'Micra 5',
    'nissan-juke-2': 'Juke 2',
    'nissan-qashqai-2': 'Qashqai 2',
    'nissan-qashqai-3': 'Qashqai 3',
    'nissan-leaf': 'Leaf',
    'nissan-note': 'Note',
    'nissan-note-2': 'Note 2',
    'nissan-350z': '350z',
    'nissan-370z': '370z',
    'nissan-pathfinder-2': 'Pathfinder 2',
    'nissan-pathfinder-3': 'Pathfinder 3',
    'nissan-navara': 'Navara',
    'nissan-navara-2': 'Navara 2',
    'nissan-primera-3': 'Primera 3',
    'nissan-pixo': 'Pixo',
    'nissan-pulsar': 'Pulsar',
    'nissan-cube': 'Cube',
    'nissan-x-trail-2': 'X Trail 2',
    'nissan-primastar-combi-2': 'Primastar Combi 2',
    'nissan-primastar-minibus': 'Primastar Minibus',
    'nissan-interstar-minibus': 'Interstar Minibus',
    'nissan-x-trail-entreprise': 'X Trail Entreprise',
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

BRAND = 'NISSAN'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('nissan-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
