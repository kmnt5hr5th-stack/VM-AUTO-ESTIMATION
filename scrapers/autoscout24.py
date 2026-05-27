import logging
import re
from typing import Optional
from urllib.parse import quote_plus
from curl_cffi.requests import AsyncSession
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte


def _extraire_cv(motorisation: str) -> Optional[int]:
    """Extrait les chevaux depuis '1.6 TDI 105CV', '130 ch', '1.2 PureTech 110'…"""
    m = re.search(r'(\d{2,4})\s*(?:cv|ch|hp|bhp)', motorisation, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Dernier nombre si aucune unité (ex: "1.2 PureTech 130")
    nums = re.findall(r'\b(\d{2,4})\b', motorisation)
    candidates = [int(n) for n in nums if 50 <= int(n) <= 600]
    return candidates[-1] if candidates else None

logger = logging.getLogger(__name__)


class AutoScout24Scraper(BaseScraper):
    name = "autoscout24"

    # Mapping carburant → code AutoScout24
    FUEL_MAP = {
        "diesel": "D", "gazole": "D",
        "essence": "B", "petrol": "B", "sp95": "B", "sp98": "B",
        "hybride": "H", "hybrid": "H",
        "electrique": "E", "électrique": "E", "electric": "E",
        "gpl": "L", "gnv": "G",
    }
    GEAR_MAP = {
        "mecanique": "M", "mécanique": "M", "manuelle": "M", "bvm": "M", "bm": "M",
        "automatique": "A", "auto": "A", "bva": "A", "dsg": "A", "edr": "A",
    }

    def _build_url(self, marque: str, modele: str, annee: int, kilometrage: int, page: int = 1, finition: Optional[str] = None, carburant: Optional[str] = None, boite: Optional[str] = None, motorisation: Optional[str] = None, type_vehicule: Optional[str] = None) -> str:
        m = marque.lower().replace(" ", "-")
        mo = modele.lower().replace(" ", "-")
        km_delta = 10_000
        km_min = max(0, kilometrage - km_delta)
        km_max = kilometrage + km_delta
        is_util = type_vehicule and type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
        atype = "V" if is_util else "C"
        url = (
            f"https://www.autoscout24.fr/lst/{m}/{mo}"
            f"?atype={atype}&cy=F"
            f"&fregfrom={annee - 1}&fregto={annee}"
            f"&kmfrom={km_min}&kmto={km_max}"
            f"&sort=standard&ustate=N%2CU&page={page}"
        )
        if carburant:
            fuel_code = self.FUEL_MAP.get(carburant.lower().strip())
            if fuel_code:
                url += f"&fuel={fuel_code}"
        if boite:
            gear_code = self.GEAR_MAP.get(boite.lower().strip())
            if gear_code:
                url += f"&gear={gear_code}"
        if motorisation:
            cv = _extraire_cv(motorisation)
            if cv:
                kw = round(cv * 0.7355)
                url += f"&powerFrom={max(1, kw - 11)}&powerTo={kw + 11}"
        if finition:
            url += f"&q={quote_plus(finition)}"
        return url

    def _parse_data_price(self, html: str) -> list[int]:
        """Extraction via data-price sur les vraies annonces (data-source=listpage_search-results)."""
        prices = []
        for m in re.findall(r'data-source="listpage_search-results"[^>]*?data-price="(\d+)"', html):
            v = int(m)
            if 500 <= v <= 150_000:
                prices.append(v)
        return prices

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None, carburant=None, boite=None, motorisation=None, type_vehicule=None):
        prix: list[int] = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        for page_num in range(1, max_pages + 1):
            url = self._build_url(marque, modele, annee, kilometrage, page_num, finition, carburant, boite, motorisation, type_vehicule)
            logger.info(f"[autoscout24] URL p{page_num}: {url}")

            try:
                async with AsyncSession(impersonate="chrome124") as s:
                    r = await s.get(url, headers=headers, timeout=20)

                if r.status_code != 200:
                    logger.warning(f"[autoscout24] HTTP {r.status_code} p{page_num}")
                    break

                prix_page = self._parse_data_price(r.text)
                logger.info(f"[autoscout24] p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)

                if not prix_page:
                    break

            except Exception as e:
                logger.error(f"[autoscout24] Erreur p{page_num}: {e}")
                break

        return prix

    async def _scrape(self, context: BrowserContext, marque, modele, annee, kilometrage, max_pages, finition=None) -> list[int]:
        return []
