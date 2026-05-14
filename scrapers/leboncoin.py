import logging
import uuid
import random
from typing import Optional
from urllib.parse import urlencode, quote_plus
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte
from ._proxy import proxy_available, flaresolverr_available, build_url, service_name, FLARESOLVERR_URL

logger = logging.getLogger(__name__)

API_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"


def _mobile_ua() -> tuple[str, str]:
    """Retourne (User-Agent LBC mobile, browser à imiter)."""
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

    def _build_payload(self, marque: str, modele: str, annee: int, km: int, page: int = 1, finition: Optional[str] = None) -> dict:
        text = f"{marque} {modele}"
        if finition:
            text += f" {finition}"
        km_delta = 40_000 if km > 150_000 else 20_000
        return {
            "filters": {
                "category": {"id": "2"},
                "enums": {"ad_type": ["offer"]},
                "keywords": {"text": text},
                "ranges": {
                    "regdate": {"min": annee - 1, "max": annee + 1},
                    "mileage": {"min": max(0, km - km_delta), "max": km + km_delta},
                },
            },
            "limit": 35,
            "limit_alu": 3,
            "offset": 35 * (page - 1),
            "disable_total": True,
            "extend": True,
            "listing_source": "direct-search" if page == 1 else "pagination",
        }

    async def _fetch_mobile_api(self, marque: str, modele: str, annee: int, km: int, page: int, finition: Optional[str]) -> list[int]:
        ua, impersonate = _mobile_ua()
        headers = {
            "User-Agent": ua,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
        payload = self._build_payload(marque, modele, annee, km, page, finition)

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

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None):
        # 1) API mobile LeBonCoin — User-Agent LBC, pas de proxy requis
        logger.info("[leboncoin] Tentative API mobile (User-Agent LBC)...")
        try:
            prix = []
            for page_num in range(1, max_pages + 1):
                page_prices = await self._fetch_mobile_api(marque, modele, annee, kilometrage, page_num, finition)
                logger.info(f"[leboncoin] API p{page_num} → {len(page_prices)} prix : {page_prices[:5]}")
                prix.extend(page_prices)
                if not page_prices:
                    break
            if prix:
                return prix
            logger.warning("[leboncoin] API mobile: 0 résultats, fallback proxy")
        except Exception as e:
            logger.warning(f"[leboncoin] API mobile échouée ({e}), fallback proxy")

        # 2) Fallback proxy (FlareSolverr / ZenRows / ScraperAPI)
        if not proxy_available():
            logger.warning("[leboncoin] Aucun proxy configuré — source ignorée")
            return []

        logger.info(f"[leboncoin] Service proxy: {service_name()}")
        prix: list[int] = []

        for page_num in range(1, max_pages + 1):
            target = self._build_search_url(marque, modele, annee, kilometrage, finition)
            if page_num > 1:
                target += f"&page={page_num}"

            try:
                if flaresolverr_available():
                    html = await self._fetch_flaresolverr(target)
                else:
                    api_url = build_url(target, js_render=True)
                    async with AsyncSession(impersonate="chrome131") as s:
                        r = await s.get(api_url, timeout=90)
                    if r.status_code != 200:
                        break
                    html = r.text

                prix_page = self._parse_html(html)
                logger.info(f"[leboncoin] proxy p{page_num} → {len(prix_page)} prix")
                prix.extend(prix_page)
                if not prix_page:
                    break

            except Exception as e:
                logger.error(f"[leboncoin] Proxy erreur: {e}")
                break

        return prix

    def _build_search_url(self, marque: str, modele: str, annee: int, km: int, finition: Optional[str] = None) -> str:
        text = f"{marque} {modele}"
        if finition:
            text += f" {finition}"
        km_delta = 40_000 if km > 150_000 else 20_000
        params = {
            "category": "2",
            "text": text,
            "regdate_min": str(annee - 1),
            "regdate_max": str(annee + 1),
            "mileage_min": str(max(0, km - km_delta)),
            "mileage_max": str(km + km_delta),
            "price": "500-150000",
        }
        return f"https://www.leboncoin.fr/recherche?{urlencode(params, quote_via=quote_plus)}"

    async def _fetch_flaresolverr(self, url: str) -> str:
        async with AsyncSession(impersonate="chrome131") as s:
            r = await s.post(
                f"{FLARESOLVERR_URL}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
                timeout=70,
            )
        data = r.json()
        if data.get("status") != "ok":
            raise Exception(f"FlareSolverr: {data.get('message', data)}")
        return data["solution"]["response"]

    def _parse_html(self, html: str) -> list[int]:
        prix: set[int] = set()
        soup = BeautifulSoup(html, "lxml")
        for selector in [
            {"attrs": {"data-qa-id": "aditem_price"}},
            {"attrs": {"data-test-id": "price"}},
        ]:
            for el in soup.find_all(**selector):
                for v in extraire_prix_texte(el.get_text(" ", strip=True)):
                    prix.add(v)
            if prix:
                return list(prix)
        for v in extraire_prix_texte(soup.get_text(" ")):
            prix.add(v)
        return list(prix)

    async def _scrape(self, context: BrowserContext, marque, modele, annee, kilometrage, max_pages, finition=None) -> list[int]:
        return []
