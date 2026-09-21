"""Fill remaining KIA gaps in caradisiac_catalog.json"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    'kia-sorento':     (2005, 2019),
    'kia-sorento-2':   (2009, 2015),
    'kia-sorento-3':   (2015, 2020),
    'kia-sorento-4':   (2020, 2026),
    'kia-sportage':    (2005, 2010),
    'kia-sportage-2':  (2005, 2010),
    'kia-sportage-3':  (2010, 2016),
    'kia-sportage-4':  (2016, 2022),
    'kia-sportage-5':  (2021, 2026),
    'kia-picanto':     (2005, 2016),
    'kia-picanto-2':   (2012, 2017),
    'kia-picanto-3':   (2017, 2026),
    'kia-rio':         (2005, 2012),
    'kia-rio-2':       (2005, 2012),
    'kia-rio-3':       (2011, 2017),
    'kia-rio-4':       (2017, 2026),
    'kia-cee-d':       (2006, 2018),
    'kia-cee-d-2':     (2012, 2018),
    'kia-cee-d-2-sw':  (2012, 2018),
    'kia-cee-d-sw':    (2007, 2012),
    'kia-proceed-3':   (2018, 2026),
    'kia-pro-cee-d':   (2007, 2015),
    'kia-pro-cee-d-2': (2010, 2018),
    'kia-pro-cee-d-2-gt': (2013, 2017),
    'kia-niro':        (2016, 2022),
    'kia-niro-2':      (2022, 2026),
    'kia-e-niro':      (2019, 2022),
    'kia-stonic':      (2017, 2026),
    'kia-xceed':       (2019, 2026),
    'kia-ev6':         (2021, 2026),
    'kia-ev9':         (2023, 2026),
    'kia-ev3':         (2024, 2026),
    'kia-k4':          (2025, 2026),
}

def slug_to_name(slug):
    name = slug.replace('kia-', '').replace('-', ' ').title()
    return name

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

# Map slug to catalog model name
SLUG_NAME = {
    'kia-sorento': 'Sorento', 'kia-sorento-2': 'Sorento 2', 'kia-sorento-3': 'Sorento 3', 'kia-sorento-4': 'Sorento 4',
    'kia-sportage': 'Sportage', 'kia-sportage-2': 'Sportage 2', 'kia-sportage-3': 'Sportage 3',
    'kia-sportage-4': 'Sportage 4', 'kia-sportage-5': 'Sportage 5',
    'kia-picanto': 'Picanto', 'kia-picanto-2': 'Picanto 2', 'kia-picanto-3': 'Picanto 3',
    'kia-rio': 'Rio', 'kia-rio-2': 'Rio 2', 'kia-rio-3': 'Rio 3', 'kia-rio-4': 'Rio 4',
    'kia-cee-d': 'Cee D', 'kia-cee-d-2': 'Cee D 2', 'kia-cee-d-2-sw': 'Cee D 2 Sw', 'kia-cee-d-sw': 'Cee D Sw',
    'kia-proceed-3': 'Proceed 3',
    'kia-pro-cee-d': 'Pro Cee D', 'kia-pro-cee-d-2': 'Pro Cee D 2', 'kia-pro-cee-d-2-gt': 'Pro Cee D 2 Gt',
    'kia-niro': 'Niro', 'kia-niro-2': 'Niro 2', 'kia-e-niro': 'E Niro',
    'kia-stonic': 'Stonic', 'kia-xceed': 'Xceed',
    'kia-ev6': 'Ev6', 'kia-ev9': 'Ev9', 'kia-ev3': 'Ev3', 'kia-k4': 'K4',
}

added = 0
skipped = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        skipped += 1
        continue
    model_name = SLUG_NAME.get(slug, slug_to_name(slug))
    existing = catalog.setdefault('KIA', {}).setdefault(model_name, {}).get(year)
    if not has_data(existing):
        catalog['KIA'][model_name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + KIA {model_name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées, {skipped} URLs vides.')
