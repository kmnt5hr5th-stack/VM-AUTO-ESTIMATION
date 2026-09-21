"""Fill Volkswagen catalog — missing models + gaps"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # Golf generations
    'volkswagen-golf-5':                (2005, 2008),
    'volkswagen-golf-5-gti':            (2005, 2008),
    'volkswagen-golf-5-break':          (2006, 2009),
    'volkswagen-golf-5-r32':            (2005, 2008),
    'volkswagen-golf-6':                (2008, 2012),
    'volkswagen-golf-6-sw':             (2008, 2013),
    'volkswagen-golf-6-gti':            (2009, 2013),
    'volkswagen-golf-6-cabriolet':      (2011, 2016),
    'volkswagen-golf-7':                (2012, 2020),
    'volkswagen-golf-7-sw':             (2012, 2020),
    'volkswagen-golf-7-gti':            (2012, 2020),
    'volkswagen-golf-7-cabriolet':      (2012, 2016),
    'volkswagen-golf-8':                (2019, 2026),
    'volkswagen-golf-8-sw':             (2020, 2026),
    'volkswagen-golf-8-gti':            (2020, 2026),
    # Polo generations
    'volkswagen-polo-4':                (2005, 2009),
    'volkswagen-polo-4-gti':            (2006, 2009),
    'volkswagen-polo-5':                (2009, 2017),
    'volkswagen-polo-5-gti':            (2010, 2017),
    'volkswagen-polo-6':                (2017, 2026),
    'volkswagen-polo-6-gti':            (2018, 2026),
    # Passat generations
    'volkswagen-passat-6':              (2005, 2010),
    'volkswagen-passat-6-sw':           (2005, 2010),
    'volkswagen-passat-7':              (2010, 2014),
    'volkswagen-passat-7-sw':           (2010, 2015),
    'volkswagen-passat-8':              (2014, 2023),
    'volkswagen-passat-8-sw':           (2014, 2023),
    'volkswagen-passat-9-sw':           (2023, 2026),
    # Tiguan
    'volkswagen-tiguan-2':              (2016, 2023),
    'volkswagen-tiguan-3':              (2023, 2026),
    'volkswagen-tiguan-business':       (2007, 2016),
    # Touareg
    'volkswagen-touareg-2':             (2010, 2018),
    'volkswagen-touareg-2-societe':     (2010, 2018),
    'volkswagen-touareg-3':             (2018, 2026),
    # Touran
    'volkswagen-touran-2':              (2010, 2015),
    'volkswagen-touran-3':              (2015, 2024),
    'volkswagen-touran-2-entreprise':   (2010, 2015),
    'volkswagen-touran-entreprise':     (2005, 2010),
    # Caddy
    'volkswagen-caddy-3':               (2005, 2015),
    'volkswagen-caddy-4':               (2015, 2020),
    'volkswagen-cross-caddy':           (2013, 2017),
    # Sharan
    'volkswagen-sharan':                (2005, 2010),
    'volkswagen-sharan-2':              (2010, 2022),
    'volkswagen-sharan-business':       (2010, 2022),
    # Eos, New Beetle, Phaeton
    'volkswagen-eos':                   (2006, 2015),
    'volkswagen-new-beetle':            (2005, 2011),
    'volkswagen-new-beetle-cabriolet':  (2005, 2011),
    'volkswagen-phaeton':               (2005, 2016),
    'volkswagen-phaeton-2':             (2010, 2016),
    # Transporter
    'volkswagen-transporter-5':         (2005, 2015),
    'volkswagen-transporter-6':         (2015, 2024),
    'volkswagen-transporter-7':         (2024, 2026),
    # Crafter 2
    'volkswagen-crafter-2':             (2016, 2026),
    # Up
    'volkswagen-up':                    (2012, 2023),
    # Gaps in existing models
    'volkswagen-caddy-4-fourgon':       (2021, 2022),
    'volkswagen-caddy-fourgon':         (2012, 2012),
    'volkswagen-crafter-minibus':       (2008, 2010),
    'volkswagen-golf-6-entreprise':     (2010, 2011),
    'volkswagen-polo-4-entreprise':     (2007, 2007),
    'volkswagen-tiguan':                (2014, 2014),
    'volkswagen-touareg':               (2015, 2015),
    'volkswagen-transporter':           (2012, 2014),
}

SLUG_NAME = {
    'volkswagen-golf-5': 'Golf 5',
    'volkswagen-golf-5-gti': 'Golf 5 Gti',
    'volkswagen-golf-5-break': 'Golf 5 Break',
    'volkswagen-golf-5-r32': 'Golf 5 R32',
    'volkswagen-golf-6': 'Golf 6',
    'volkswagen-golf-6-sw': 'Golf 6 Sw',
    'volkswagen-golf-6-gti': 'Golf 6 Gti',
    'volkswagen-golf-6-cabriolet': 'Golf 6 Cabriolet',
    'volkswagen-golf-7': 'Golf 7',
    'volkswagen-golf-7-sw': 'Golf 7 Sw',
    'volkswagen-golf-7-gti': 'Golf 7 Gti',
    'volkswagen-golf-7-cabriolet': 'Golf 7 Cabriolet',
    'volkswagen-golf-8': 'Golf 8',
    'volkswagen-golf-8-sw': 'Golf 8 Sw',
    'volkswagen-golf-8-gti': 'Golf 8 Gti',
    'volkswagen-polo-4': 'Polo 4',
    'volkswagen-polo-4-gti': 'Polo 4 Gti',
    'volkswagen-polo-5': 'Polo 5',
    'volkswagen-polo-5-gti': 'Polo 5 Gti',
    'volkswagen-polo-6': 'Polo 6',
    'volkswagen-polo-6-gti': 'Polo 6 Gti',
    'volkswagen-passat-6': 'Passat 6',
    'volkswagen-passat-6-sw': 'Passat 6 Sw',
    'volkswagen-passat-7': 'Passat 7',
    'volkswagen-passat-7-sw': 'Passat 7 Sw',
    'volkswagen-passat-8': 'Passat 8',
    'volkswagen-passat-8-sw': 'Passat 8 Sw',
    'volkswagen-passat-9-sw': 'Passat 9 Sw',
    'volkswagen-tiguan-2': 'Tiguan 2',
    'volkswagen-tiguan-3': 'Tiguan 3',
    'volkswagen-tiguan-business': 'Tiguan Business',
    'volkswagen-touareg-2': 'Touareg 2',
    'volkswagen-touareg-2-societe': 'Touareg 2 Societe',
    'volkswagen-touareg-3': 'Touareg 3',
    'volkswagen-touran-2': 'Touran 2',
    'volkswagen-touran-3': 'Touran 3',
    'volkswagen-touran-2-entreprise': 'Touran 2 Entreprise',
    'volkswagen-touran-entreprise': 'Touran Entreprise',
    'volkswagen-caddy-3': 'Caddy 3',
    'volkswagen-caddy-4': 'Caddy 4',
    'volkswagen-cross-caddy': 'Cross Caddy',
    'volkswagen-sharan': 'Sharan',
    'volkswagen-sharan-2': 'Sharan 2',
    'volkswagen-sharan-business': 'Sharan Business',
    'volkswagen-eos': 'Eos',
    'volkswagen-new-beetle': 'New Beetle',
    'volkswagen-new-beetle-cabriolet': 'New Beetle Cabriolet',
    'volkswagen-phaeton': 'Phaeton',
    'volkswagen-phaeton-2': 'Phaeton 2',
    'volkswagen-transporter-5': 'Transporter 5',
    'volkswagen-transporter-6': 'Transporter 6',
    'volkswagen-transporter-7': 'Transporter 7',
    'volkswagen-crafter-2': 'Crafter 2',
    'volkswagen-up': 'Up',
    'volkswagen-caddy-4-fourgon': 'Caddy 4 Fourgon',
    'volkswagen-caddy-fourgon': 'Caddy Fourgon',
    'volkswagen-crafter-minibus': 'Crafter Minibus',
    'volkswagen-golf-6-entreprise': 'Golf 6 Entreprise',
    'volkswagen-polo-4-entreprise': 'Polo 4 Entreprise',
    'volkswagen-tiguan': 'Tiguan',
    'volkswagen-touareg': 'Touareg',
    'volkswagen-transporter': 'Transporter',
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

BRAND = 'VOLKSWAGEN'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('volkswagen-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
