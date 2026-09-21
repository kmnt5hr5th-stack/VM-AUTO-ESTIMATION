"""
Fill catalog gaps URL by URL — fast async fetch, no artificial delays.
Step 1: Discover Caradisiac slugs for brands with gaps.
Step 2: Fetch all missing year-pages concurrently.
"""
import asyncio, json, re, ssl, time, logging
from pathlib import Path
import aiohttp
from scrapers.caradisiac_scraper import (
    get_brands, get_model_slugs_for_brand, extract_model_name,
    detect_fuel, clean_version, BASE,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

OUTPUT   = Path(__file__).parent / "caradisiac_catalog.json"
FUEL_KEYS = ["essence", "diesel", "hybride", "electrique", "gpl"]
MIN_YEAR  = 2005
EXCLUDE   = {"BENTLEY", "FERRARI", "LAMBORGHINI", "ASTON MARTIN", "ROLLS-ROYCE", "MASERATI"}
HEADERS   = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
CONCURRENCY = 10

def has_data(v):
    return isinstance(v, dict) and any(v.get(f) for f in FUEL_KEYS)

def parse_versions_from_html(html: str, slug: str, year: str) -> dict:
    """Extract {fuel: [versions]} from Caradisiac page (any version link on the page)."""
    base = f"/fiches-techniques/modele--{slug}/"
    year_prefix = f"{base}{year}/"
    entry = {f: [] for f in FUEL_KEYS}
    seen = set()
    for m in re.finditer(rf'href="({re.escape(base)}[^"?]+)"[^>]*>([^<]{{3,}})<', html):
        href, text = m.group(1).rstrip("/"), m.group(2).strip()
        # Must have a version segment (at least /YYYY/something)
        after = href[len(base):]
        parts = after.split("/")
        if len(parts) < 2 or not parts[1]:
            continue
        v = clean_version(text)
        if not v or len(v) < 3 or v in seen:
            continue
        seen.add(v)
        fuel = detect_fuel(v)
        entry[fuel].append(v)
    return entry

async def fetch_year(session, slug: str, year: str, sem, proxy: str = None) -> dict:
    url = f"{BASE}/fiches-techniques/modele--{slug}/{year}/"
    async with sem:
        try:
            async with session.get(url, headers=HEADERS, proxy=proxy, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return {f: [] for f in FUEL_KEYS}
                html = await r.text()
                return parse_versions_from_html(html, slug, year)
        except Exception:
            return {f: [] for f in FUEL_KEYS}

async def main():
    with open(OUTPUT, encoding="utf-8") as f:
        catalog = json.load(f)

    # Step 1: compute gaps
    gaps = {}  # {marque: {modele: [years]}}
    for marque, modeles in catalog.items():
        if marque in EXCLUDE:
            continue
        for modele, annees in modeles.items():
            filled = [int(y) for y, v in annees.items() if has_data(v)]
            if not filled:
                continue
            first, last = max(min(filled), MIN_YEAR), max(filled)
            missing = [str(y) for y in range(first, last + 1) if not has_data(annees.get(str(y)))]
            if missing:
                gaps.setdefault(marque, {})[modele] = missing

    total = sum(len(yrs) for m in gaps.values() for yrs in m.values())
    log.info(f"Gaps: {len(gaps)} marques, {sum(len(v) for v in gaps.values())} modèles, {total} années")

    # Step 2: discover slugs for brands with gaps (via proxy)
    log.info("Découverte des slugs Caradisiac (via proxy)...")
    PROXY_URL = "http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80"
    import requests as _req
    _proxies = {"http": PROXY_URL, "https": PROXY_URL}

    BRAND_SLUG_MAP = {
        "ABARTH": "abarth", "ALFA ROMEO": "alfa-romeo", "ALPINE": "alpine",
        "AUDI": "audi", "BMW": "bmw", "BYD": "byd", "CADILLAC": "cadillac",
        "CHEVROLET": "chevrolet", "CHRYSLER": "chrysler", "CITROËN": "citroen",
        "CITROEN": "citroen", "CUPRA": "cupra", "DACIA": "dacia", "DODGE": "dodge",
        "DS": "ds", "FIAT": "fiat", "FORD": "ford", "GENESIS": "genesis",
        "HONDA": "honda", "HYUNDAI": "hyundai", "ISUZU": "isuzu", "JAGUAR": "jaguar",
        "JEEP": "jeep", "KIA": "kia", "LAND ROVER": "land-rover", "LANCIA": "lancia",
        "LEAPMOTOR": "leapmotor", "LEXUS": "lexus", "LINCOLN": "lincoln",
        "LYNK & CO": "lynk-co", "MAZDA": "mazda", "MERCEDES": "mercedes",
        "MG": "mg", "MINI": "mini", "MITSUBISHI": "mitsubishi", "NISSAN": "nissan",
        "NIO": "nio", "OPEL": "opel", "PEUGEOT": "peugeot", "POLESTAR": "polestar",
        "PORSCHE": "porsche", "RENAULT": "renault", "SEAT": "seat", "SKODA": "skoda",
        "SMART": "smart", "SSANGYONG": "ssangyong", "SUBARU": "subaru",
        "SUZUKI": "suzuki", "TESLA": "tesla", "TOYOTA": "toyota",
        "VOLKSWAGEN": "volkswagen", "VOLVO": "volvo", "XPENG": "xpeng",
    }

    slug_map = {}
    for brand_display in sorted(gaps.keys()):
        brand_slug = BRAND_SLUG_MAP.get(brand_display)
        if not brand_slug:
            log.warning(f"  Slug inconnu pour {brand_display}")
            continue
        try:
            r = _req.get(f"{BASE}/auto--{brand_slug}/", headers=HEADERS, proxies=_proxies, timeout=15)
            if r.status_code != 200:
                log.warning(f"  {brand_display}: HTTP {r.status_code}")
                continue
            import re as _re
            model_slugs_raw = list(dict.fromkeys(_re.findall(rf'/modele--({_re.escape(brand_slug)}-[a-z0-9\-]+)', r.text)))
            for ms in model_slugs_raw:
                name = extract_model_name(brand_slug, ms)
                if name in gaps.get(brand_display, {}):
                    key = (brand_display, name)
                    if key not in slug_map:
                        slug_map[key] = ms
        except Exception as e:
            log.warning(f"  {brand_display}: {e}")

    log.info(f"Slugs trouvés: {len(slug_map)}")

    # Step 3: build fetch list
    fetch_list = []  # [(marque, modele, slug, year)]
    for (marque, modele), slug in slug_map.items():
        for year in gaps[marque][modele]:
            fetch_list.append((marque, modele, slug, year))

    log.info(f"URLs à fetcher: {len(fetch_list)}")

    # Step 4: fetch all concurrently
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=ssl_ctx)
    PROXY = "http://lmgdmysu:nomkg04o6fsd@p.webshare.io:80"

    t0 = time.time()
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_year(session, slug, year, sem, PROXY) for (_, _, slug, year) in fetch_list]
        results = await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    log.info(f"Fetch terminé en {elapsed:.1f}s")

    # Step 5: update catalog
    added = 0
    for (marque, modele, slug, year), entry in zip(fetch_list, results):
        catalog.setdefault(marque, {}).setdefault(modele, {})[year] = entry
        n = sum(len(entry[f]) for f in FUEL_KEYS)
        if n:
            added += n
            log.info(f"  + {marque} {modele} {year}: {n} versions")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    log.info(f"\nTerminé. {added} versions ajoutées en {elapsed:.1f}s.")

if __name__ == "__main__":
    asyncio.run(main())
