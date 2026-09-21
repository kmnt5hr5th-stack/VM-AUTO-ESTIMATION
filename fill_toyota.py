"""Fill Toyota catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Yaris
    'toyota-yaris':                         (2005, 2011),
    'toyota-yaris-ts':                      (2005, 2011),
    'toyota-yaris-2':                       (2011, 2014),
    'toyota-yaris-2-ts':                    (2006, 2011),
    'toyota-yaris-3':                       (2011, 2020),
    'toyota-yaris-3-grmn':                  (2017, 2020),
    'toyota-yaris-4':                       (2020, 2026),
    # Aygo
    'toyota-aygo':                          (2005, 2014),
    'toyota-aygo-2':                        (2014, 2022),
    # Auris
    'toyota-auris-2':                       (2012, 2018),
    'toyota-auris-2-business':              (2013, 2018),
    'toyota-auris-3':                       (2018, 2022),
    # Corolla
    'toyota-corolla-9':                     (2005, 2007),
    'toyota-corolla-9-break':               (2005, 2007),
    'toyota-corolla-12':                    (2018, 2026),
    # C-HR
    'toyota-c-hr':                          (2016, 2023),
    'toyota-c-hr-2':                        (2023, 2026),
    # Prius
    'toyota-prius-2':                       (2005, 2009),
    'toyota-prius-3':                       (2009, 2015),
    'toyota-prius-3-rechargeable':          (2012, 2015),
    'toyota-prius-4':                       (2015, 2023),
    'toyota-prius-5':                       (2023, 2026),
    # Land Cruiser
    'toyota-land-cruiser-serie-120':        (2005, 2009),
    'toyota-land-cruiser-serie-150':        (2009, 2024),
    'toyota-land-cruiser-serie-250':        (2024, 2026),
    'toyota-land-cruiser-utilitaire':       (2005, 2015),
    # Hilux
    'toyota-hilux':                         (2005, 2011),
    'toyota-hilux-2':                       (2005, 2015),
    'toyota-hilux-3':                       (2015, 2020),
    'toyota-hilux-4':                       (2020, 2026),
    # Avensis
    'toyota-avensis-2':                     (2005, 2008),
    'toyota-avensis-2-break':               (2005, 2008),
    'toyota-avensis-3':                     (2008, 2018),
    'toyota-avensis-3-break':               (2008, 2018),
    'toyota-avensis-4':                     (2018, 2026),
    # GT86
    'toyota-gt86':                          (2012, 2021),
    # Urban Cruiser
    'toyota-urban-cruiser-2':               (2013, 2020),
    # gaps
    'toyota-urban-cruiser':                 (2010, 2010),
    'toyota-yaris-2-ts':                    (2007, 2009),
}

SLUG_NAME = {
    'toyota-yaris': 'Yaris',
    'toyota-yaris-ts': 'Yaris Ts',
    'toyota-yaris-2': 'Yaris 2',
    'toyota-yaris-2-ts': 'Yaris 2 Ts',
    'toyota-yaris-3': 'Yaris 3',
    'toyota-yaris-3-grmn': 'Yaris 3 Grmn',
    'toyota-yaris-4': 'Yaris 4',
    'toyota-aygo': 'Aygo',
    'toyota-aygo-2': 'Aygo 2',
    'toyota-auris-2': 'Auris 2',
    'toyota-auris-2-business': 'Auris 2 Business',
    'toyota-auris-3': 'Auris 3',
    'toyota-corolla-9': 'Corolla 9',
    'toyota-corolla-9-break': 'Corolla 9 Break',
    'toyota-corolla-12': 'Corolla 12',
    'toyota-c-hr': 'C Hr',
    'toyota-c-hr-2': 'C Hr 2',
    'toyota-prius-2': 'Prius 2',
    'toyota-prius-3': 'Prius 3',
    'toyota-prius-3-rechargeable': 'Prius 3 Rechargeable',
    'toyota-prius-4': 'Prius 4',
    'toyota-prius-5': 'Prius 5',
    'toyota-land-cruiser-serie-120': 'Land Cruiser Serie 120',
    'toyota-land-cruiser-serie-150': 'Land Cruiser Serie 150',
    'toyota-land-cruiser-serie-250': 'Land Cruiser Serie 250',
    'toyota-land-cruiser-utilitaire': 'Land Cruiser Utilitaire',
    'toyota-hilux': 'Hilux',
    'toyota-hilux-2': 'Hilux 2',
    'toyota-hilux-3': 'Hilux 3',
    'toyota-hilux-4': 'Hilux 4',
    'toyota-avensis-2': 'Avensis 2',
    'toyota-avensis-2-break': 'Avensis 2 Break',
    'toyota-avensis-3': 'Avensis 3',
    'toyota-avensis-3-break': 'Avensis 3 Break',
    'toyota-avensis-4': 'Avensis 4',
    'toyota-gt86': 'Gt86',
    'toyota-urban-cruiser-2': 'Urban Cruiser 2',
    'toyota-urban-cruiser': 'Urban Cruiser',
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

BRAND = 'TOYOTA'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('toyota-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
