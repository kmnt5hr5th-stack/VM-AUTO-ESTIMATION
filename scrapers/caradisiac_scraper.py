"""
Scrape Caradisiac fiches-techniques to build a complete vehicle catalog.
Output: caradisiac_catalog.json
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
OUTPUT  = Path(__file__).parent.parent / "caradisiac_catalog.json"
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
        time.sleep(random.uniform(0.5, 1.2))
    return None


def detect_fuel(version: str) -> str:
    v = version.upper()
    # Plug-in hybrids (Mercedes: 300 DE = diesel-electric, 300 E = gasoline-electric)
    if any(x in v for x in ["E-TECH", "FULL HYBRID", "HYBRIDE", "HYBRID", "HSD", "MHEV", "PHEV",
                              " DE ", " DE 4", "300 DE", "350 E ", "300 E ", "PLUG-IN", "PLUGIN"]):
        return "hybride"
    if any(x in v for x in ["ELECTRIQUE", "ELECTRIC", "KWH", "EV ", "BEV"]):
        return "electrique"
    if any(x in v for x in ["GPL", "GNV"]):
        return "gpl"
    if any(x in v for x in ["TDI", "DCI", "HDI", "BLUEHDI", "BLUE DCI", "BLUE HDI",
                              "CDTI", "JTD", "CRDI", "JTDM", "SDI", "D4D", "TDCI",
                              "2.0 D", "1.6 D", "1.5 D", "2.2 D", "TD ", " D "]):
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
    Ex: brand='renault', slug='renault-clio-5'    → 'Clio'
        brand='renault', slug='renault-clio-4-rs'  → 'Clio'
        brand='peugeot', slug='peugeot-308-3'      → '308'
        brand='mercedes-benz', slug='mercedes-benz-classe-a-4' → 'Classe A'
    """
    model_part = full_slug[len(brand_slug):].lstrip("-")
    # Remove known variant suffixes first
    for suffix in ["-rs", "-gti", "-gt", "-gts", "-gtd", "-st", "-sw", "-estate",
                   "-break", "-coupe", "-cabriolet", "-cabrio", "-roadster",
                   "-societe", "-van", "-pickup", "-phev", "-plug-in-hybrid"]:
        if model_part.endswith(suffix):
            model_part = model_part[: -len(suffix)]
    # Remove generation codes: trailing -5, -4, -f20, -e46, -w204, etc.
    model_part = re.sub(r"-[a-z]?\d+$", "", model_part)
    return model_part.replace("-", " ").title()


# ── Scraping layers ───────────────────────────────────────────────────────────

ACTIVE_BRANDS = {
    # Français
    "citroen", "peugeot", "renault", "ds", "alpine", "dacia",
    # Allemands
    "audi", "bmw", "mercedes", "volkswagen", "opel", "mini", "porsche", "smart",
    "seat", "skoda", "cupra",
    # Japonais
    "toyota", "honda", "nissan", "mazda", "mitsubishi", "suzuki", "lexus",
    "subaru", "isuzu",
    # Coréens
    "hyundai", "kia", "genesis",
    # Italiens
    "fiat", "alfa-romeo", "lancia", "abarth", "maserati", "ferrari", "lamborghini",
    # Américains
    "ford", "jeep", "chevrolet", "dodge", "tesla", "lincoln", "cadillac", "chrysler",
    # Britanniques
    "land-rover", "jaguar", "aston-martin", "bentley", "rolls-royce",
    # Suédois
    "volvo",
    # Chinois/nouveaux
    "mg", "byd", "nio", "xpeng", "polestar", "lynk-co", "leapmotor",
    # Autres actifs en France
    "seat", "ssangyong",
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
        after = clean[len(base_pattern):]   # e.g. "2015/220+d+4matic"
        parts = after.split("/")
        if len(parts) < 2 or not parts[1]:
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

                time.sleep(random.uniform(0.3, 0.7))

        # Sauvegarder après chaque marque
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)

        time.sleep(random.uniform(0.5, 1.5))

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
