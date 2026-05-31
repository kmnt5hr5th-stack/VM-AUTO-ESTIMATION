import logging
import uuid
import random
from typing import Optional
from curl_cffi.requests import AsyncSession
from playwright.async_api import BrowserContext

from .base import BaseScraper
from ._proxy import LBC_PROXY_URL

logger = logging.getLogger(__name__)

API_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"


def _mobile_ua() -> tuple[str, str]:
    if random.choice([True, False]):
        ua = (
            f"LBC;iOS;{random.choice(['18.3', '18.4', '26.0', '26.2'])};"
            f"iPhone;phone;{str(uuid.uuid4()).upper()};wifi;"
            f"{random.choice(['101.44.0', '101.45.0', '101.43.1'])}"
        )
        return ua, "safari_ios"
    else:
        model = random.choice(["Pixel 8", "Pixel 7", "SM-G991B", "SM-S918B"])
        ua = (
            f"LBC;Android;{random.choice(['13', '14'])};"
            f"{model};phone;{uuid.uuid4().hex[:16].upper()};wifi;"
            f"{random.choice(['100.85.2', '100.84.1'])}"
        )
        return ua, "chrome_android"


class LeboncoinScraper(BaseScraper):
    name = "leboncoin"

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

    def _build_payload(self, marque, modele, annee, km, page=1, carburant=None, boite=None, type_vehicule=None) -> dict:
        enums: dict = {"ad_type": ["offer"]}
        if carburant:
            fuel = self.FUEL_MAP.get(carburant.lower().strip())
            if fuel:
                enums["fuel"] = [fuel]
        if boite:
            gear = self.GEAR_MAP.get(boite.lower().strip())
            if gear:
                enums["gearbox"] = [gear]
        is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
        cat_id = "5" if is_util else "2"
        return {
            "filters": {
                "category": {"id": cat_id},
                "enums": enums,
                "keywords": {"text": f"{marque} {modele}"},
                "ranges": {
                    "regdate": {"min": annee - 1, "max": annee + 1},
                    "mileage": {"min": max(0, km - 10_000), "max": km + 10_000},
                },
            },
            "limit": 35,
            "limit_alu": 3,
            "offset": 35 * (page - 1),
            "disable_total": True,
            "extend": True,
            "listing_source": "direct-search" if page == 1 else "pagination",
        }

    async def _fetch_mobile_api(self, marque, modele, annee, km, page, carburant=None, boite=None, type_vehicule=None) -> list[int]:
        ua, impersonate = _mobile_ua()
        headers = {
            "User-Agent": ua,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
        payload = self._build_payload(marque, modele, annee, km, page, carburant, boite, type_vehicule)

        async with AsyncSession(impersonate=impersonate) as s:
            await s.get(HOMEPAGE, headers=headers, timeout=15)
            r = await s.post(API_URL, json=payload, headers=headers, timeout=30)

        if r.status_code == 403:
            raise Exception("DataDome 403")
        if not r.ok:
            raise Exception(f"API {r.status_code}")

        ads = r.json().get("ads", [])
        prix = []
        for ad in ads:
            raw = ad.get("price", [])
            p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
            if p and 500 <= int(p) <= 150_000:
                prix.append(int(p))
        return prix

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None, carburant=None, boite=None, motorisation=None, type_vehicule=None):
        if LBC_PROXY_URL:
            logger.info(f"[leboncoin] Via lbc-proxy (Webshare)")
            try:
                payload = {"marque": marque, "modele": modele, "annee": annee,
                           "kilometrage": kilometrage, "max_pages": max_pages,
                           "carburant": carburant, "boite": boite, "type_vehicule": type_vehicule}
                async with AsyncSession(impersonate="chrome131") as s:
                    r = await s.post(f"{LBC_PROXY_URL}/leboncoin", json=payload, timeout=90)
                if r.ok:
                    prix = r.json().get("prix", [])
                    logger.info(f"[leboncoin] lbc-proxy: {len(prix)} prix")
                    if prix:
                        return prix
            except Exception as e:
                logger.warning(f"[leboncoin] lbc-proxy échoué: {e}")

        logger.info("[leboncoin] Tentative API mobile directe")
        try:
            prix = []
            for page_num in range(1, max_pages + 1):
                page_prices = await self._fetch_mobile_api(marque, modele, annee, kilometrage, page_num, carburant, boite, type_vehicule)
                logger.info(f"[leboncoin] API p{page_num} → {len(page_prices)} prix")
                prix.extend(page_prices)
                if not page_prices:
                    break
            return prix
        except Exception as e:
            logger.warning(f"[leboncoin] API mobile échouée: {e}")
            return []

    async def _scrape(self, context: BrowserContext, marque, modele, annee, kilometrage, max_pages, finition=None) -> list[int]:
        return []
