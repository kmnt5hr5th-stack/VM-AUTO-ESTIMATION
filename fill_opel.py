"""Fill Opel catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Corsa E (5th gen, 2014-2019) — base missing
    'opel-corsa-e':                             (2014, 2019),
    # Insignia gen 1
    'opel-insignia':                            (2008, 2017),
    'opel-insignia-sports-tourer':              (2008, 2017),
    'opel-insignia-opc':                        (2009, 2017),
    # Adam
    'opel-adam':                                (2012, 2019),
    # Meriva
    'opel-meriva':                              (2005, 2010),
    'opel-meriva-b-2e-generation':              (2010, 2017),
    # Zafira Tourer (C gen, 2011-2019)
    'opel-zafira-tourer':                       (2011, 2019),
    # Vivaro gen 2 & 3
    'opel-vivaro-2':                            (2014, 2019),
    'opel-vivaro-3':                            (2019, 2026),
    'opel-vivaro-2-combi':                      (2014, 2019),
    'opel-vivaro-3-combi':                      (2019, 2026),
    # Gap fills — existing models
    'opel-astra-h-gtc-3e-generation':           (2007, 2007),
    'opel-astra-h-twintop-3e-generation':       (2009, 2009),
    'opel-astra-j-4e-generation':               (2014, 2014),
    'opel-astra-j-4e-generation-affaire':       (2007, 2009),
    'opel-astra-j-gtc-4e-generation':           (2015, 2015),
    'opel-combo':                               (2007, 2007),
    'opel-combo-tour':                          (2018, 2024),
    'opel-corsa-5-affaires':                    (2020, 2023),
    'opel-corsa-d-4e-generation-opc':           (2008, 2009),
    'opel-vivaro-combi':                        (2013, 2024),
    'opel-vivaro-tour':                         (2005, 2010),
}

SLUG_NAME = {
    'opel-corsa-e': 'Corsa E',
    'opel-insignia': 'Insignia',
    'opel-insignia-sports-tourer': 'Insignia Sports Tourer',
    'opel-insignia-opc': 'Insignia Opc',
    'opel-adam': 'Adam',
    'opel-meriva': 'Meriva',
    'opel-meriva-b-2e-generation': 'Meriva B 2E Generation',
    'opel-zafira-tourer': 'Zafira Tourer',
    'opel-vivaro-2': 'Vivaro 2',
    'opel-vivaro-3': 'Vivaro 3',
    'opel-vivaro-2-combi': 'Vivaro 2 Combi',
    'opel-vivaro-3-combi': 'Vivaro 3 Combi',
    # Gap fills — exact catalog names
    'opel-astra-h-gtc-3e-generation': 'Astra H Gtc 3E Generation',
    'opel-astra-h-twintop-3e-generation': 'Astra H Twintop 3E Generation',
    'opel-astra-j-4e-generation': 'Astra J 4E Generation',
    'opel-astra-j-4e-generation-affaire': 'Astra J 4E Generation Affaire',
    'opel-astra-j-gtc-4e-generation': 'Astra J Gtc 4E Generation',
    'opel-combo': 'Combo',
    'opel-combo-tour': 'Combo Tour',
    'opel-corsa-5-affaires': 'Corsa 5 Affaires',
    'opel-corsa-d-4e-generation-opc': 'Corsa D 4E Generation Opc',
    'opel-vivaro-combi': 'Vivaro Combi',
    'opel-vivaro-tour': 'Vivaro Tour',
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

BRAND = 'OPEL'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('opel-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
