import logging
import re
from typing import Optional
from urllib.parse import urlencode, quote_plus
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte
from ._proxy import proxy_available, build_url, service_name

logger = logging.getLogger(__name__)


class LeboncoinScraper(BaseScraper):
    name = "leboncoin"

    def _build_search_url(self, marque: str, modele: str, annee: int, km: int, finition: Optional[str] = None) -> str:
        text = f"{marque} {modele}"
        if finition:
            text += f" {finition}"
        params = {
            "category": "2",
            "text": text,
            "regdate_min": str(annee - 1),
            "regdate_max": str(annee + 1),
            "mileage_min": str(max(0, km - 20_000)),
            "mileage_max": str(km + 20_000),
            "price": "500-150000",
        }
        return f"https://www.leboncoin.fr/recherche?{urlencode(params, quote_via=quote_plus)}"

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None):
        if not proxy_available():
            logger.warning("[leboncoin] Aucun proxy configuré (ZENROWS_KEY ou SCRAPERAPI_KEY) — source ignorée")
            return []

        logger.info(f"[leboncoin] Service: {service_name()}")
        prix: list[int] = []

        for page_num in range(1, max_pages + 1):
            target = self._build_search_url(marque, modele, annee, kilometrage, finition)
            if page_num > 1:
                target += f"&page={page_num}"

            api_url = build_url(target, js_render=True)
            logger.info(f"[leboncoin] Requête p{page_num}…")

            try:
                async with AsyncSession(impersonate="chrome131") as s:
                    r = await s.get(api_url, timeout=90)

                logger.info(f"[leboncoin] p{page_num} status={r.status_code}  len={len(r.text)}")
                if r.status_code != 200:
                    logger.error(f"[leboncoin] Erreur {r.status_code}: {r.text[:300]}")
                    break

                prix_page = self._parse_html(r.text)
                logger.info(f"[leboncoin] p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)
                if not prix_page:
                    break

            except Exception as e:
                logger.error(f"[leboncoin] Erreur p{page_num}: {e}")
                break

        return prix

    def _parse_html(self, html: str) -> list[int]:
        prix: set[int] = set()
        soup = BeautifulSoup(html, "lxml")

        for selector, attr in [
            ({"attrs": {"data-qa-id": "aditem_price"}}, None),
            ({"attrs": {"data-test-id": "price"}}, None),
        ]:
            for el in soup.find_all(**selector):
                for v in extraire_prix_texte(el.get_text(" ", strip=True)):
                    prix.add(v)
            if prix:
                return list(prix)

        # Fallback : chercher toutes les occurrences de prix dans la page
        for v in extraire_prix_texte(soup.get_text(" ")):
            prix.add(v)
        return list(prix)

    async def _scrape(self, context: BrowserContext, *args, **kwargs) -> list[int]:
        return []
