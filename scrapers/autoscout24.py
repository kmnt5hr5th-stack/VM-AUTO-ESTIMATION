import logging
from typing import Optional
from urllib.parse import quote_plus
from playwright.async_api import BrowserContext

from .base import BaseScraper, extraire_prix_texte

logger = logging.getLogger(__name__)


class AutoScout24Scraper(BaseScraper):
    name = "autoscout24"

    def _build_url(self, marque: str, modele: str, annee: int, kilometrage: int, page: int = 1, finition: Optional[str] = None) -> str:
        m = marque.lower().replace(" ", "-")
        mo = modele.lower().replace(" ", "-")
        km_delta = 20_000
        km_min = max(0, kilometrage - km_delta)
        km_max = kilometrage + km_delta
        url = (
            f"https://www.autoscout24.fr/lst/{m}/{mo}"
            f"?atype=C&cy=F"
            f"&fregfrom={annee - 1}&fregto={annee + 1}"
            f"&kmfrom={km_min}&kmto={km_max}"
            f"&sort=standard&ustate=N%2CU&page={page}"
        )
        if finition:
            url += f"&q={quote_plus(finition)}"
        return url

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
        prix: list[int] = []

        page = await context.new_page()
        try:
            for page_num in range(1, max_pages + 1):
                url = self._build_url(marque, modele, annee, kilometrage, page_num, finition)
                logger.info(f"[autoscout24] URL p{page_num}: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Accepter les cookies (première page seulement)
                if page_num == 1:
                    try:
                        await page.click(
                            "button[data-testid='as24-cmp-accept-all-button']",
                            timeout=5_000,
                        )
                        logger.info("[autoscout24] Cookies acceptés")
                    except Exception:
                        pass

                # Attendre que les annonces soient rendues
                try:
                    await page.wait_for_selector("article", timeout=10_000)
                except Exception:
                    logger.warning(f"[autoscout24] Aucun article détecté p{page_num}")
                    break

                await page.wait_for_timeout(1_500)

                # Sélecteur principal : prix dans les cartes annonces
                prix_page: list[int] = []
                elements = await page.query_selector_all("[data-testid='regular-price']")
                for el in elements:
                    texte = await el.inner_text()
                    prix_page.extend(extraire_prix_texte(texte))

                # Fallback : sélecteurs alternatifs
                if not prix_page:
                    for sel in ["[class*='Price_price']", "[class*='price__']"]:
                        elements = await page.query_selector_all(sel)
                        for el in elements:
                            texte = await el.inner_text()
                            prix_page.extend(extraire_prix_texte(texte))
                        if prix_page:
                            break

                logger.info(f"[autoscout24] Page {page_num} : {len(prix_page)} prix → {prix_page[:5]}")
                prix.extend(prix_page)

                if not prix_page:
                    break

        except Exception as e:
            logger.error(f"[autoscout24] Erreur : {e}")
        finally:
            await page.close()

        return prix
