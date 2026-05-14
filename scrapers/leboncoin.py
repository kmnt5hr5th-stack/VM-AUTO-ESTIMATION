import logging
import re
import asyncio
from typing import Optional
from urllib.parse import urlencode, quote_plus
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

from .base import BaseScraper, extraire_prix_texte
from ._proxy import proxy_available, flaresolverr_available, build_url, service_name, FLARESOLVERR_URL

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
        # 1) Playwright stealth — gratuit, sans limite
        logger.info("[leboncoin] Tentative Playwright stealth...")
        try:
            prices = await self._scrape_with_playwright(marque, modele, annee, kilometrage, max_pages, finition)
            if prices:
                logger.info(f"[leboncoin] Playwright: {len(prices)} prix")
                return prices
            logger.warning("[leboncoin] Playwright: 0 prix (DataDome actif?), fallback proxy")
        except Exception as e:
            logger.warning(f"[leboncoin] Playwright échoué: {e}, fallback proxy")

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

            logger.info(f"[leboncoin] Requête proxy p{page_num}…")
            try:
                if flaresolverr_available():
                    html = await self._fetch_flaresolverr(target)
                else:
                    api_url = build_url(target, js_render=True)
                    async with AsyncSession(impersonate="chrome131") as s:
                        r = await s.get(api_url, timeout=90)
                    if r.status_code != 200:
                        logger.error(f"[leboncoin] Erreur {r.status_code}: {r.text[:300]}")
                        break
                    html = r.text

                prix_page = self._parse_html(html)
                logger.info(f"[leboncoin] proxy p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                prix.extend(prix_page)
                if not prix_page:
                    break

            except Exception as e:
                logger.error(f"[leboncoin] Proxy erreur p{page_num}: {e}")
                break

        return prix

    async def _scrape_with_playwright(
        self,
        marque: str,
        modele: str,
        annee: int,
        kilometrage: int,
        max_pages: int,
        finition: Optional[str],
    ) -> list[int]:
        from playwright_stealth import stealth_async

        prix: list[int] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="fr-FR",
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
            )

            page = await context.new_page()
            await stealth_async(page)

            try:
                for page_num in range(1, max_pages + 1):
                    url = self._build_search_url(marque, modele, annee, kilometrage, finition)
                    if page_num > 1:
                        url += f"&page={page_num}"

                    logger.info(f"[leboncoin] Playwright p{page_num}: {url}")
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_timeout(2_000)

                    # Accepter les cookies Didomi
                    if page_num == 1:
                        for selector in [
                            "button#didomi-notice-agree-button",
                            "button[data-testid='didomi-notice-agree-button']",
                            "button:has-text('Tout accepter')",
                            "button:has-text('Accepter')",
                        ]:
                            try:
                                await page.click(selector, timeout=3_000)
                                await page.wait_for_timeout(1_000)
                                break
                            except Exception:
                                pass

                    # Détecter si DataDome a bloqué
                    current_url = page.url
                    title = await page.title()
                    if "datadome" in current_url.lower() or "blocked" in title.lower() or "captcha" in title.lower():
                        logger.warning(f"[leboncoin] DataDome détecté (url={current_url})")
                        break

                    # Attendre les annonces
                    try:
                        await page.wait_for_selector(
                            "[data-qa-id='aditem_container'], [data-test-id='ad'], article[data-qa-id]",
                            timeout=8_000,
                        )
                    except Exception:
                        logger.warning(f"[leboncoin] Aucune annonce détectée p{page_num}")
                        break

                    html = await page.content()
                    prix_page = self._parse_html(html)
                    logger.info(f"[leboncoin] Playwright p{page_num} → {len(prix_page)} prix : {prix_page[:5]}")
                    prix.extend(prix_page)

                    if not prix_page:
                        break

                    await page.wait_for_timeout(1_500)

            finally:
                await page.close()
                await browser.close()

        return prix

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
        html = data["solution"]["response"]
        logger.info(f"[leboncoin] FlareSolverr OK len={len(html)}")
        return html

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
