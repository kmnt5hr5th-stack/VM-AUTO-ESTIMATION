"""Scrape Fiat 500X versions from Caradisiac 2014-2026"""
import asyncio, aiohttp, ssl, re, json
from pathlib import Path
from scrapers.caradisiac_scraper import detect_fuel, clean_version, BASE

PROXY = 'http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36', 'Accept-Language': 'fr-FR'}
FUEL_KEYS = ['essence', 'diesel', 'hybride', 'electrique', 'gpl']

SLUG = 'fiat-500-x'
MODEL_NAME = '500X'
BRAND = 'FIAT'
YEARS = range(2014, 2027)

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

async def fetch(session, year, sem):
    async with sem:
        url = f'{BASE}/fiches-techniques/modele--{SLUG}/{year}/'
        try:
            async with session.get(url, headers=HEADERS, proxy=PROXY,
                                   timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    print(f'  {year}: HTTP {r.status}')
                    return year, None
                html = await r.text()
                entry = parse(html, SLUG)
                total = sum(len(entry[f]) for f in FUEL_KEYS)
                print(f'  {year}: {total} versions (essence={len(entry["essence"])}, diesel={len(entry["diesel"])}, hybride={len(entry["hybride"])})')
                return year, entry
        except Exception as e:
            print(f'  {year}: ERREUR {e}')
            return year, None

async def main():
    print(f'Scraping Fiat 500X 2014-2026...')
    sem = asyncio.Semaphore(5)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
        results = await asyncio.gather(*[fetch(s, str(y), sem) for y in YEARS])
    return results

results = asyncio.run(main())

OUTPUT = Path(__file__).parent / 'vm_catalog.json'
with open(OUTPUT, encoding='utf-8') as f:
    catalog = json.load(f)

def has_data(v):
    return isinstance(v, dict) and any(v.get(k) for k in FUEL_KEYS)

added = 0
for year, entry in results:
    if entry is None or not has_data(entry):
        continue
    catalog.setdefault(BRAND, {})[MODEL_NAME] = catalog.get(BRAND, {}).get(MODEL_NAME, {})
    catalog[BRAND][MODEL_NAME][year] = entry
    added += sum(len(entry[f]) for f in FUEL_KEYS)

with open(OUTPUT, 'w', encoding='utf-8') as f:
    json.dump(catalog, f, ensure_ascii=False, indent=2)

print(f'\nTerminé : {added} versions réelles ajoutées pour Fiat 500X.')
