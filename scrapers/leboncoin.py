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
    ranges: dict = {
        "regdate": {"min": annee - 1, "max": annee + 1},
        "mileage": {"min": max(0, km - 15_000), "max": km + 15_000},
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


def _extract_prix(ads: list, modele: str) -> list[int]:
    modele_lower = (modele or "").lower()
    VARIANTS = ["stepway", "stepway 2", "rs", "sport", "gt"]
    exclude = [v for v in VARIANTS if v not in modele_lower]
    prix = []
    for ad in ads:
        title = ad.get("subject", "").lower()
        if any(v in title for v in exclude):
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
        # Mobile API: pas de filtres carburant/boite pour éviter les blocages DataDome
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
        return _extract_prix(r.json().get("ads", []), modele)

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
                prix = _extract_prix(ads, modele)
                logger.info(f"[leboncoin] Playwright → {len(prix)} prix")
                return prix
            except Exception as e:
                logger.error(f"[leboncoin] Playwright erreur: {e}")
                return []
            finally:
                await browser.close()

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2,
                          finition=None, carburant=None, boite=None,
                          motorisation=None, type_vehicule=None):
        target_hp = _extraire_cv(motorisation) if motorisation else None

        # 1. API mobile directe (rapide, sans filtres carburant/boite)
        logger.info("[leboncoin] Tentative API mobile directe")
        try:
            prix = []
            for page_num in range(1, max_pages + 1):
                page_prices = await self._fetch_mobile_api(
                    marque, modele, annee, kilometrage, page_num,
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

        # 2. Playwright fallback (vrai navigateur, tous filtres)
        logger.info("[leboncoin] Playwright fallback")
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
