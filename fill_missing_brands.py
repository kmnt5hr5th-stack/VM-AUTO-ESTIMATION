"""Fill remaining empty brands: Alfa Romeo, DS, Honda, Hyundai, Jaguar, Jeep, Lexus"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence', 'diesel', 'hybride', 'electrique', 'gpl']

# (slug, brand, model_name, first_year, last_year)
ENTRIES = [
    # Alfa Romeo
    ('alfa-romeo-giulietta', 'ALFA ROMEO', 'Giulietta', 2010, 2021),
    ('alfa-romeo-giulietta-2', 'ALFA ROMEO', 'Giulietta', 2016, 2021),
    ('alfa-romeo-giulia', 'ALFA ROMEO', 'Giulia', 2016, 2026),
    ('alfa-romeo-stelvio', 'ALFA ROMEO', 'Stelvio', 2017, 2026),
    ('alfa-romeo-tonale', 'ALFA ROMEO', 'Tonale', 2022, 2026),
    ('alfa-romeo-mito', 'ALFA ROMEO', 'Mito', 2008, 2019),
    ('alfa-romeo-147', 'ALFA ROMEO', '147', 2005, 2010),
    ('alfa-romeo-156', 'ALFA ROMEO', '156', 2005, 2007),
    ('alfa-romeo-159', 'ALFA ROMEO', '159', 2005, 2012),
    ('alfa-romeo-brera', 'ALFA ROMEO', 'Brera', 2006, 2012),
    ('alfa-romeo-spider', 'ALFA ROMEO', 'Spider', 2006, 2011),
    # DS
    ('ds-ds3', 'DS', 'DS3', 2010, 2019),
    ('ds-ds3-2', 'DS', 'DS3 Crossback', 2019, 2026),
    ('ds-ds3-crossback', 'DS', 'DS3 Crossback', 2019, 2026),
    ('ds-ds4', 'DS', 'DS4', 2011, 2021),
    ('ds-ds4-2', 'DS', 'DS4', 2021, 2026),
    ('ds-ds5', 'DS', 'DS5', 2011, 2018),
    ('ds-ds7-crossback', 'DS', 'DS7 Crossback', 2017, 2026),
    ('ds-ds9', 'DS', 'DS9', 2021, 2026),
    # Honda
    ('honda-civic', 'HONDA', 'Civic', 2005, 2012),
    ('honda-civic-2', 'HONDA', 'Civic', 2012, 2017),
    ('honda-civic-3', 'HONDA', 'Civic', 2017, 2022),
    ('honda-civic-4', 'HONDA', 'Civic', 2022, 2026),
    ('honda-jazz', 'HONDA', 'Jazz', 2005, 2015),
    ('honda-jazz-2', 'HONDA', 'Jazz', 2015, 2020),
    ('honda-jazz-3', 'HONDA', 'Jazz', 2020, 2026),
    ('honda-cr-v', 'HONDA', 'CR-V', 2007, 2012),
    ('honda-cr-v-2', 'HONDA', 'CR-V', 2012, 2018),
    ('honda-cr-v-3', 'HONDA', 'CR-V', 2018, 2026),
    ('honda-hr-v', 'HONDA', 'HR-V', 2015, 2021),
    ('honda-hr-v-2', 'HONDA', 'HR-V', 2021, 2026),
    ('honda-accord', 'HONDA', 'Accord', 2008, 2015),
    ('honda-e', 'HONDA', 'Honda e', 2020, 2024),
    # Hyundai
    ('hyundai-i10', 'HYUNDAI', 'i10', 2008, 2013),
    ('hyundai-i10-2', 'HYUNDAI', 'i10', 2013, 2019),
    ('hyundai-i10-3', 'HYUNDAI', 'i10', 2019, 2026),
    ('hyundai-i20', 'HYUNDAI', 'i20', 2009, 2014),
    ('hyundai-i20-2', 'HYUNDAI', 'i20', 2014, 2020),
    ('hyundai-i20-3', 'HYUNDAI', 'i20', 2020, 2026),
    ('hyundai-i30', 'HYUNDAI', 'i30', 2007, 2012),
    ('hyundai-i30-2', 'HYUNDAI', 'i30', 2012, 2017),
    ('hyundai-i30-3', 'HYUNDAI', 'i30', 2017, 2026),
    ('hyundai-tucson', 'HYUNDAI', 'Tucson', 2010, 2015),
    ('hyundai-tucson-2', 'HYUNDAI', 'Tucson', 2015, 2020),
    ('hyundai-tucson-3', 'HYUNDAI', 'Tucson', 2020, 2026),
    ('hyundai-santa-fe', 'HYUNDAI', 'Santa Fe', 2012, 2018),
    ('hyundai-santa-fe-2', 'HYUNDAI', 'Santa Fe', 2018, 2026),
    ('hyundai-kona', 'HYUNDAI', 'Kona', 2017, 2023),
    ('hyundai-kona-2', 'HYUNDAI', 'Kona', 2023, 2026),
    ('hyundai-ioniq', 'HYUNDAI', 'Ioniq', 2016, 2022),
    ('hyundai-ioniq-5', 'HYUNDAI', 'Ioniq 5', 2021, 2026),
    ('hyundai-ioniq-6', 'HYUNDAI', 'Ioniq 6', 2022, 2026),
    # Jaguar
    ('jaguar-xe', 'JAGUAR', 'XE', 2015, 2026),
    ('jaguar-xf', 'JAGUAR', 'XF', 2007, 2015),
    ('jaguar-xf-2', 'JAGUAR', 'XF', 2015, 2026),
    ('jaguar-xj', 'JAGUAR', 'XJ', 2009, 2019),
    ('jaguar-f-pace', 'JAGUAR', 'F-Pace', 2016, 2026),
    ('jaguar-e-pace', 'JAGUAR', 'E-Pace', 2017, 2026),
    ('jaguar-i-pace', 'JAGUAR', 'I-Pace', 2018, 2026),
    ('jaguar-f-type', 'JAGUAR', 'F-Type', 2013, 2026),
    # Jeep
    ('jeep-renegade', 'JEEP', 'Renegade', 2014, 2026),
    ('jeep-compass', 'JEEP', 'Compass', 2017, 2026),
    ('jeep-grand-cherokee', 'JEEP', 'Grand Cherokee', 2011, 2021),
    ('jeep-grand-cherokee-2', 'JEEP', 'Grand Cherokee', 2021, 2026),
    ('jeep-wrangler', 'JEEP', 'Wrangler', 2007, 2018),
    ('jeep-wrangler-2', 'JEEP', 'Wrangler', 2018, 2026),
    ('jeep-avenger', 'JEEP', 'Avenger', 2023, 2026),
    # Lexus
    ('lexus-ct', 'LEXUS', 'CT', 2011, 2022),
    ('lexus-is', 'LEXUS', 'IS', 2005, 2013),
    ('lexus-is-2', 'LEXUS', 'IS', 2013, 2026),
    ('lexus-es', 'LEXUS', 'ES', 2018, 2026),
    ('lexus-gs', 'LEXUS', 'GS', 2012, 2020),
    ('lexus-ls', 'LEXUS', 'LS', 2017, 2026),
    ('lexus-nx', 'LEXUS', 'NX', 2014, 2021),
    ('lexus-nx-2', 'LEXUS', 'NX', 2021, 2026),
    ('lexus-rx', 'LEXUS', 'RX', 2015, 2022),
    ('lexus-rx-2', 'LEXUS', 'RX', 2022, 2026),
    ('lexus-ux', 'LEXUS', 'UX', 2019, 2026),
    ('lexus-lc', 'LEXUS', 'LC', 2017, 2026),
]

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
    tasks = [(slug, str(y)) for slug, brand, name, first, last in ENTRIES for y in range(first, last+1)]
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

slug_info = {slug: (brand, name) for slug, brand, name, _, _ in ENTRIES}
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    brand, name = slug_info[slug]
    existing = catalog.setdefault(brand, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[brand][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {brand} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
