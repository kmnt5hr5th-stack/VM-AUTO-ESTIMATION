"""Fill Peugeot catalog — only missing models/variants"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # 107
    'peugeot-107':                          (2005, 2014),
    'peugeot-107-societe':                  (2005, 2014),
    # 206
    'peugeot-206':                          (2005, 2009),
    'peugeot-206-cc':                       (2005, 2009),
    'peugeot-206-sw':                       (2005, 2009),
    'peugeot-206-rc':                       (2005, 2007),
    'peugeot-206-affaires':                 (2005, 2009),
    # 207
    'peugeot-207':                          (2006, 2015),
    'peugeot-207-sw':                       (2007, 2015),
    'peugeot-207-rc':                       (2007, 2014),
    'peugeot-207-cc':                       (2007, 2015),
    'peugeot-207-affaire':                  (2007, 2015),
    # 307
    'peugeot-307':                          (2005, 2009),
    'peugeot-307-cc':                       (2005, 2009),
    'peugeot-307-break':                    (2005, 2009),
    'peugeot-307-sw':                       (2005, 2009),
    'peugeot-307-affaire':                  (2005, 2009),
    # 308 missing years + variants
    'peugeot-308':                          (2008, 2009),
    'peugeot-308-sw':                       (2008, 2013),
    'peugeot-308-gti':                      (2010, 2013),
    'peugeot-308-affaire':                  (2008, 2013),
    'peugeot-308-2e-generation-sw':         (2013, 2021),
    'peugeot-308-2e-generation-gti':        (2015, 2017),
    'peugeot-308-3e-generation-sw':         (2021, 2026),
    'peugeot-e-308-3e-generation':          (2022, 2026),
    'peugeot-e-308-3e-generation-sw':       (2022, 2026),
    # 208 variants
    'peugeot-208-gti':                      (2013, 2019),
    'peugeot-208-affaire':                  (2012, 2019),
    'peugeot-208-2e-generation-affaire':    (2019, 2026),
    # 508 variants
    'peugeot-508-sw':                       (2011, 2018),
    'peugeot-508-2e-generation-sw':         (2019, 2026),
    'peugeot-508-2e-generation-pse':        (2021, 2026),
    'peugeot-508-2e-generation-sw-pse':     (2021, 2026),
    # RCZ
    'peugeot-rcz':                          (2010, 2015),
    # Partner 2 (Tepee)
    'peugeot-partner-2':                    (2008, 2018),
    # 1007, 4007, 4008
    'peugeot-1007':                         (2005, 2009),
    'peugeot-4007':                         (2006, 2012),
    'peugeot-4008':                         (2012, 2016),
    # Ion
    'peugeot-ion':                          (2011, 2015),
    # Expert 3 Combi (exists in catalog but no data)
    'peugeot-expert-3-combi':               (2016, 2026),
    'peugeot-expert-minibus':               (2005, 2007),
    # 3008 / 5008 business
    'peugeot-3008-business':                (2009, 2016),
    'peugeot-5008-business':                (2009, 2017),
    # Rifter gap 2023
    'peugeot-rifter':                       (2023, 2023),
}

SLUG_NAME = {
    'peugeot-107': '107',
    'peugeot-107-societe': '107 Societe',
    'peugeot-206': '206',
    'peugeot-206-cc': '206 Cc',
    'peugeot-206-sw': '206 Sw',
    'peugeot-206-rc': '206 Rc',
    'peugeot-206-affaires': '206 Affaires',
    'peugeot-207': '207',
    'peugeot-207-sw': '207 Sw',
    'peugeot-207-rc': '207 Rc',
    'peugeot-207-cc': '207 Cc',
    'peugeot-207-affaire': '207 Affaire',
    'peugeot-307': '307',
    'peugeot-307-cc': '307 Cc',
    'peugeot-307-break': '307 Break',
    'peugeot-307-sw': '307 Sw',
    'peugeot-307-affaire': '307 Affaire',
    'peugeot-308': '308',
    'peugeot-308-sw': '308 Sw',
    'peugeot-308-gti': '308 Gti',
    'peugeot-308-affaire': '308 Affaire',
    'peugeot-308-2e-generation-sw': '308 2E Generation Sw',
    'peugeot-308-2e-generation-gti': '308 2E Generation Gti',
    'peugeot-308-3e-generation-sw': '308 3E Generation Sw',
    'peugeot-e-308-3e-generation': 'E 308 3E Generation',
    'peugeot-e-308-3e-generation-sw': 'E 308 3E Generation Sw',
    'peugeot-208-gti': '208 Gti',
    'peugeot-208-affaire': '208 Affaire',
    'peugeot-208-2e-generation-affaire': '208 2E Generation Affaire',
    'peugeot-508-sw': '508 Sw',
    'peugeot-508-2e-generation-sw': '508 2E Generation Sw',
    'peugeot-508-2e-generation-pse': '508 2E Generation Pse',
    'peugeot-508-2e-generation-sw-pse': '508 2E Generation Sw Pse',
    'peugeot-rcz': 'Rcz',
    'peugeot-partner-2': 'Partner 2',
    'peugeot-1007': '1007',
    'peugeot-4007': '4007',
    'peugeot-4008': '4008',
    'peugeot-ion': 'Ion',
    'peugeot-expert-3-combi': 'Expert 3 Combi',
    'peugeot-expert-minibus': 'Expert Minibus',
    'peugeot-3008-business': '3008 Business',
    'peugeot-5008-business': '5008 Business',
    'peugeot-rifter': 'Rifter',
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

BRAND = 'PEUGEOT'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('peugeot-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
