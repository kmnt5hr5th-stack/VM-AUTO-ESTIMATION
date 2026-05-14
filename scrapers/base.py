import re
import logging
from abc import ABC, abstractmethod
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

PRIX_MIN = 500
PRIX_MAX = 150_000


def extraire_prix_texte(texte: str) -> list[int]:
    """Extrait tous les prix en euros.

    Formats gérés :
      "15 500 €"   (espace ordinaire ou \\xa0 avant €)
      "€ 13 490"   (€ en premier — format AutoScout24 avec espace fine \\u202f)
      "15.500 €"   (point comme séparateur de milliers)
    """
    # Normalise tous les types d'espaces insécables vers espace ordinaire
    t = texte.replace("\xa0", " ").replace(" ", " ").replace(" ", " ")

    prix: set[int] = set()

    # Format 1 : nombre AVANT €  →  "15 500 €" / "15500€"
    for m in re.findall(r"(\d{1,3}(?:[\s.]\d{3})*)\s*€", t):
        try:
            v = int(re.sub(r"[\s.]", "", m))
            if PRIX_MIN <= v <= PRIX_MAX:
                prix.add(v)
        except ValueError:
            pass

    # Format 2 : € AVANT le nombre  →  "€ 13 490" (AutoScout24)
    for m in re.findall(r"€\s*(\d{1,3}(?:[\s.]\d{3})*)", t):
        try:
            v = int(re.sub(r"[\s.]", "", m))
            if PRIX_MIN <= v <= PRIX_MAX:
                prix.add(v)
        except ValueError:
            pass

    return list(prix)


class BaseScraper(ABC):
    name: str = "base"

    async def get_prices(
        self,
        marque: str,
        modele: str,
        annee: int,
        kilometrage: int,
        max_pages: int = 2,
        finition: Optional[str] = None,
        carburant: Optional[str] = None,
    ) -> list[int]:
        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="fr-FR",
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            try:
                prices = await self._scrape(
                    context, marque, modele, annee, kilometrage, max_pages, finition
                )
            finally:
                await browser.close()
        return prices

    @abstractmethod
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
        ...
