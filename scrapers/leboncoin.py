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


def _extraire_cv(motorisation: str) -> Optional[int]:
    if not motorisation:
        return None
    m = re.search(r'(\d{2,4})\s*(?:cv|ch|hp|bhp)', motorisation, re.IGNORECASE)
    if m:
        return int(m.group(1))
    nums = re.findall(r'\b(\d{2,4})\b', motorisation)
    candidates = [int(n) for n in nums if 50 <= int(n) <= 600]
    return candidates[-1] if candidates else None


API_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"

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
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
        return ua, "chrome131_android", headers


def _build_lbc_payload(marque, modele, annee, km, page=1, carburant=None, boite=None,
                       type_vehicule=None, target_hp=None) -> dict:
    FUEL_MAP = {
        "diesel": "diesel", "gazole": "diesel",
        "essence": "petrol", "sp95": "petrol", "sp98": "petrol",
        "hybride": "hybrid", "hybrid": "hybrid",
        "electrique": "electric", "électrique": "electric",
        "gpl": "lpg", "gnv": "cng",
    }
    GEAR_MAP = {
        "mecanique": "manual", "mécanique": "manual", "manuelle": "manual", "bvm": "manual", "bm": "manual",
        "automatique": "automatic", "auto": "automatic", "bva": "automatic", "dsg": "automatic", "edr": "automatic",
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
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
            "keywords": {"text": f"{marque} {modele}"},
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
    ranges: dict = {"regdate": {"min": annee - 1, "max": annee + 1}}
    if _km_bas_pour_age(km, annee):
        logger.info(f"[leboncoin] Km bas pour l'âge ({km} km / {annee}) — filtre km désactivé")
    else:
        ranges["mileage"] = {"min": max(0, km - 10_000), "max": km + 10_000}
    if target_hp:
        ranges["horse_power_din"] = {"min": target_hp - 5, "max": target_hp + 5}
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
            "keywords": {"text": f"{marque} {modele}"},
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
_GEAR_LABELS = {
    "manual": ["manuelle", "manual", "mécanique", "mecanique", "bvm"],
    "automatic": ["automatique", "automatic", "auto", "bva", "dsg"],
}

def _match_fuel(attr_val: str, carburant: str) -> bool:
    v = attr_val.lower()
    labels = _FUEL_LABELS.get(carburant.lower(), [carburant.lower()])
    return any(lbl in v for lbl in labels)

def _match_gear(attr_val: str, boite: str) -> bool:
    v = attr_val.lower()
    boite_norm = boite.lower().replace("mécanique", "mecanique").replace("é", "e")
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


def _extract_prix(ads: list, modele: str, marque: str = None, carburant: str = None,
                   boite: str = None, target_hp: int = None, km_cible: int = None) -> list[int]:
    modele_lower = (modele or "").lower()
    marque_lower = (marque or "").lower()
    VARIANTS = ["stepway", "stepway 2", "rs", "sport", "gt"]
    exclude = [v for v in VARIANTS if v not in modele_lower]
    is_coupe_search = "coup" in modele_lower.replace("é", "e")
    prix = []
    for ad in ads:
        title = ad.get("subject", "").lower().replace("é", "e").replace("è", "e").replace("ê", "e")
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

        attrs = {a["key"]: a.get("value_label", a.get("value", ""))
                 for a in ad.get("attributes", [])}

        # Vérification stricte marque + modèle depuis les attributs LBC
        if marque_lower:
            brand_attr = str(attrs.get("brand", "")).lower()
            if brand_attr and marque_lower not in brand_attr and brand_attr not in marque_lower:
                continue
        if modele_lower:
            model_attr = str(attrs.get("model", "")).lower()
            # "Autres" = LBC fourre-tout pour les sous-versions (ex: GLC 300e) — on fait confiance au keyword
            if model_attr and model_attr not in ("autres", "other") and modele_lower not in model_attr and model_attr not in modele_lower:
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
            if hp and abs(hp - target_hp) > 20:
                continue

        raw = ad.get("price", [])
        p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
        if p and 500 <= int(p) <= 150_000:
            prix.append(int(p))
    return prix


class LeboncoinScraper(BaseScraper):
    name = "leboncoin"

    async def _fetch_mobile_api(self, marque, modele, annee, km, page,
                                 carburant=None, boite=None, type_vehicule=None,
                                 target_hp=None) -> list[int]:
        ua, impersonate, headers = _mobile_ua()
        # Pas de filtres carburant/boite dans le payload (DataDome bloque) — filtrage post-hoc
        payload = _build_lbc_payload(marque, modele, annee, km, page,
                                      carburant=None, boite=None,
                                      type_vehicule=type_vehicule, target_hp=target_hp)
        proxies = _webshare_proxies()
        async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
            await s.get(HOMEPAGE, headers=headers, timeout=15)
            r = await s.post(API_URL, json=payload, headers=headers, timeout=30)
        if r.status_code == 403:
            raise Exception("DataDome 403")
        if not r.ok:
            raise Exception(f"API {r.status_code}")
        return _extract_prix(r.json().get("ads", []), modele, marque=marque,
                             carburant=carburant, boite=boite, target_hp=target_hp, km_cible=km)

    async def _playwright_search(self, marque, modele, annee, km,
                                  carburant=None, boite=None, type_vehicule=None,
                                  target_hp=None) -> list[int]:
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
                            headers: {"Content-Type": "application/json", "Accept": "application/json"},
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
                                     boite=boite, target_hp=target_hp, km_cible=km)
                logger.info(f"[leboncoin] Playwright → {len(prix)} prix")
                return prix
            except Exception as e:
                logger.error(f"[leboncoin] Playwright erreur: {e}")
                return []
            finally:
                await browser.close()

    async def _camoufox_search(self, marque, modele, annee, km,
                                carburant=None, boite=None, type_vehicule=None,
                                target_hp=None) -> list[int]:
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
                await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=25_000)
                await asyncio.sleep(random.uniform(1.5, 2.5))

                result = await page.evaluate(
                    """async ([payload, url]) => {
                        try {
                            const r = await fetch(url, {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                    "Accept": "application/json",
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
                                     boite=boite, target_hp=target_hp, km_cible=km)
                logger.info(f"[leboncoin] Camoufox → {len(prix)} prix filtrés")
                return prix
            finally:
                await page.close()

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2,
                          finition=None, carburant=None, boite=None,
                          motorisation=None, type_vehicule=None):
        target_hp = _extraire_cv(motorisation) if motorisation else None

        # 1. Camoufox (nouveau) — proxy résidentiel, filtre km natif, boite correcte
        logger.info("[leboncoin] Tentative Camoufox")
        try:
            prix = await self._camoufox_search(
                marque, modele, annee, kilometrage,
                carburant=carburant, boite=boite,
                type_vehicule=type_vehicule, target_hp=target_hp,
            )
            if prix:
                return prix
            logger.info("[leboncoin] Camoufox → 0 prix, bascule sur API mobile")
        except Exception as e:
            logger.warning(f"[leboncoin] Camoufox échoué: {e}")

        # 2. API mobile directe (ancien système — conservé comme fallback)
        logger.info("[leboncoin] Fallback API mobile directe")
        try:
            prix = []
            for page_num in range(1, max_pages + 1):
                page_prices = await self._fetch_mobile_api(
                    marque, modele, annee, kilometrage, page_num,
                    carburant=carburant, boite=boite,
                    type_vehicule=type_vehicule, target_hp=target_hp,
                )
                logger.info(f"[leboncoin] API p{page_num} → {len(page_prices)} prix")
                prix.extend(page_prices)
                if not page_prices:
                    break
            if prix:
                return prix
        except Exception as e:
            logger.warning(f"[leboncoin] API mobile échouée: {e}")

        # 3. Playwright (ancien système — dernier recours)
        logger.info("[leboncoin] Fallback Playwright")
        try:
            prix = await self._playwright_search(
                marque, modele, annee, kilometrage,
                carburant=carburant, boite=boite,
                type_vehicule=type_vehicule, target_hp=target_hp,
            )
            return prix
        except Exception as e:
            logger.warning(f"[leboncoin] Playwright échoué: {e}")
            return []

    async def _scrape(self, context: BrowserContext, marque, modele, annee, kilometrage,
                       max_pages, finition=None) -> list[int]:
        return []
