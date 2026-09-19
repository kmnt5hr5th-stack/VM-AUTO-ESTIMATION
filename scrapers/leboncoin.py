import asyncio
import datetime
import logging
import uuid
import random
import re
import json as _json
from typing import Optional
from curl_cffi.requests import AsyncSession
from playwright.async_api import async_playwright, BrowserContext

from .base import BaseScraper
from ._proxy import LBC_PROXY_URL

logger = logging.getLogger(__name__)

# ── Contexte Playwright persistant (partagé entre toutes les requêtes) ────────
_pw: dict = {"playwright": None, "browser": None, "context": None}
_pw_lock = asyncio.Lock()
_pw_sem: Optional[asyncio.Semaphore] = None   # initialisé à la 1ère utilisation


async def _init_pw_context() -> None:
    """Lance un navigateur Playwright persistant et initialise la session DataDome."""
    global _pw_sem
    for key in ("context", "browser", "playwright"):
        try:
            obj = _pw.get(key)
            if obj:
                await obj.close() if key != "playwright" else await obj.stop()
        except Exception:
            pass
        _pw[key] = None

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
    )
    proxy_cfg = _camoufox_proxy()
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="fr-FR",
        extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
        proxy=proxy_cfg,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    # Visite homepage pour initialiser la session DataDome dans ce contexte
    page = await context.new_page()
    try:
        await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(1.5)
    finally:
        await page.close()

    _pw["playwright"] = pw
    _pw["browser"] = browser
    _pw["context"] = context
    if _pw_sem is None:
        _pw_sem = asyncio.Semaphore(2)  # max 2 pages Playwright en parallèle
    logger.info("[leboncoin] Contexte Playwright persistant prêt")


async def _get_pw_context():
    """Retourne le contexte Playwright, le réinitialise si nécessaire."""
    ctx = _pw.get("context")
    if ctx and not ctx.is_closed():
        return ctx
    async with _pw_lock:
        ctx = _pw.get("context")
        if ctx and not ctx.is_closed():
            return ctx
        logger.info("[leboncoin] Réinitialisation contexte Playwright…")
        await _init_pw_context()
        return _pw["context"]


async def warm_up_playwright() -> None:
    """Pré-charge le contexte Playwright au démarrage du serveur."""
    try:
        await asyncio.wait_for(_get_pw_context(), timeout=40)
        logger.info("[leboncoin] Contexte Playwright prêt au démarrage")
    except Exception as e:
        logger.warning(f"[leboncoin] Warmup Playwright échoué : {e}")


def _extraire_cv(motorisation: str) -> Optional[int]:
    if not motorisation:
        return None
    m = re.search(r'(\d{2,4})\s*(?:cv|ch|hp|bhp)', motorisation, re.IGNORECASE)
    if m:
        return int(m.group(1))
    nums = re.findall(r'\b(\d{2,4})\b', motorisation)
    candidates = [int(n) for n in nums if 50 <= int(n) <= 600]
    return candidates[-1] if candidates else None


def _extraire_code_moteur(motorisation: str) -> Optional[str]:
    """Extrait le code moteur alphanumérique pour affiner le keyword LBC."""
    if not motorisation:
        return None
    # BMW/Mercedes/Audi style: 3-4 chiffres + lettre(s) (318d, 320i, 330e, C220d, Q2 30…)
    m = re.search(r'\b(\d{3,4}[dDiIeE]{1,2})\b', motorisation)
    if m:
        return m.group(1).lower()
    # Codes moteur complets — ordre : du plus spécifique au plus générique
    m = re.search(
        r'\b('
        # VW/Audi/Skoda
        r'TFSI|TDI|TDCI|TSI|ETSI|'
        # Peugeot/Citroën/DS/Opel
        r'PURETECH|PURE TECH|BLUEHDI|BLUE HDI|CDTI|HDI|'
        # Renault/Dacia
        r'SCE|TCE|DCI|BLUE DCI|E-TECH|'
        # Toyota/Lexus
        r'HSD|TNGA|'
        # Ford/Mazda
        r'ECOBOOST|SKYACTIV|'
        # Divers
        r'MHEV|PHEV|THP|GTI|GTE|GTD|GTS|SDTi|CRDI|'
        # Hybride Renault (sans tiret)
        r'ETECH'
        r')\b',
        motorisation, re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    return None


def _extraire_displacement(motorisation: str) -> Optional[str]:
    """Extrait la cylindrée (ex: '1.5', '2.0') depuis une version Caradisiac."""
    m = re.search(r'\b(\d\.\d)\b', motorisation)
    return m.group(1) if m else None


API_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"
SEARCH_URL = "https://www.leboncoin.fr/recherche"


def _lbc_code(s: str) -> str:
    """Normalise une chaîne en code LBC : majuscules, sans accents, espaces → underscores."""
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r'[\s\-]+', '_', s).upper()


def _build_search_url(marque: str, modele: str, annee: int, carburant: str = None,
                       boite: str = None, motorisation: str = None,
                       type_vehicule: str = None, kilometrage: int = None) -> str:
    """Construit l'URL de recherche LBC avec les paramètres structurés voiture."""
    import urllib.parse

    FUEL_MAP = {
        "essence": "1", "sp95": "1", "sp98": "1",
        "diesel": "2", "gazole": "2",
        "hybride": "3",
        "electrique": "4", "électrique": "4",
        "gpl": "5",
    }
    # LBC gearbox : 1 = manuelle, 2 = automatique
    GEAR_CODE = {
        "manuelle": "1", "mécanique": "1", "mecanique": "1", "bvm": "1", "bm": "1",
        "automatique": "2", "auto": "2", "bva": "2", "dsg": "2", "edr": "2",
    }

    is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    brand_code = _lbc_code(marque)
    model_code = f"{brand_code}_{_lbc_code(modele)}"

    params: dict = {
        "category": "5" if is_util else "2",
        "u_car_brand": brand_code,
        "u_car_model": model_code,
        "regdate": f"{annee}-{annee}",
        "sort": "price",
        "order": "asc",
    }

    if carburant:
        fuel = FUEL_MAP.get(carburant.lower().strip())
        if fuel:
            params["fuel"] = fuel

    if boite:
        gear = GEAR_CODE.get(boite.lower().strip())
        if gear:
            params["gearbox"] = gear

    if motorisation:
        hp = _extraire_cv(motorisation)
        if hp:
            params["horse_power_din"] = f"{hp}-{hp}"

    if kilometrage:
        margin = 15_000 if kilometrage <= 100_000 else 25_000
        params["mileage"] = f"{max(0, kilometrage - margin)}-{kilometrage + margin}"

    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"

def _lbc_finition_code(marque: str, modele: str, finition: str) -> str:
    """Construit le code finition LBC : AUDI_Q2_Design, VOLKSWAGEN_GOLF_GTI, etc."""
    import unicodedata as _ud
    def _title(s):
        s = _ud.normalize("NFD", s)
        s = "".join(c for c in s if _ud.category(c) != "Mn")
        return "_".join(w.capitalize() for w in re.split(r'[\s\-]+', s.strip()) if w)
    brand_code = _lbc_code(marque)
    model_code = f"{brand_code}_{_lbc_code(modele)}"
    fin_code = _title(finition)
    return f"{model_code}_{fin_code}"


def _build_structured_payload(marque: str, modele: str, annee: int,
                               carburant: str = None, boite: str = None,
                               target_hp: int = None, kilometrage: int = None,
                               type_vehicule: str = None, finition: str = None,
                               page: int = 1) -> dict:
    """Payload finder/search identique à l'URL LBC (u_car_brand, u_car_model, u_car_finition, hp exact)."""
    FUEL_MAP = {
        "essence": "1", "sp95": "1", "sp98": "1",
        "diesel": "2", "gazole": "2",
        "hybride": "3",
        "electrique": "4", "électrique": "4",
        "gpl": "5",
    }
    GEAR_CODE = {
        "manuelle": "1", "mécanique": "1", "mecanique": "1", "bvm": "1", "bm": "1",
        "automatique": "2", "auto": "2", "bva": "2", "dsg": "2", "edr": "2",
    }
    is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    brand_code = _lbc_code(marque)
    model_code = f"{brand_code}_{_lbc_code(modele)}"

    enums: dict = {"ad_type": ["offer"], "u_car_brand": [brand_code], "u_car_model": [model_code]}
    if finition:
        # finition est déjà un code LBC encodé (ex: "AUDI_Q2_Design") — pas de re-encodage
        enums["u_car_finition"] = [finition]
        logger.info(f"[leboncoin] u_car_finition={finition}")
    if carburant:
        fuel = FUEL_MAP.get(carburant.lower().strip())
        if fuel:
            enums["fuel"] = [fuel]
    if boite:
        gear = GEAR_CODE.get(boite.lower().strip())
        if gear:
            enums["gearbox"] = [gear]

    ranges: dict = {}
    if annee:
        ranges["regdate"] = {"min": annee - 1, "max": annee + 1}
    if kilometrage:
        km_base = round(kilometrage / 10_000) * 10_000
        ranges["mileage"] = {"min": max(0, km_base - 10_000), "max": km_base + 10_000}
    if target_hp:
        # HP exact comme l'URL LBC (116-116), pas ±5
        ranges["horse_power_din"] = {"min": target_hp, "max": target_hp}

    return {
        "filters": {
            "category": {"id": "5" if is_util else "2"},
            "enums": enums,
            "ranges": ranges,
        },
        "limit": 35,
        "offset": 35 * (page - 1),
        "sort_by": "price",
        "sort_order": "asc",
        "disable_total": True,
        "extend": True,
        "listing_source": "direct-search" if page == 1 else "pagination",
    }


_WEBSHARE_HOST = "p.webshare.io:80"
_WEBSHARE_USER = "lmgdmysu"
_WEBSHARE_PASS = "nomkg04o6fsd"
_WEBSHARE_COUNTRIES = ["fr", "de", "gb", "nl", "be", "es"]

def _webshare_proxies() -> dict:
    country = random.choice(_WEBSHARE_COUNTRIES)
    session = random.randint(1, 99999)
    proxy = f"http://{_WEBSHARE_USER}-{country}-{session}:{_WEBSHARE_PASS}@{_WEBSHARE_HOST}"
    return {"http": proxy, "https": proxy}

def _camoufox_proxy() -> dict:
    country = random.choice(["fr", "de", "gb", "nl", "be"])
    session = random.randint(1, 99999)
    return {
        "server": f"http://{_WEBSHARE_HOST}",
        "username": f"{_WEBSHARE_USER}-{country}-{session}",
        "password": _WEBSHARE_PASS,
    }


def _mobile_ua() -> tuple[str, str, dict]:
    if random.choice([True, False]):
        ios = random.choice(["18.3", "18.4", "17.6"])
        lbc = random.choice(["101.50.0", "101.49.1", "101.48.0"])
        device_id = str(uuid.uuid4()).upper()
        ua = f"LBC;iOS;{ios};{random.choice(['iPhone15,2','iPhone15,3','iPhone14,2'])};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "ios",
            "api_key": "ba0c2dad52b3ec",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
        return ua, "safari18_0_ios", headers
    else:
        lbc = random.choice(["101.50.0", "101.49.1"])
        model = random.choice(["Pixel 8", "SM-G991B", "SM-S918B"])
        device_id = uuid.uuid4().hex[:16].upper()
        ua = f"LBC;Android;{random.choice(['13','14'])};{model};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "android",
            "api_key": "ba0c2dad52b3ec",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
        return ua, "chrome131_android", headers


def _build_lbc_payload(marque, modele, annee, km, page=1, carburant=None, boite=None,
                       type_vehicule=None, target_hp=None) -> dict:
    FUEL_MAP = {
        "essence": "1", "sp95": "1", "sp98": "1",
        "diesel": "2", "gazole": "2",
        "hybride": "3",
        "electrique": "4", "électrique": "4",
        "gpl": "5", "gnv": "6",
    }
    GEAR_MAP = {
        "mecanique": "1", "mécanique": "1", "manuelle": "1", "bvm": "1", "bm": "1",
        "automatique": "2", "auto": "2", "bva": "2", "dsg": "2", "edr": "2",
    }
    enums: dict = {"ad_type": ["offer"]}
    if carburant:
        fuel = FUEL_MAP.get(carburant.lower().strip())
        if fuel:
            enums["fuel"] = [fuel]
    if boite:
        gear = GEAR_MAP.get(boite.lower().strip())
        if gear:
            enums["gearbox"] = [gear]
    is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    cat_id = "5" if is_util else "2"
    # Pas de filtre km dans l'API — LBC l'ignore souvent et retourne
    # des voitures hors plage. Le filtrage km est fait post-hoc dans _extract_prix.
    ranges: dict = {
        "regdate": {"min": annee, "max": annee},
    }
    if target_hp:
        ranges["horse_power_din"] = {"min": target_hp - 5, "max": target_hp + 5}
    keyword_modele = re.sub(r'\bsportback\b', '', modele, flags=re.IGNORECASE).strip()
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
            "keywords": {"text": f"{marque} {keyword_modele}"},
            "ranges": ranges,
        },
        "limit": 35,
        "limit_alu": 3,
        "offset": 35 * (page - 1),
        "disable_total": True,
        "extend": True,
        "listing_source": "direct-search" if page == 1 else "pagination",
    }


def _km_bas_pour_age(km: int, annee: int) -> bool:
    """Vrai si le km est anormalement bas pour l'âge du véhicule.
    Base : 15 000 km/an. Si km < 50% du km attendu → pas de filtre km."""
    age = max(1, datetime.date.today().year - annee)
    return km < age * 15_000 * 0.5


def _build_camoufox_payload(marque, modele, annee, km, boite=None,
                             type_vehicule=None, target_hp=None) -> dict:
    """Payload optimisé pour le navigateur camoufox — gearbox numérique, km ±10k, année ±1.
    Si km anormalement bas pour l'âge → pas de filtre km (marché de niche)."""
    GEAR_NUM = {
        "mecanique": "1", "mécanique": "1", "manuelle": "1", "bvm": "1", "bm": "1", "manual": "1",
        "automatique": "2", "auto": "2", "bva": "2", "dsg": "2", "edr": "2", "automatic": "2",
    }
    is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    cat_id = "5" if is_util else "2"
    enums: dict = {"ad_type": ["offer"]}
    if boite:
        gear = GEAR_NUM.get(boite.lower().strip())
        if gear:
            enums["gearbox"] = [gear]
    ranges: dict = {"regdate": {"min": annee - 1, "max": annee}}
    if _km_bas_pour_age(km, annee):
        logger.info(f"[leboncoin] Km bas pour l'âge ({km} km / {annee}) — filtre km désactivé")
    elif km > 200_000:
        # Peu d'annonces >200k km sur LBC — élargir pour trouver des résultats
        ranges["mileage"] = {"min": max(0, km - 40_000), "max": km + 40_000}
    elif km > 150_000:
        ranges["mileage"] = {"min": max(0, km - 20_000), "max": km + 20_000}
    else:
        ranges["mileage"] = {"min": max(0, km - 10_000), "max": km + 10_000}
    if target_hp:
        ranges["horse_power_din"] = {"min": target_hp - 5, "max": target_hp + 5}
    keyword_modele = re.sub(r'\bsportback\b', '', modele, flags=re.IGNORECASE).strip()
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
            "keywords": {"text": f"{marque} {keyword_modele}"},
            "ranges": ranges,
        },
        "limit": 35,
        "limit_alu": 3,
        "offset": 0,
        "disable_total": False,
        "extend": True,
        "listing_source": "direct-search",
    }


_FUEL_LABELS = {
    "diesel": ["diesel", "gazole"],
    "essence": ["essence", "petrol", "sp95", "sp98"],
    "hybride": ["hybride", "hybrid"],
    "electrique": ["electrique", "électrique", "electric"],
    "gpl": ["gpl", "lpg"],
}
_FUEL_NUMERIC = {"1": "essence", "2": "diesel", "3": "hybride", "4": "electrique", "5": "gpl"}
_GEAR_LABELS = {
    "manual": ["manuelle", "manual", "mécanique", "mecanique", "bvm"],
    "automatic": ["automatique", "automatic", "auto", "bva", "dsg"],
}

def _match_fuel(attr_val: str, carburant: str) -> bool:
    v = attr_val.strip()
    # Codes numériques LBC (fuel=1=essence, 2=diesel, …)
    if v in _FUEL_NUMERIC:
        return _FUEL_NUMERIC[v] == carburant.lower().strip()
    labels = _FUEL_LABELS.get(carburant.lower(), [carburant.lower()])
    return any(lbl in v.lower() for lbl in labels)

def _match_gear(attr_val: str, boite: str) -> bool:
    v = attr_val.strip()
    boite_norm = boite.lower().replace("mécanique", "mecanique").replace("é", "e")
    # Codes numériques LBC (gearbox=1=manuelle, 2=automatique)
    if v == "1":
        return boite_norm in _GEAR_LABELS["manual"]
    if v == "2":
        return boite_norm in _GEAR_LABELS["automatic"]
    v = v.lower()
    for gear_key, labels in _GEAR_LABELS.items():
        if boite_norm in labels or boite_norm == gear_key:
            return any(lbl in v for lbl in labels)
    return boite_norm in v


_PROBLEM_KEYWORDS = [
    "moteur hs", "moteur h.s", "moteur défaillant", "moteur defaillant",
    "problème moteur", "probleme moteur", "casse moteur",
    "pour pièces", "pour pieces", "pour piece", "a la casse",
    "accidenté", "accidente", "epave", "épave",
    "à réparer", "a reparer", "ne demarre pas", "ne démarre pas",
    "hors service", "à démonter", "a demonter",
]


_CARROSSERIE_KEYWORDS: dict[str, list[str]] = {
    "break":      ["sw", "break", "touring", "estate", "combi", "avant", "sport tourer", "wagon"],
    "coupé":      ["coup", "coupé"],
    "cabriolet":  ["cabrio", "décap", "spider", "roadster", "convertible"],
    "suv / 4x4":  ["suv", "4x4", "crossover"],
    "monospace":  ["monospace", " van ", "7 pl", "7pl", "mpv"],
}


def _extract_prix(ads: list, modele: str, marque: str = None, carburant: str = None,
                   boite: str = None, target_hp: int = None, km_cible: int = None,
                   finition: str = None, carrosserie: str = None) -> list[int]:
    modele_lower = (modele or "").lower()
    marque_lower = (marque or "").lower()
    finition_lower = (finition or "").lower()
    VARIANTS = ["stepway", "stepway 2", "rs", "sport", "gt"]
    # Ne pas exclure un variant si la finition demandée le contient (ex: Mustang + finition GT)
    exclude = [v for v in VARIANTS if v not in modele_lower and v not in finition_lower]
    is_coupe_search = "coup" in modele_lower.replace("é", "e")
    prix = []
    for ad in ads:
        title = (ad.get("subject") or ad.get("title") or ad.get("body") or "").lower().replace("é", "e").replace("è", "e").replace("ê", "e")
        if any(v in title for v in exclude):
            continue
        if any(kw in title for kw in _PROBLEM_KEYWORDS):
            logger.debug(f"[leboncoin] Exclu (problème): {ad.get('subject', '')[:60]}")
            continue
        # Si on cherche un Coupé, exclure les SUV standard (et vice versa)
        if is_coupe_search and "coup" not in title.replace("é", "e"):
            continue
        if not is_coupe_search and "coup" in title.replace("é", "e") and "suv" not in title and modele_lower in ["glc", "gle", "q3", "q5"]:
            continue

        attrs = {a["key"]: (a.get("value_label") or a.get("value") or "")
                 for a in ad.get("attributes", [])}

        # Vérification stricte marque + modèle depuis les attributs LBC
        if marque_lower:
            brand_attr = str(attrs.get("brand", "")).lower()
            if brand_attr and marque_lower not in brand_attr and brand_attr not in marque_lower:
                continue
        if modele_lower:
            model_attr = str(attrs.get("model", "")).lower()
            # "Autres" = LBC fourre-tout pour les sous-versions (ex: GLC 300e) — on fait confiance au keyword
            if model_attr and model_attr not in ("autres", "other"):
                # Normaliser espaces/tirets pour gérer "rav4" vs "rav 4", "a3" vs "a 3", etc.
                modele_norm = modele_lower.replace(" ", "").replace("-", "")
                model_attr_norm = model_attr.replace(" ", "").replace("-", "")
                if modele_norm not in model_attr_norm and model_attr_norm not in modele_norm:
                    continue
                # Distinguer Sportback / non-Sportback strictement
                is_sportback_search = "sportback" in modele_lower
                ad_is_sportback = "sportback" in model_attr
                if is_sportback_search and not ad_is_sportback:
                    continue
                if not is_sportback_search and ad_is_sportback:
                    continue

        if carburant:
            fuel_val = str(attrs.get("fuel", ""))
            if fuel_val and not _match_fuel(fuel_val, carburant):
                continue
        if boite:
            gear_val = str(attrs.get("gearbox", ""))
            if gear_val and not _match_gear(gear_val, boite):
                continue

        # Filtre km post-hoc — LBC API ignore souvent le filtre mileage
        if km_cible is not None:
            # km peut être dans les attributs ou directement sur l'annonce
            mileage_raw = (
                attrs.get("mileage") or attrs.get("km") or
                ad.get("mileage") or ad.get("kilometrage") or ""
            )
            try:
                ad_km = int(re.sub(r"[^\d]", "", str(mileage_raw))) if mileage_raw else None
            except (ValueError, TypeError):
                ad_km = None
            if ad_km is not None:
                # Tolérance plus serrée pour véhicules très kilométrés pour éviter de mixer
                # avec des voitures nettement moins kilométrées qui faussent la médiane vers le haut
                if km_cible > 150_000:
                    km_tolerance = max(25_000, int(km_cible * 0.13))
                else:
                    km_tolerance = max(50_000, int(km_cible * 0.25))
                if abs(ad_km - km_cible) > km_tolerance:
                    continue

        # Filtre HP post-hoc (filet de sécurité si le filtre API laisse passer des cas limites)
        if target_hp:
            hp_raw = attrs.get("horse_power_din") or attrs.get("power") or ""
            try:
                hp = int(re.sub(r"[^\d]", "", str(hp_raw))) if hp_raw else None
            except (ValueError, TypeError):
                hp = None
            if hp and abs(hp - target_hp) > 3:
                continue

        raw = ad.get("price", [])
        p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
        if p and 500 <= int(p) <= 150_000:
            prix.append((title, int(p)))

    # Filtre finition (soft): si >= 5 annonces mentionnent la finition, on garde seulement celles-là
    if finition and prix:
        fin_norm = finition.lower().replace("-", " ").replace("_", " ")
        fin_words = [w for w in fin_norm.split() if len(w) > 2]
        if fin_words:
            filtered = [p for t, p in prix if all(w in t for w in fin_words)]
            if len(filtered) >= 5:
                logger.info(f"[leboncoin] Filtre finition '{finition}': {len(filtered)}/{len(prix)} annonces")
                return filtered
            else:
                logger.info(f"[leboncoin] Finition '{finition}' trop peu d'annonces ({len(filtered)}) → pas de filtre")

    # Filtre carrosserie (soft): si >= 3 annonces matchent, on filtre
    if carrosserie and prix:
        car_key = carrosserie.lower().strip()
        keywords = _CARROSSERIE_KEYWORDS.get(car_key)
        if keywords:
            filtered = [p for t, p in prix if any(kw in t for kw in keywords)]
            if len(filtered) >= 3:
                logger.info(f"[leboncoin] Filtre carrosserie '{carrosserie}': {len(filtered)}/{len(prix)} annonces")
                return filtered
            else:
                logger.info(f"[leboncoin] Carrosserie '{carrosserie}' trop peu d'annonces ({len(filtered)}) → pas de filtre")

    return [p for _, p in prix]


def _extract_annonces(ads: list, modele: str, marque: str = None, carburant: str = None,
                       boite: str = None, target_hp: int = None, km_cible: int = None,
                       finition: str = None, carrosserie: str = None,
                       structured: bool = False) -> list[dict]:
    """Même filtrage que _extract_prix mais retourne les détails complets de chaque annonce.
    structured=True : LBC a déjà filtré via enums — on saute les filtres post-hoc redondants."""
    modele_lower = (modele or "").lower()
    annonces = []
    for ad in ads:
        titre_original = (ad.get("subject") or ad.get("title") or ad.get("body") or "")
        title = titre_original.lower().replace("é", "e").replace("è", "e").replace("ê", "e")

        # Toujours exclure les voitures accidentées/HS
        if any(kw in title for kw in _PROBLEM_KEYWORDS):
            continue

        attrs = {a["key"]: (a.get("value_label") or a.get("value") or "")
                 for a in ad.get("attributes", [])}

        if not structured:
            marque_lower = (marque or "").lower()
            finition_lower = (finition or "").lower()
            VARIANTS = ["stepway", "stepway 2", "rs", "sport", "gt"]
            exclude = [v for v in VARIANTS if v not in modele_lower and v not in finition_lower]
            is_coupe_search = "coup" in modele_lower.replace("é", "e")

            if any(v in title for v in exclude):
                continue
            if is_coupe_search and "coup" not in title.replace("é", "e"):
                continue
            if not is_coupe_search and "coup" in title.replace("é", "e") and "suv" not in title and modele_lower in ["glc", "gle", "q3", "q5"]:
                continue

            if marque_lower:
                brand_attr = str(attrs.get("brand", "")).lower()
                if brand_attr and marque_lower not in brand_attr and brand_attr not in marque_lower:
                    continue
            if modele_lower:
                model_attr = str(attrs.get("model", "")).lower()
                if model_attr and model_attr not in ("autres", "other"):
                    modele_norm = modele_lower.replace(" ", "").replace("-", "")
                    model_attr_norm = model_attr.replace(" ", "").replace("-", "")
                    if modele_norm not in model_attr_norm and model_attr_norm not in modele_norm:
                        continue
                    is_sportback_search = "sportback" in modele_lower
                    ad_is_sportback = "sportback" in model_attr
                    if is_sportback_search and not ad_is_sportback:
                        continue
                    if not is_sportback_search and ad_is_sportback:
                        continue

            if carburant:
                fuel_val = str(attrs.get("fuel", ""))
                if fuel_val and not _match_fuel(fuel_val, carburant):
                    continue
            if boite:
                gear_val = str(attrs.get("gearbox", ""))
                if gear_val and not _match_gear(gear_val, boite):
                    continue

            if km_cible is not None:
                mileage_raw = (
                    attrs.get("mileage") or attrs.get("km") or
                    ad.get("mileage") or ad.get("kilometrage") or ""
                )
                try:
                    ad_km_check = int(re.sub(r"[^\d]", "", str(mileage_raw))) if mileage_raw else None
                except (ValueError, TypeError):
                    ad_km_check = None
                if ad_km_check is not None:
                    if km_cible > 150_000:
                        km_tolerance = max(25_000, int(km_cible * 0.13))
                    else:
                        km_tolerance = max(50_000, int(km_cible * 0.25))
                    if abs(ad_km_check - km_cible) > km_tolerance:
                        continue

            if target_hp:
                hp_raw = attrs.get("horse_power_din") or attrs.get("power") or ""
                try:
                    hp = int(re.sub(r"[^\d]", "", str(hp_raw))) if hp_raw else None
                except (ValueError, TypeError):
                    hp = None
                if hp and abs(hp - target_hp) > 3:
                    continue

        # Extraire km pour les détails (même en mode structured)
        mileage_raw = (
            attrs.get("mileage") or attrs.get("km") or
            ad.get("mileage") or ad.get("kilometrage") or ""
        )
        try:
            ad_km = int(re.sub(r"[^\d]", "", str(mileage_raw))) if mileage_raw else None
        except (ValueError, TypeError):
            ad_km = None

        raw = ad.get("price", [])
        p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
        if p and 500 <= int(p) <= 150_000:
            list_id = ad.get("list_id", "")
            url = ad.get("url") or (f"https://www.leboncoin.fr/voitures/{list_id}.htm" if list_id else "")
            owner = ad.get("owner", {})
            vendeur_type = "pro" if str(owner.get("type", "")).lower() in ("pro", "professional") else "particulier"
            vendeur_nom = owner.get("name") or owner.get("store_name") or ""
            pub_date_str = ad.get("first_publication_date") or ad.get("index_date") or ""
            age_jours = None
            if pub_date_str:
                try:
                    import datetime as _dt
                    pub_date = _dt.datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    age_jours = ((_dt.datetime.now(_dt.timezone.utc) - pub_date).days)
                except Exception:
                    pass
            images = ad.get("images", {}) or {}
            image_url = (
                images.get("thumb_url") or images.get("small_url") or
                images.get("url") or ad.get("thumb_url") or ""
            )
            annonces.append({
                "prix": int(p),
                "km": ad_km,
                "titre": titre_original,
                "url": url,
                "image_url": image_url,
                "vendeur_type": vendeur_type,
                "vendeur_nom": vendeur_nom,
                "age_jours": age_jours,
            })

    if not structured:
        # Filtre finition soft
        if finition and annonces:
            fin_norm = finition.lower().replace("-", " ").replace("_", " ")
            fin_words = [w for w in fin_norm.split() if len(w) > 2]
            if fin_words:
                filtered = [a for a in annonces if all(w in a["titre"].lower() for w in fin_words)]
                if len(filtered) >= 5:
                    annonces = filtered

        # Filtre carrosserie soft
        if carrosserie and annonces:
            car_key = carrosserie.lower().strip()
            keywords = _CARROSSERIE_KEYWORDS.get(car_key)
            if keywords:
                filtered = [a for a in annonces if any(kw in a["titre"].lower() for kw in keywords)]
                if len(filtered) >= 3:
                    annonces = filtered

    return annonces


async def _fetch_structured_api_pages(marque, modele, annee, kilometrage,
                                       carburant=None, boite=None, target_hp=None,
                                       type_vehicule=None, finition=None, carrosserie=None,
                                       lbc_finition=None, max_pages=10, return_details=False) -> list:
    """Appelle finder/search avec les enums structurés LBC via curl_cffi (bypass DataDome).
    Plus précis que la recherche par mots-clés, parcourt jusqu'à max_pages pages."""
    ua, impersonate, headers = _mobile_ua()
    proxies = _webshare_proxies()
    all_results = []
    extract_args = dict(marque=marque, carburant=carburant, boite=boite,
                        target_hp=target_hp, km_cible=kilometrage,
                        finition=finition, carrosserie=carrosserie)
    async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
        await s.get(HOMEPAGE, headers=headers, timeout=15)
        for pg in range(1, max_pages + 1):
            payload = _build_structured_payload(
                marque, modele, annee,
                carburant=carburant, boite=boite, target_hp=target_hp,
                kilometrage=kilometrage, type_vehicule=type_vehicule,
                finition=lbc_finition, page=pg,
            )
            r = await s.post(API_URL, json=payload, headers=headers, timeout=30)
            if r.status_code == 403:
                logger.warning(f"[leboncoin] structured API 403 page {pg}")
                break
            if not r.ok:
                logger.warning(f"[leboncoin] structured API {r.status_code} page {pg}")
                break
            ads = r.json().get("ads", [])
            logger.info(f"[leboncoin] structured API page {pg} → {len(ads)} annonces brutes")
            if return_details:
                page_results = _extract_annonces(ads, modele, structured=True, **extract_args)
            else:
                page_results = _extract_prix(ads, modele, **extract_args)
            all_results.extend(page_results)
            if len(ads) < 35:
                break  # dernière page
    # Dedup par URL (évite les doublons entre pages)
    if return_details:
        seen = set()
        deduped = []
        for r in all_results:
            key = r.get("url") or r.get("titre", "") + str(r.get("prix", ""))
            if key and key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped
    return all_results


class LeboncoinScraper(BaseScraper):
    name = "leboncoin"

    async def _fetch_mobile_api(self, marque, modele, annee, km, page,
                                 carburant=None, boite=None, type_vehicule=None,
                                 target_hp=None, finition=None, carrosserie=None,
                                 return_details: bool = False):
        ua, impersonate, headers = _mobile_ua()
        base = _build_camoufox_payload(marque, modele, annee, km, boite=boite,
                                        type_vehicule=type_vehicule, target_hp=target_hp)
        payload = {**base, "offset": 35 * (page - 1),
                   "listing_source": "direct-search" if page == 1 else "pagination"}
        proxies = _webshare_proxies()
        async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
            await s.get(HOMEPAGE, headers=headers, timeout=15)
            r = await s.post(API_URL, json=payload, headers=headers, timeout=30)
        if r.status_code == 403:
            raise Exception("DataDome 403")
        if not r.ok:
            raise Exception(f"API {r.status_code}")
        ads = r.json().get("ads", [])
        if return_details:
            return _extract_annonces(ads, modele, marque=marque, carburant=carburant,
                                     boite=boite, target_hp=target_hp, km_cible=km,
                                     finition=finition, carrosserie=carrosserie)
        return _extract_prix(ads, modele, marque=marque,
                             carburant=carburant, boite=boite, target_hp=target_hp, km_cible=km,
                             finition=finition, carrosserie=carrosserie)

    async def _search_via_context(self, payload: dict, modele: str, marque: str = None,
                                   carburant: str = None, boite: str = None,
                                   target_hp: int = None, km_cible: int = None,
                                   finition: str = None, carrosserie: str = None) -> list[int]:
        """Recherche LBC via le contexte Playwright persistant — DataDome natif, filtre HP fiable."""
        global _pw_sem
        if _pw_sem is None:
            _pw_sem = asyncio.Semaphore(2)

        for attempt in range(2):
            try:
                ctx = await _get_pw_context()
                async with _pw_sem:
                    page = await ctx.new_page()
                    try:
                        result = await asyncio.wait_for(
                            page.evaluate(
                                """async ([payload, url]) => {
                                    try {
                                        const r = await fetch(url, {
                                            method: "POST",
                                            credentials: "include",
                                            headers: {
                                                "Content-Type": "application/json",
                                                "Accept": "application/json",
                                                "api_key": "ba0c2dad52b3ec"
                                            },
                                            body: JSON.stringify(payload)
                                        });
                                        const data = await r.json();
                                        return {status: r.status, ads: data.ads || []};
                                    } catch(e) {
                                        return {status: 0, ads: [], error: String(e)};
                                    }
                                }""",
                                [payload, API_URL],
                            ),
                            timeout=20,
                        )
                        status = result.get("status", 0)
                        ads = result.get("ads", [])
                        logger.info(f"[leboncoin] Context status={status} → {len(ads)} annonces brutes")
                        if status == 403:
                            _pw["context"] = None  # force réinitialisation
                            if attempt == 0:
                                continue
                            return []
                        return _extract_prix(ads, modele, marque=marque, carburant=carburant,
                                            boite=boite, target_hp=target_hp, km_cible=km_cible,
                                            finition=finition, carrosserie=carrosserie)
                    finally:
                        await page.close()
            except Exception as e:
                logger.warning(f"[leboncoin] Context search erreur (attempt {attempt}): {e}")
                _pw["context"] = None
                if attempt == 0:
                    continue
                return []
        return []

    async def _playwright_search(self, marque, modele, annee, km,
                                  carburant=None, boite=None, type_vehicule=None,
                                  target_hp=None, finition=None, carrosserie=None) -> list[int]:
        """Appel API LBC depuis un vrai contexte Playwright — contourne DataDome."""
        payload = _build_lbc_payload(marque, modele, annee, km, 1,
                                      carburant=carburant, boite=boite,
                                      type_vehicule=type_vehicule, target_hp=target_hp)
        logger.info(f"[leboncoin] Playwright fallback — payload: {_json.dumps(payload)[:200]}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="fr-FR",
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()
            try:
                await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=20_000)
                result = await page.evaluate(
                    """async (payload) => {
                        const r = await fetch("https://api.leboncoin.fr/finder/search", {
                            method: "POST",
                            credentials: "include",
                            headers: {
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                                "api_key": "ba0c2dad52b3ec"
                            },
                            body: JSON.stringify(payload)
                        });
                        return {status: r.status, data: await r.json()};
                    }""",
                    payload,
                )
                logger.info(f"[leboncoin] Playwright status={result['status']}")
                if result["status"] != 200:
                    return []
                ads = result["data"].get("ads", [])
                prix = _extract_prix(ads, modele, marque=marque, carburant=carburant,
                                     boite=boite, target_hp=target_hp, km_cible=km,
                                     finition=finition, carrosserie=carrosserie)
                logger.info(f"[leboncoin] Playwright → {len(prix)} prix")
                return prix
            except Exception as e:
                logger.error(f"[leboncoin] Playwright erreur: {e}")
                return []
            finally:
                await browser.close()

    async def _camoufox_search(self, marque, modele, annee, km,
                                carburant=None, boite=None, type_vehicule=None,
                                target_hp=None, finition=None, carrosserie=None) -> list[int]:
        """Camoufox + proxy résidentiel — filtre km natif (±10k), boite numérique."""
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            raise Exception("camoufox non installé")

        proxy = _camoufox_proxy()
        payload = _build_camoufox_payload(marque, modele, annee, km, boite=boite,
                                           type_vehicule=type_vehicule, target_hp=target_hp)
        logger.info(f"[leboncoin] Camoufox payload: {_json.dumps(payload)[:200]}")

        async with AsyncCamoufox(
            headless=True,
            proxy=proxy,
            geoip=True,
            locale="fr-FR",
            os="windows",
        ) as browser:
            page = await browser.new_page()
            try:
                await page.goto(HOMEPAGE, wait_until="commit", timeout=20_000)
                await asyncio.sleep(random.uniform(0.4, 0.8))

                result = await page.evaluate(
                    """async ([payload, url]) => {
                        try {
                            const r = await fetch(url, {
                                method: "POST",
                                credentials: "include",
                                headers: {
                                    "Content-Type": "application/json",
                                    "Accept": "application/json",
                                    "api_key": "ba0c2dad52b3ec",
                                    "Origin": "https://www.leboncoin.fr",
                                    "Referer": "https://www.leboncoin.fr/"
                                },
                                body: JSON.stringify(payload)
                            });
                            const data = await r.json();
                            return {status: r.status, ads: data.ads || []};
                        } catch(e) {
                            return {status: 0, ads: [], error: e.toString()};
                        }
                    }""",
                    [payload, API_URL]
                )

                status = result.get("status")
                ads = result.get("ads", [])
                logger.info(f"[leboncoin] Camoufox status={status} → {len(ads)} annonces brutes")

                if status != 200:
                    raise Exception(f"Camoufox API {status}")

                prix = _extract_prix(ads, modele, marque=marque, carburant=carburant,
                                     boite=boite, target_hp=target_hp, km_cible=km,
                                     finition=finition, carrosserie=carrosserie)
                logger.info(f"[leboncoin] Camoufox → {len(prix)} prix filtrés")
                return prix
            finally:
                await page.close()

    async def _url_search(self, marque, modele, annee, kilometrage,
                           carburant=None, boite=None, motorisation=None,
                           type_vehicule=None, finition=None, carrosserie=None,
                           max_pages: int = 10, return_details: bool = False) -> list:
        """Navigue sur l'URL LBC, intercepte le payload API page 1,
        puis pagine jusqu'à max_pages en réutilisant ce payload avec offset."""
        global _pw_sem
        if _pw_sem is None:
            _pw_sem = asyncio.Semaphore(2)

        target_hp = _extraire_cv(motorisation) if motorisation else None
        url = _build_search_url(marque, modele, annee, carburant=carburant, boite=boite,
                                 motorisation=motorisation, type_vehicule=type_vehicule,
                                 kilometrage=kilometrage)
        logger.info(f"[leboncoin] URL search: {url}")

        for attempt in range(2):
            try:
                ctx = await _get_pw_context()
                async with _pw_sem:
                    page = await ctx.new_page()
                    try:
                        # Capturer le payload exact utilisé par LBC pour page 1
                        first_payload: dict = {}

                        def on_request(req):
                            if "finder/search" in req.url and req.method == "POST":
                                try:
                                    first_payload.update(_json.loads(req.post_data or "{}"))
                                except Exception:
                                    pass

                        page.on("request", on_request)

                        # Naviguer page 1 et intercepter la réponse
                        async with page.expect_response(
                            lambda r: "finder/search" in r.url and r.status == 200,
                            timeout=40_000,
                        ) as resp_info:
                            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)

                        page.remove_listener("request", on_request)

                        resp = await resp_info.value
                        data = await resp.json()
                        all_ads = list(data.get("ads", []))
                        logger.info(f"[leboncoin] Page 1 → {len(all_ads)} annonces")

                        # Pages 2…max_pages via fetch() depuis le contexte Playwright
                        for pg in range(2, max_pages + 1):
                            if len(data.get("ads", [])) < 35:
                                break  # moins de 35 résultats → dernière page
                            payload_next = {
                                **first_payload,
                                "offset": 35 * (pg - 1),
                                "listing_source": "pagination",
                                "disable_total": True,
                            }
                            result = await asyncio.wait_for(
                                page.evaluate(
                                    """async ([payload, apiUrl]) => {
                                        try {
                                            const r = await fetch(apiUrl, {
                                                method: "POST",
                                                credentials: "include",
                                                headers: {
                                                    "Content-Type": "application/json",
                                                    "Accept": "application/json",
                                                    "api_key": "ba0c2dad52b3ec"
                                                },
                                                body: JSON.stringify(payload)
                                            });
                                            const d = await r.json();
                                            return {status: r.status, ads: d.ads || []};
                                        } catch(e) {
                                            return {status: 0, ads: []};
                                        }
                                    }""",
                                    [payload_next, API_URL],
                                ),
                                timeout=15,
                            )
                            page_ads = result.get("ads", [])
                            logger.info(f"[leboncoin] Page {pg} → {len(page_ads)} annonces")
                            all_ads.extend(page_ads)
                            data = result  # pour vérifier len() à la prochaine itération
                            if not page_ads:
                                break

                        logger.info(f"[leboncoin] URL search total → {len(all_ads)} annonces brutes")
                        extract_args = dict(marque=marque, carburant=carburant, boite=boite,
                                            target_hp=target_hp, km_cible=kilometrage,
                                            finition=finition, carrosserie=carrosserie)
                        if return_details:
                            return _extract_annonces(all_ads, modele, **extract_args)
                        return _extract_prix(all_ads, modele, **extract_args)
                    finally:
                        await page.close()
            except Exception as e:
                logger.warning(f"[leboncoin] URL search erreur (attempt {attempt}): {e}")
                _pw["context"] = None
                if attempt == 0:
                    continue
                return []
        return []

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2,
                          finition=None, carburant=None, boite=None,
                          motorisation=None, type_vehicule=None, carrosserie=None):
        target_hp = _extraire_cv(motorisation) if motorisation else None
        engine_code = _extraire_code_moteur(motorisation) if motorisation else None
        modele_api = re.sub(r'\bsportback\b', '', modele, flags=re.IGNORECASE).strip()

        # kw_args pour _search_via_context uniquement (inclut marque)
        ctx_args = dict(marque=marque, carburant=carburant, boite=boite,
                        type_vehicule=type_vehicule, finition=finition, carrosserie=carrosserie)

        async def _mobile_pages(mod, km, hp):
            """mod = modele (keyword LBC), km = km cible, hp = target_hp ou None."""
            prix = []
            for pg in range(1, max_pages + 1):
                try:
                    p = await self._fetch_mobile_api(
                        marque, mod, annee, km, pg,
                        carburant=carburant, boite=boite,
                        type_vehicule=type_vehicule, target_hp=hp,
                        finition=finition, carrosserie=carrosserie,
                    )
                    prix.extend(p)
                    if not p:
                        break
                except Exception as e:
                    logger.debug(f"[leboncoin] _mobile_pages erreur pg{pg}: {e}")
                    break
            return prix

        # ── 1. URL search via Playwright (motorisation dans le texte) ───────────
        if motorisation:
            logger.info("[leboncoin] URL search (Playwright)")
            try:
                prix = await asyncio.wait_for(
                    self._url_search(marque, modele_api, annee, kilometrage,
                                     carburant=carburant, boite=boite, motorisation=motorisation,
                                     type_vehicule=type_vehicule, finition=finition,
                                     carrosserie=carrosserie),
                    timeout=30,
                )
            except Exception:
                prix = []
            if prix:
                return prix

        # ── 2. Mobile API avec HP ─────────────────────────────────────────────
        logger.info("[leboncoin] Mobile API (avec HP)")
        try:
            prix = await asyncio.wait_for(_mobile_pages(modele_api, kilometrage, target_hp), timeout=22)
        except Exception:
            prix = []
        if prix:
            return prix

        # ── 3. Contexte Playwright avec HP (DataDome natif → filtre HP fiable) ─
        if target_hp:
            logger.info("[leboncoin] Context Playwright avec HP")
            payload_hp = _build_lbc_payload(marque, modele_api, annee, kilometrage, 1,
                                             carburant=carburant, boite=boite,
                                             type_vehicule=type_vehicule, target_hp=target_hp)
            try:
                prix = await asyncio.wait_for(
                    self._search_via_context(payload_hp, modele_api, km_cible=kilometrage, **ctx_args),
                    timeout=25,
                )
            except Exception as e:
                logger.warning(f"[leboncoin] Context HP erreur: {e}")
                prix = []
            if prix:
                logger.info(f"[leboncoin] Context HP → {len(prix)} prix")
                return prix

        # ── 4. Engine code retry (BMW 318d, VW GTI…) ─────────────────────────
        if target_hp and engine_code and engine_code.lower() not in modele_api.lower():
            modele_engine = f"{modele_api} {engine_code}"
            logger.info(f"[leboncoin] Retry code moteur '{modele_engine}'")
            try:
                prix = await asyncio.wait_for(_mobile_pages(modele_engine, kilometrage, None), timeout=22)
            except Exception:
                prix = []
            if prix:
                return prix

        # ── 5. Mobile API SANS HP (élargissement) ────────────────────────────
        if target_hp:
            logger.info("[leboncoin] Retry Mobile sans HP")
            try:
                prix = await asyncio.wait_for(_mobile_pages(modele_api, kilometrage, None), timeout=22)
            except Exception:
                prix = []
            if prix:
                return prix

        # ── 6. Contexte Playwright SANS HP ────────────────────────────────────
        logger.info("[leboncoin] Context Playwright sans HP")
        payload_no_hp = _build_lbc_payload(marque, modele_api, annee, kilometrage, 1,
                                            carburant=carburant, boite=boite,
                                            type_vehicule=type_vehicule, target_hp=None)
        try:
            prix = await asyncio.wait_for(
                self._search_via_context(payload_no_hp, modele_api, km_cible=kilometrage, **ctx_args),
                timeout=25,
            )
        except Exception as e:
            logger.warning(f"[leboncoin] Context sans HP erreur: {e}")
            prix = []
        if prix:
            logger.info(f"[leboncoin] Context sans HP → {len(prix)} prix")
            return prix

        # ── 7. Retry km=50k (véhicule rare ou km atypique) ───────────────────
        km_retry = 50_000
        if kilometrage != km_retry:
            logger.info(f"[leboncoin] Retry km={km_retry}")
            try:
                prix = await asyncio.wait_for(_mobile_pages(modele_api, km_retry, None), timeout=22)
            except Exception:
                prix = []
            if prix:
                return prix

        logger.warning(f"[leboncoin] Aucun résultat pour {marque} {modele} {annee}")
        return []

    async def get_listings(self, marque, modele, annee, kilometrage,
                            finition=None, carburant=None, boite=None,
                            motorisation=None, type_vehicule=None, carrosserie=None) -> list[dict]:
        """Retourne la liste complète des annonces LBC avec prix, km, titre et url.
        Utilise l'API structurée (curl_cffi, bypass DataDome) avec les enums u_car_brand/u_car_model."""
        modele_api = re.sub(r'\bsportback\b', '', modele, flags=re.IGNORECASE).strip()
        target_hp = _extraire_cv(motorisation) if motorisation else None
        # Extraire finition depuis motorisation si pas fournie explicitement
        # Ex: "30 TDI 116 DESIGN" → "DESIGN" | "30 TFSI 116 S LINE PLUS" → "S LINE PLUS"
        if not finition and motorisation:
            m = re.match(r'^\d+\s+\S+\s+\d+\s+(.+)$', motorisation.strip(), re.IGNORECASE)
            if m:
                finition = m.group(1).strip()
        # Code finition LBC ex: "AUDI_Q2_Design" — transmis si finition connue
        lbc_fin = _lbc_finition_code(marque, modele_api, finition) if finition else None

        # ── Recherche structurée via curl_cffi (bypass DataDome, enums précis) ───
        logger.info(f"[leboncoin] get_listings → structured API hp={target_hp} finition={lbc_fin}")
        try:
            listings = await asyncio.wait_for(
                _fetch_structured_api_pages(
                    marque, modele_api, annee, kilometrage,
                    carburant=carburant, boite=boite, target_hp=target_hp,
                    type_vehicule=type_vehicule, finition=finition, carrosserie=carrosserie,
                    lbc_finition=lbc_fin, max_pages=10, return_details=True,
                ),
                timeout=160,
            )
        except Exception as e:
            logger.warning(f"[leboncoin] structured API erreur: {e}")
            listings = []

        if listings:
            return listings

        # Fallback : mobile API keyword (km + titre + url disponibles)
        logger.info("[leboncoin] get_listings fallback → mobile API details")
        all_listings: list[dict] = []
        for pg in range(1, 4):
            try:
                page_listings = await asyncio.wait_for(
                    self._fetch_mobile_api(
                        marque, modele_api, annee, kilometrage, pg,
                        carburant=carburant, boite=boite,
                        type_vehicule=type_vehicule, target_hp=target_hp,
                        finition=finition, carrosserie=carrosserie,
                        return_details=True,
                    ),
                    timeout=35,
                )
                all_listings.extend(page_listings)
                if not page_listings:
                    break
            except Exception as e:
                logger.debug(f"[leboncoin] mobile details pg{pg}: {e}")
                break

        if all_listings:
            return all_listings

        # Dernier recours : prix bruts sans détails
        logger.info("[leboncoin] get_listings dernier recours → get_prices")
        prices = await self.get_prices(
            marque, modele, annee, kilometrage,
            finition=finition, carburant=carburant, boite=boite,
            motorisation=motorisation, type_vehicule=type_vehicule, carrosserie=carrosserie,
        )
        return [{"prix": p, "km": None, "titre": "", "url": "", "vendeur_type": "particulier", "vendeur_nom": "", "age_jours": None} for p in prices]

    async def _scrape(self, context: BrowserContext, marque, modele, annee, kilometrage,
                       max_pages, finition=None) -> list[int]:
        return []
