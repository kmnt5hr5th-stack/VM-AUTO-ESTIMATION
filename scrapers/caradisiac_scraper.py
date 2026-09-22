"""
Scrape Caradisiac fiches-techniques to build a complete vehicle catalog.
Output: vm_catalog.json
Structure: { "RENAULT": { "Clio": { "2020": { "essence": [...], "diesel": [...] } } } }
"""

import re
import json
import time
import random
import logging
from pathlib import Path
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

BASE    = "https://www.caradisiac.com"
OUTPUT  = Path(__file__).parent.parent / "vm_catalog.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def get(url: str, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "lxml")
            if r.status_code == 404:
                return None
        except Exception as e:
            log.warning(f"  Erreur {url}: {e}")
        time.sleep(random.uniform(0.1, 0.3))
    return None


def detect_fuel(version: str) -> str:
    v = version.upper()
    # Plug-in hybrids
    if any(x in v for x in ["E-TECH", "E-TENSE", "FULL HYBRID", "HYBRIDE", "HYBRID", "HSD", "MHEV", "PHEV",
                              " DE ", " DE 4", "300 DE", "350 E ", "300 E ", "PLUG-IN", "PLUGIN",
                              "GTE", "RECHARGE",
                              "330E", "530E", "225XE", "225E", "740E", "740LE",
                              "P300E", "P400E",
                              "TFSI E "]):
        return "hybride"
    if any(x in v for x in ["ELECTRIQUE", "ELECTRIC", "KWH", "EV ", "BEV"]):
        return "electrique"
    if any(x in v for x in ["GPL", "GNV"]):
        return "gpl"
    if any(x in v for x in ["TDI", "DCI", "HDI", "BLUEHDI", "BLUE DCI", "BLUE HDI",
                              "CDTI", "CDI", "JTD", "CRDI", "JTDM", "SDI", "D4D", "TDCI",
                              "MULTIJET", "MJET", "BLUETEC", "BLUEEFFICIENCY", "ECOBLUE", "BLUEMOTION",
                              "VCDI", "D-4D", "D4D", "DTI", "DDIS",
                              "SKYACTIV-D", "SKYACTIV D",
                              "2.0 D", "1.6 D", "1.5 D", "2.2 D", "TD ", " TD", " D "]):
        return "diesel"
    # BMW/Volvo diesel: "116D", "320D", "D3", "D4", "D5" etc.
    if re.search(r'\d+D\b', v) or re.search(r'\bD\d\b', v):
        return "diesel"
    return "essence"


def clean_version(raw: str) -> str:
    """Remove leading 'V ' or 'V (2) ' prefix from Caradisiac version names."""
    s = raw.strip()
    s = re.sub(r"^V\s*\(\d+\)\s*", "", s)
    s = re.sub(r"^V\s+", "", s)
    return s.strip()


def extract_model_name(brand_slug, full_slug):
    """
    Extract clean model name from Caradisiac slug.
    Ex: brand='renault', slug='renault-clio-5'                  → 'Clio'
        brand='renault', slug='renault-clio-4-rs'               → 'Clio'
        brand='peugeot', slug='peugeot-308-3'                   → '308'
        brand='mercedes-benz', slug='mercedes-benz-classe-a-4'  → 'Classe A'
        brand='bmw', slug='bmw-serie-3-e93-cabriolet-m3'        → 'Serie 3'
        brand='bmw', slug='bmw-serie-4-f82'                     → 'Serie 4'
        brand='bmw', slug='bmw-serie-5-g31-touring'             → 'Serie 5'
        brand='audi', slug='audi-a3-sportback'                  → 'A3'
        brand='audi', slug='audi-rs3-2e-generation-berline'     → 'Rs3'
    """
    model_part = full_slug[len(brand_slug):].lstrip("-")

    # Words that act as series designators: the digit following them is part of the model name.
    # e.g. "serie-3", "classe-a" — do NOT strip these trailing digits/letters.
    SERIES_DESIGNATORS = {"serie", "series", "classe", "klasse"}

    # Body style and trim suffixes to strip (longest first to avoid partial matches).
    BODY_STYLE_SUFFIXES = [
        "-gran-coupe", "-gran-turismo", "-gran-cabrio",
        "-plug-in-hybrid", "-phev",
        "-sportback", "-hatchback", "-fastback",
        "-cabriolet", "-cabrio", "-convertible",
        "-roadster", "-spyder", "-spider",
        "-touring", "-estate", "-break", "-sw", "-avant", "-wagon",
        "-coupe", "-berline", "-targa",
        "-societe", "-van", "-pickup",
        "-gti", "-gts", "-gtd", "-gt",
        "-rs", "-st", "-m3", "-m4", "-m5", "-amg",
    ]

    # Generation code: 1-2 letters followed by 2+ digits (E93, F82, G31, W204)
    # or 1-3 digits followed by 1-2 letters (e.g. "2e" in "2e-generation"),
    # or single-letter + single-digit where it follows a known model token (B9, C7).
    # The regex strips a trailing "-<letters><digits>" or "-<digits><letters>" segment.
    GEN_CODE_RE = re.compile(
        r"-(?:[a-z]{1,2}\d{2,4}|\d{1,3}[a-z]{1,2}|\d+e-generation)$",
        re.IGNORECASE
    )

    # Single-letter+single-digit gen codes (B9, C7, E9) — only strip when preceded by
    # a token that looks like a model name (letter-only token), not when that token is
    # itself a series designator (handled separately below).
    SHORT_GEN_CODE_RE = re.compile(r"-[a-z]\d$", re.IGNORECASE)

    def _last_token(s):
        parts = s.rsplit("-", 1)
        return parts[-1].lower() if len(parts) > 1 else s.lower()

    # Iteratively strip suffixes until stable
    prev = None
    while prev != model_part:
        prev = model_part
        lower = model_part.lower()

        # 1. Strip body style and trim suffixes
        for suffix in BODY_STYLE_SUFFIXES:
            if lower.endswith(suffix):
                model_part = model_part[: -len(suffix)]
                lower = model_part.lower()
                break

        # 2. Strip "-N-portes" door-count suffixes (e.g. "5-portes", "3-portes")
        model_part = re.sub(r"-\d+-portes$", "", model_part, flags=re.IGNORECASE)

        # 3. Strip generation codes like E93, F82, G31, W204, B9 (multi-digit)
        model_part = GEN_CODE_RE.sub("", model_part)

        # 4. Strip short single-letter+single-digit gen codes (B9, C7) but only when
        #    the preceding token is a plain model identifier, not a series designator.
        m = SHORT_GEN_CODE_RE.search(model_part)
        if m:
            before = model_part[: m.start()]
            if _last_token(before) not in SERIES_DESIGNATORS:
                model_part = before

        # 5. Strip plain trailing generation numbers (-5, -4, -3, -2, -1) but NOT
        #    when the preceding token is a series designator (serie-3, classe-a, etc.)
        #    and NOT when the preceding token is itself numeric (308-3 → strip, fine).
        m2 = re.search(r"-(\d+)$", model_part)
        if m2:
            before = model_part[: m2.start()]
            last = _last_token(before)
            # Keep the digit if it belongs to a series-style name
            if last not in SERIES_DESIGNATORS:
                model_part = before

    return model_part.replace("-", " ").title()


# ── Scraping layers ───────────────────────────────────────────────────────────

ACTIVE_BRANDS = {
    # Français
    "citroen", "peugeot", "renault", "ds", "alpine", "dacia",
    # Allemands
    "audi", "bmw", "mercedes", "volkswagen", "opel", "mini", "porsche", "smart",
    "seat", "skoda", "cupra",
    # Japonais
    "toyota", "honda", "nissan", "mazda", "mitsubishi", "suzuki", "lexus", "subaru", "isuzu",
    # Coréens
    "hyundai", "kia", "genesis",
    # Italiens
    "fiat", "alfa-romeo", "lancia", "abarth",
    # Américains
    "ford", "jeep", "tesla", "chevrolet", "dodge", "lincoln", "cadillac", "chrysler",
    # Britanniques
    "land-rover", "jaguar", "volvo",
    # Nouveaux/Chinois
    "mg", "polestar", "byd", "nio", "xpeng", "lynk-co", "leapmotor",
    # Autres
    "ssangyong",
}

def get_brands():
    """Returns {brand_display: brand_slug} — active brands only."""
    soup = get(f"{BASE}/constructeurs--automobiles/")
    if not soup:
        return {}
    by_slug = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/auto--([a-z0-9\-]+)/?$", href)
        if m:
            slug = m.group(1)
            if slug not in ACTIVE_BRANDS:
                continue
            label = a.get_text(strip=True).upper() or slug.upper()
            if label and label not in ("TOUTES LES MARQUES", "") and slug not in by_slug:
                by_slug[slug] = label
    # Retourner {label: slug}
    return {label: slug for slug, label in by_slug.items()}


def get_model_slugs_for_brand(brand_slug):
    """
    Returns list of Caradisiac modele slugs for a brand.
    Handles gamme → expanded into individual modele slugs.
    """
    soup = get(f"{BASE}/auto--{brand_slug}/")
    if not soup:
        return []

    gammes, modeles = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m_gamme  = re.search(r"/gamme--([a-z0-9\-]+)/?$", href)
        m_modele = re.search(r"/modele--([a-z0-9\-]+)/?$", href)
        if m_gamme:
            gammes.append(m_gamme.group(1))
        elif m_modele:
            slug = m_modele.group(1)
            if slug.startswith(brand_slug):
                modeles.append(slug)

    # Expand gammes → list of generation modeles
    for gamme_slug in gammes:
        gsoup = get(f"{BASE}/gamme--{gamme_slug}/")
        if not gsoup:
            continue
        for a in gsoup.find_all("a", href=True):
            m = re.search(r"/modele--([a-z0-9\-]+)/?$", a["href"])
            if m:
                slug = m.group(1)
                if slug.startswith(brand_slug) and slug not in modeles:
                    modeles.append(slug)

    return list(set(modeles))


def get_years_for_model(model_slug):
    """Returns list of years available for a given modele slug."""
    soup = get(f"{BASE}/fiches-techniques/modele--{model_slug}/")
    if not soup:
        return []
    years = set()
    for a in soup.find_all("a", href=True):
        m = re.search(rf"/fiches-techniques/modele--{re.escape(model_slug)}/(\d{{4}})/?$", a["href"])
        if m:
            years.add(m.group(1))
    return sorted(years)


def get_versions_for_year(model_slug, year):
    """Returns list of raw version strings for a model+year."""
    soup = get(f"{BASE}/fiches-techniques/modele--{model_slug}/{year}/")
    if not soup:
        return []
    versions = []
    # Caradisiac shows all versions across years in a table; capture any version
    # link for this model (any year), not just the exact year.
    base_pattern = f"/fiches-techniques/modele--{model_slug}/"
    year_pattern  = f"{base_pattern}{year}/"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if base_pattern not in href:
            continue
        # Skip bare year-index links (no version segment)
        clean = href.rstrip("/")
        if clean == year_pattern.rstrip("/"):
            continue
        # Must be a version link (not just /YYYY/)
        after = clean[len(base_pattern):]   # e.g. "2019/1-4-boosterjet-140"
        parts = after.split("/")
        if len(parts) < 2 or not parts[1]:
            continue
        if parts[0] != year:  # ignorer les versions d'autres années
            continue
        txt = a.get_text(strip=True)
        if txt and len(txt) > 3 and txt not in versions:
            versions.append(txt)
    return versions


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    # Charger l'existant pour reprendre si interrompu
    catalog: dict = {}
    if OUTPUT.exists():
        with open(OUTPUT) as f:
            catalog = json.load(f)
        log.info(f"Reprise: {len(catalog)} marques déjà traitées")

    brands = get_brands()
    log.info(f"Marques trouvées: {len(brands)}")

    for brand_display, brand_slug in brands.items():
        if brand_display in catalog:
            log.info(f"[skip] {brand_display}")
            continue

        log.info(f"\n── {brand_display} ──")
        catalog[brand_display] = {}

        model_slugs = get_model_slugs_for_brand(brand_slug)
        log.info(f"  {len(model_slugs)} modèles/générations")

        for model_slug in model_slugs:
            model_name = extract_model_name(brand_slug, model_slug)
            if model_name not in catalog[brand_display]:
                catalog[brand_display][model_name] = {}

            years = get_years_for_model(model_slug)
            log.info(f"  {model_slug} → {model_name}: {years}")

            for year in years:
                raw_versions = get_versions_for_year(model_slug, year)
                if not raw_versions:
                    continue

                if year not in catalog[brand_display][model_name]:
                    catalog[brand_display][model_name][year] = {
                        "essence": [], "diesel": [], "hybride": [],
                        "electrique": [], "gpl": []
                    }

                for raw in raw_versions:
                    v = clean_version(raw)
                    if not v:
                        continue
                    fuel = detect_fuel(v)
                    bucket = catalog[brand_display][model_name][year][fuel]
                    if v not in bucket:
                        bucket.append(v)

                total = sum(len(vs) for vs in catalog[brand_display][model_name][year].values())
                log.info(f"    {year}: {total} versions")

                time.sleep(random.uniform(0.05, 0.15))

        # Sauvegarder après chaque marque
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)

        time.sleep(random.uniform(0.1, 0.3))

    # Stats finales
    total_models  = sum(len(m) for m in catalog.values())
    total_years   = sum(len(y) for m in catalog.values() for y in m.values())
    total_versions = sum(
        len(vs)
        for m in catalog.values()
        for y in m.values()
        for fuel_vs in y.values()
        for vs in [fuel_vs]
    )
    log.info(f"\nTerminé: {len(catalog)} marques, {total_models} modèles, {total_years} années, {total_versions} versions")
    log.info(f"Sauvegardé: {OUTPUT}")


if __name__ == "__main__":
    run()
