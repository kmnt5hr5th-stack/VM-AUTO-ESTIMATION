import logging
import json
import re
from urllib.parse import urlencode, quote
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte
from ._proxy import proxy_available, build_url, service_name

logger = logging.getLogger(__name__)


class LaCentraleScraper(BaseScraper):
    name = "lacentrale"

    def _build_url(self, marque: str, modele: str, annee: int, km: int) -> str:
        params = {
            "makesModelsCommercialNames": f"{marque.upper()}:{modele.upper()}",
            "yearMin": str(annee - 1),
            "yearMax": str(annee + 1),
            "mileageMin": str(max(0, km - 20_000)),
            "mileageMax": str(km + 20_000),
        }
        return f"https://www.lacentrale.fr/listing?{urlencode(params, quote_via=quote)}"

    async def get_prices(self, marque, modele, annee, kilometrage, max_pages=2, finition=None, carburant=None, boite=None, motorisation=None, type_vehicule=None):
        if not proxy_available():
            logger.warning("[lacentrale] Aucun proxy configuré (ZENROWS_KEY ou SCRAPERAPI_KEY) — source ignorée")
            return []

        logger.info(f"[lacentrale] Service: {service_name()}")
        prix: list[int] = []

        for page_num in range(1, max_pages + 1):
            target = self._build_url(marque, modele, annee, kilometrage)
            if page_num > 1:
                target += f"&page={page_num}"

            api_url = build_url(target, js_render=True)
            logger.info(f"[lacentrale] Requête p{page_num}…")

            try:
                async with AsyncSession(impersonate="chrome131") as s:
                    r = await s.get(api_url, timeout=30)

                logger.info(f"[lacentrale] p{page_num} status={r.status_code}  len={len(r.text)}")
                if r.status_code != 200:
                    logger.error(f"[lacentrale] Erreur {r.status_code}: {r.text[:300]}")
                    break

                prix_page = self._parse_html(r.text)
                logger.info(f"[lacentrale] p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)
                if not prix_page:
                    break

            except Exception as e:
                # La Centrale bloque les IPs proxy connues (ZenRows, ScraperAPI, etc.)
                # au niveau réseau → timeout immédiat sans données
                logger.warning(f"[lacentrale] Inaccessible via proxy ({type(e).__name__}) — source ignorée")
                break

        return prix

    def _parse_html(self, html: str) -> list[int]:
        prix: set[int] = set()
        soup = BeautifulSoup(html, "lxml")

        # 1) __NEXT_DATA__ SSR (La Centrale = Next.js)
        tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if tag and tag.string:
            try:
                raw = json.dumps(json.loads(tag.string))
                for m in re.findall(r'"price"\s*:\s*(\d+)', raw):
                    v = int(m)
                    if 500 <= v <= 150_000:
                        prix.add(v)
                if prix:
                    logger.info(f"[lacentrale] {len(prix)} prix via __NEXT_DATA__")
                    return list(prix)
            except json.JSONDecodeError:
                pass

        # 2) Sélecteurs CSS
        for els in [
            soup.find_all(class_=re.compile(r"(SearchCard_price|price)", re.I)),
            soup.find_all(attrs={"data-test": "ad-price"}),
        ]:
            for el in els:
                for v in extraire_prix_texte(el.get_text(" ", strip=True)):
                    prix.add(v)
            if prix:
                return list(prix)

        # 3) Fallback regex
        for v in extraire_prix_texte(soup.get_text(" ")):
            prix.add(v)
        logger.info(f"[lacentrale] {len(prix)} prix via regex fallback")
        return list(prix)

    async def _scrape(self, context: BrowserContext, *args, **kwargs) -> list[int]:
        return []
