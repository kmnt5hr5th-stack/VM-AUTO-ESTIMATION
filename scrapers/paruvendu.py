import logging
import re
from typing import Optional
from urllib.parse import urlencode
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte

logger = logging.getLogger(__name__)


class ParuVenduScraper(BaseScraper):
    name = "paruvendu"

    def _build_url(self, marque: str, modele: str, annee: int, km: int, page: int = 1, finition: Optional[str] = None) -> str:
        text = f"{marque} {modele}"
        if finition:
            text += f" {finition}"
        km_delta = 40_000 if km > 150_000 else 20_000
        params = {
            "q": text,
            "annee_min": annee - 1,
            "annee_max": annee + 1,
            "km_max": km + km_delta,
            "pa": page,
        }
        return f"https://www.paruvendu.fr/auto-moto-bateau/voitures/?{urlencode(params)}"

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None):
        logger.info(f"[paruvendu] Recherche {marque} {modele} {annee} {kilometrage}km")
        prix: list[int] = []

        for page_num in range(1, max_pages + 1):
            url = self._build_url(marque, modele, annee, kilometrage, page_num, finition)
            logger.info(f"[paruvendu] URL p{page_num}: {url}")

            try:
                async with AsyncSession(impersonate="chrome131") as s:
                    r = await s.get(url, timeout=30)

                logger.info(f"[paruvendu] p{page_num} status={r.status_code} len={len(r.text)}")
                if r.status_code != 200:
                    break

                prix_page = self._parse_html(r.text)
                logger.info(f"[paruvendu] p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)
                if not prix_page:
                    break

            except Exception as e:
                logger.error(f"[paruvendu] Erreur p{page_num}: {e}")
                break

        return prix

    def _parse_html(self, html: str) -> list[int]:
        prix: set[int] = set()
        soup = BeautifulSoup(html, "lxml")

        # Sélecteurs spécifiques ParuVendu
        for el in soup.find_all(class_=re.compile(r"\bprix\b|\bprice\b", re.I)):
            for v in extraire_prix_texte(el.get_text(" ", strip=True)):
                prix.add(v)
        if prix:
            return list(prix)

        for el in soup.find_all(attrs={"itemprop": "price"}):
            content = el.get("content", "") or el.get_text(" ", strip=True)
            for v in extraire_prix_texte(content):
                prix.add(v)
        if prix:
            return list(prix)

        # Fallback regex sur tout le texte
        for v in extraire_prix_texte(soup.get_text(" ")):
            prix.add(v)
        return list(prix)

    async def _scrape(
        self,
        context: BrowserContext,
        marque: str,
        modele: str,
        annee: int,
        kilometrage: int,
        max_pages: int,
        finition: Optional[str] = None,
    ) -> list[int]:
        return []
