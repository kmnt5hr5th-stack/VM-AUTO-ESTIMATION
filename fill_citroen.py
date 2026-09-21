"""Fill Citroën catalog — all models/variants 2005-2026"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence','diesel','hybride','electrique','gpl']

SLUGS = {
    # C1
    'citroen-c1':                               (2005, 2014),
    'citroen-c1-2e-generation':                 (2014, 2022),
    'citroen-c1-entreprise':                    (2005, 2014),
    # C2
    'citroen-c2':                               (2005, 2009),
    'citroen-c2-vts':                           (2005, 2009),
    'citroen-c2-entreprise':                    (2005, 2009),
    # C3
    'citroen-c3':                               (2005, 2010),
    'citroen-c3-entreprise':                    (2005, 2010),
    'citroen-c3-aircross':                      (2017, 2024),
    'citroen-c3-aircross-2':                    (2024, 2026),
    'citroen-e-c3-aircross-2':                  (2024, 2026),
    # C4
    'citroen-c4':                               (2005, 2011),
    'citroen-c4-coupe':                         (2005, 2011),
    'citroen-c4-entreprise':                    (2005, 2011),
    'citroen-c4-2e-generation-business':        (2010, 2018),
    'citroen-c4-3e-generation-business':        (2020, 2026),
    'citroen-c4-picasso':                       (2006, 2013),
    'citroen-c4-picasso-2':                     (2013, 2018),
    'citroen-c4-picasso-business':              (2006, 2018),
    'citroen-c4-cactus':                        (2014, 2020),
    'citroen-c4-cactus-business':               (2014, 2020),
    # Grand C4
    'citroen-grand-c4-picasso':                 (2006, 2014),
    'citroen-grand-c4-picasso-2':               (2013, 2018),
    'citroen-grand-c4-picasso-business':        (2006, 2018),
    # C5
    'citroen-c5':                               (2005, 2008),
    'citroen-c5-break':                         (2005, 2008),
    'citroen-c5-2e-generation':                 (2008, 2017),
    'citroen-c5-tourer':                        (2007, 2017),
    'citroen-c5-2e-generation-tourer-xtr':      (2008, 2017),
    'citroen-c5-2e-generation-cross-tourer':    (2013, 2017),
    'citroen-c5-2e-generation-tourer-societe':  (2008, 2017),
    'citroen-c5-aircross':                      (2017, 2026),
    'citroen-c5-aircross-2':                    (2022, 2026),
    # C6, C8
    'citroen-c6':                               (2005, 2012),
    'citroen-c8':                               (2005, 2014),
    # DS (Citroën DS before DS brand split)
    'citroen-ds':                               (2009, 2015),
    'citroen-ds3':                              (2010, 2019),
    'citroen-ds3-cabriolet':                    (2012, 2016),
    'citroen-ds3-racing':                       (2010, 2015),
    'citroen-ds3-cabrio-racing':                (2014, 2016),
    'citroen-ds4':                              (2011, 2018),
    'citroen-ds4r':                             (2014, 2017),
    'citroen-ds5':                              (2012, 2018),
    # Nemo
    'citroen-nemo':                             (2007, 2014),
    'citroen-nemo-combi':                       (2007, 2014),
    # Berlingo
    'citroen-berlingo-2':                       (2007, 2019),
    'citroen-berlingo-3':                       (2018, 2026),
    'citroen-berlingo-3-xl':                    (2018, 2026),
    'citroen-berlingo-3-van':                   (2018, 2026),
    # Jumpy
    'citroen-jumpy-2':                          (2007, 2016),
    'citroen-jumpy-2-minibus':                  (2005, 2016),
    'citroen-jumpy-minibus':                    (2005, 2016),
    'citroen-jumpy-3':                          (2016, 2026),
    # Ami
    'citroen-ami-2':                            (2022, 2026),
    # C-Elysée
    'citroen-c-elysee':                         (2011, 2022),
}

SLUG_NAME = {
    'citroen-c1': 'C1',
    'citroen-c1-2e-generation': 'C1 2E Generation',
    'citroen-c1-entreprise': 'C1 Entreprise',
    'citroen-c2': 'C2',
    'citroen-c2-vts': 'C2 Vts',
    'citroen-c2-entreprise': 'C2 Entreprise',
    'citroen-c3': 'C3',
    'citroen-c3-entreprise': 'C3 Entreprise',
    'citroen-c3-aircross': 'C3 Aircross',
    'citroen-c3-aircross-2': 'C3 Aircross 2',
    'citroen-e-c3-aircross-2': 'E C3 Aircross 2',
    'citroen-c4': 'C4',
    'citroen-c4-coupe': 'C4 Coupe',
    'citroen-c4-entreprise': 'C4 Entreprise',
    'citroen-c4-2e-generation-business': 'C4 2E Generation Business',
    'citroen-c4-3e-generation-business': 'C4 3E Generation Business',
    'citroen-c4-picasso': 'C4 Picasso',
    'citroen-c4-picasso-2': 'C4 Picasso 2',
    'citroen-c4-picasso-business': 'C4 Picasso Business',
    'citroen-c4-cactus': 'C4 Cactus',
    'citroen-c4-cactus-business': 'C4 Cactus Business',
    'citroen-grand-c4-picasso': 'Grand C4 Picasso',
    'citroen-grand-c4-picasso-2': 'Grand C4 Picasso 2',
    'citroen-grand-c4-picasso-business': 'Grand C4 Picasso Business',
    'citroen-c5': 'C5',
    'citroen-c5-break': 'C5 Break',
    'citroen-c5-2e-generation': 'C5 2E Generation',
    'citroen-c5-tourer': 'C5 Tourer',
    'citroen-c5-2e-generation-tourer-xtr': 'C5 2E Generation Tourer Xtr',
    'citroen-c5-2e-generation-cross-tourer': 'C5 2E Generation Cross Tourer',
    'citroen-c5-2e-generation-tourer-societe': 'C5 2E Generation Tourer Societe',
    'citroen-c5-aircross': 'C5 Aircross',
    'citroen-c5-aircross-2': 'C5 Aircross 2',
    'citroen-c6': 'C6',
    'citroen-c8': 'C8',
    'citroen-ds': 'Ds',
    'citroen-ds3': 'Ds3',
    'citroen-ds3-cabriolet': 'Ds3 Cabriolet',
    'citroen-ds3-racing': 'Ds3 Racing',
    'citroen-ds3-cabrio-racing': 'Ds3 Cabrio Racing',
    'citroen-ds4': 'Ds4',
    'citroen-ds4r': 'Ds4R',
    'citroen-ds5': 'Ds5',
    'citroen-nemo': 'Nemo',
    'citroen-nemo-combi': 'Nemo Combi',
    'citroen-berlingo-2': 'Berlingo 2',
    'citroen-berlingo-3': 'Berlingo 3',
    'citroen-berlingo-3-xl': 'Berlingo 3 Xl',
    'citroen-berlingo-3-van': 'Berlingo 3 Van',
    'citroen-jumpy-2': 'Jumpy 2',
    'citroen-jumpy-2-minibus': 'Jumpy 2 Minibus',
    'citroen-jumpy-minibus': 'Jumpy Minibus',
    'citroen-jumpy-3': 'Jumpy 3',
    'citroen-ami-2': 'Ami 2',
    'citroen-c-elysee': 'C Elysee',
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

BRAND = 'CITROËN'
added = 0
for slug, year, entry in results:
    if entry is None or not has_data(entry):
        continue
    name = SLUG_NAME.get(slug, slug.replace('citroen-', '').replace('-', ' ').title())
    existing = catalog.setdefault(BRAND, {}).setdefault(name, {}).get(year)
    if not has_data(existing):
        catalog[BRAND][name][year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        added += n
        print(f'  + {BRAND} {name} {year}: {n} versions')

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé: {added} versions ajoutées.')
