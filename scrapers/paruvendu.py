import logging
import re
from typing import Optional
from urllib.parse import urlencode
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
        km_delta = 10_000
        params = {
            "q": text,
            "annee_min": annee - 1,
            "annee_max": annee + 1,
            "km_min": max(0, km - km_delta),
            "km_max": km + km_delta,
            "p": page,
        }
        return f"https://www.paruvendu.fr/voiture-occasion/?{urlencode(params)}"

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
                logger.info(f"[paruvendu] URL p{page_num}: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Accepter les cookies si présent
                if page_num == 1:
                    for selector in ["button#acceptAll", "button:has-text('Tout accepter')", "button:has-text('Accepter')"]:
                        try:
                            await page.click(selector, timeout=3_000)
                            break
                        except Exception:
                            pass

                # Attendre le chargement des annonces
                try:
                    await page.wait_for_selector(
                        ".annonce, .listing-item, [class*='annonce'], [class*='listing'], .car-item",
                        timeout=8_000,
                    )
                except Exception:
                    logger.warning(f"[paruvendu] Pas d'annonces p{page_num}")

                await page.wait_for_timeout(2_000)

                html = await page.content()
                prix_page = self._parse_html(html)
                logger.info(f"[paruvendu] p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)

                if not prix_page:
                    break

        except Exception as e:
            logger.error(f"[paruvendu] Erreur: {e}")
        finally:
            await page.close()

        return prix

    def _parse_html(self, html: str) -> list[int]:
        prix: set[int] = set()
        soup = BeautifulSoup(html, "lxml")

        # Sélecteurs ParuVendu
        for el in soup.find_all(attrs={"itemprop": "price"}):
            content = el.get("content", "") or el.get_text(" ", strip=True)
            for v in extraire_prix_texte(content + " €"):
                prix.add(v)
        if prix:
            return list(prix)

        for el in soup.find_all(class_=re.compile(r"\bprix\b", re.I)):
            for v in extraire_prix_texte(el.get_text(" ", strip=True)):
                prix.add(v)
        if prix:
            return list(prix)

        # Fallback regex sur tout le texte
        for v in extraire_prix_texte(soup.get_text(" ")):
            prix.add(v)
        return list(prix)
