"""Fill Renault catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Laguna
    'renault-laguna-2':                     (2005, 2007),
    'renault-laguna-2-estate':              (2005, 2007),
    'renault-laguna-3':                     (2007, 2015),
    'renault-laguna-3-estate':              (2007, 2015),
    'renault-laguna-3-coupe':               (2008, 2015),
    'renault-laguna-3-societe':             (2007, 2015),
    'renault-laguna-3-estate-societe':      (2007, 2015),
    # Scenic
    'renault-scenic-2':                     (2005, 2009),
    'renault-scenic-3':                     (2009, 2016),
    'renault-scenic-3-societe':             (2009, 2016),
    'renault-scenic-4':                     (2016, 2023),
    'renault-scenic-5':                     (2022, 2026),
    # Grand Scenic
    'renault-grand-scenic-2':               (2005, 2009),
    'renault-grand-scenic-3':               (2009, 2016),
    'renault-grand-scenic-3-societe':       (2009, 2016),
    'renault-grand-scenic-4':               (2016, 2023),
    # Captur 2
    'renault-captur-2':                     (2019, 2026),
    # Twingo
    'renault-twingo':                       (2005, 2007),
    'renault-twingo-2-societe':             (2007, 2014),
    'renault-twingo-3':                     (2014, 2022),
    'renault-twingo-3-gt':                  (2017, 2021),
    'renault-twingo-4':                     (2021, 2026),
    # Zoe
    'renault-zoe':                          (2012, 2024),
    'renault-zoe-societe':                  (2012, 2024),
    # Espace
    'renault-espace-4':                     (2005, 2015),
    'renault-espace-4-societe':             (2005, 2015),
    'renault-espace-5':                     (2014, 2023),
    'renault-espace-6':                     (2023, 2026),
    # Modus
    'renault-modus':                        (2005, 2013),
    'renault-modus-societe':                (2005, 2013),
    # Vel Satis
    'renault-vel-satis':                    (2005, 2009),
    # Koleos gen 1
    'renault-koleos':                       (2008, 2015),
    # Kangoo
    'renault-kangoo-3':                     (2021, 2026),
    'renault-grand-kangoo-3':               (2021, 2026),
    # Fluence (more years)
    'renault-fluence':                      (2012, 2017),
    # Master 4
    'renault-master-4':                     (2022, 2026),
    # Trafic 3
    'renault-trafic-3':                     (2014, 2026),
    'renault-trafic-3-grand-spacenomad':    (2020, 2026),
    'renault-trafic-3-spacenomad':          (2019, 2026),
    'renault-trafic-2-passenger':           (2005, 2014),
    # gaps in existing
    'renault-koleos-2':                     (2016, 2016),
    'renault-trafic-3-plancher-cabine':     (2017, 2022),
    'renault-trafic-combi':                 (2008, 2010),
    'renault-master-3-minibus':             (2014, 2014),
    # Talisman Estate
    'renault-talisman-estate':              (2016, 2022),
    # Symbol
    'renault-symbol':                       (2005, 2012),
    # Megane missing variants
    'renault-megane-3-estate-societe':      (2009, 2016),
    'renault-megane-4-societe':             (2015, 2024),
    'renault-megane-4-sedan':               (2016, 2020),
}

SLUG_NAME = {
    'renault-laguna-2': 'Laguna 2',
    'renault-laguna-2-estate': 'Laguna 2 Estate',
    'renault-laguna-3': 'Laguna 3',
    'renault-laguna-3-estate': 'Laguna 3 Estate',
    'renault-laguna-3-coupe': 'Laguna 3 Coupe',
    'renault-laguna-3-societe': 'Laguna 3 Societe',
    'renault-laguna-3-estate-societe': 'Laguna 3 Estate Societe',
    'renault-scenic-2': 'Scenic 2',
    'renault-scenic-3': 'Scenic 3',
    'renault-scenic-3-societe': 'Scenic 3 Societe',
    'renault-scenic-4': 'Scenic 4',
    'renault-scenic-5': 'Scenic 5',
    'renault-grand-scenic-2': 'Grand Scenic 2',
    'renault-grand-scenic-3': 'Grand Scenic 3',
    'renault-grand-scenic-3-societe': 'Grand Scenic 3 Societe',
    'renault-grand-scenic-4': 'Grand Scenic 4',
    'renault-captur-2': 'Captur 2',
    'renault-twingo': 'Twingo',
    'renault-twingo-2-societe': 'Twingo 2 Societe',
    'renault-twingo-3': 'Twingo 3',
    'renault-twingo-3-gt': 'Twingo 3 Gt',
    'renault-twingo-4': 'Twingo 4',
    'renault-zoe': 'Zoe',
    'renault-zoe-societe': 'Zoe Societe',
    'renault-espace-4': 'Espace 4',
    'renault-espace-4-societe': 'Espace 4 Societe',
    'renault-espace-5': 'Espace 5',
    'renault-espace-6': 'Espace 6',
    'renault-modus': 'Modus',
    'renault-modus-societe': 'Modus Societe',
    'renault-vel-satis': 'Vel Satis',
    'renault-koleos': 'Koleos',
    'renault-kangoo-3': 'Kangoo 3',
    'renault-grand-kangoo-3': 'Grand Kangoo 3',
    'renault-fluence': 'Fluence',
    'renault-master-4': 'Master 4',
    'renault-trafic-3': 'Trafic 3',
    'renault-trafic-3-grand-spacenomad': 'Trafic 3 Grand Spacenomad',
    'renault-trafic-3-spacenomad': 'Trafic 3 Spacenomad',
    'renault-trafic-2-passenger': 'Trafic 2 Passenger',
    'renault-koleos-2': 'Koleos',
    'renault-trafic-3-plancher-cabine': 'Trafic 3 Plancher Cabine',
    'renault-trafic-combi': 'Trafic Combi',
    'renault-master-3-minibus': 'Master 3 Minibus',
    'renault-talisman-estate': 'Talisman Estate',
    'renault-symbol': 'Symbol',
    'renault-megane-3-estate-societe': 'Megane 3 Estate Societe',
    'renault-megane-4-societe': 'Megane 4 Societe',
    'renault-megane-4-sedan': 'Megane 4 Sedan',
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

BRAND = 'RENAULT'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('renault-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
