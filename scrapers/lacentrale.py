import logging
import json
import re
import random
from typing import Optional
from urllib.parse import urlencode, quote
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext

from .base import BaseScraper, extraire_prix_texte
from ._proxy import proxy_available, build_url, service_name  # ZenRows/ScraperAPI

_WEBSHARE_HOST = "p.webshare.io:80"
_WEBSHARE_USER = "lmgdmysu"
_WEBSHARE_PASS = "nomkg04o6fsd"

def _webshare_proxy_url() -> str:
    session = random.randint(1000000, 9999999)
    return f"http://{_WEBSHARE_USER}-fr-{session}:{_WEBSHARE_PASS}@{_WEBSHARE_HOST}"

logger = logging.getLogger(__name__)


class LaCentraleScraper(BaseScraper):
    name = "lacentrale"

    def _build_url(self, marque: str, modele: str, annee: int, km: int) -> str:
        params = {
            "makesModelsCommercialNames": f"{marque.upper()}:{modele.upper()}",
            "yearMin": str(annee - 1),
            "yearMax": str(annee + 1),
            "mileageMin": str(max(0, km - 10_000)),
            "mileageMax": str(km + 10_000),
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
        return []  # La Centrale ne supporte pas Playwright

    # ─── Scan géographique par département ───────────────────────────────────

    async def scan_by_dept(self, dept_code: str, prix_max: int = 25000, km_max: int = 180000, max_pages: int = 5) -> list[dict]:
        """Scan La Centrale par département via Playwright + Webshare (bypass DataDome)."""
        listings: list[dict] = []
        seen_ids: set = set()

        proxy_url = _webshare_proxy_url()
        logger.info(f"[lacentrale-geo] Démarrage Playwright (dept={dept_code})")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    proxy={"server": f"http://{_WEBSHARE_HOST}", "username": f"{_WEBSHARE_USER}-fr-{random.randint(1000000,9999999)}", "password": _WEBSHARE_PASS},
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    locale="fr-FR",
                    extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = await context.new_page()

                try:
                    for page_num in range(1, max_pages + 1):
                        url = (
                            f"https://www.lacentrale.fr/listing"
                            f"?departmentIds[]={dept_code}"
                            f"&priceMax={prix_max}"
                            f"&mileageMax={km_max}"
                            f"&sortBy=priceAsc"
                        )
                        if page_num > 1:
                            url += f"&page={page_num}"

                        logger.info(f"[lacentrale-geo] p{page_num}: {url}")
                        await page.goto(url, wait_until="domcontentloaded", timeout=40_000)

                        html = await page.content()
                        page_listings = self._parse_listings_html(html)
                        new = [l for l in page_listings if l.get("external_id") not in seen_ids]
                        seen_ids.update(l["external_id"] for l in new if l.get("external_id"))
                        listings.extend(new)
                        logger.info(f"[lacentrale-geo] p{page_num}: {len(new)} nouvelles ({len(listings)} total)")

                        if not page_listings:
                            break
                finally:
                    await browser.close()

        except Exception as e:
            logger.error(f"[lacentrale-geo] Playwright erreur: {e}")

        return listings

    def _parse_listings_html(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not tag or not tag.string:
            logger.warning("[lacentrale-geo] __NEXT_DATA__ introuvable")
            return []
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            logger.warning("[lacentrale-geo] JSON invalide dans __NEXT_DATA__")
            return []

        results: list[dict] = []
        self._find_ad_objects(data, results, depth=0)
        return results

    def _find_ad_objects(self, obj, results: list, depth: int):
        if depth > 12:
            return
        if isinstance(obj, list):
            candidate_batch = []
            for item in obj:
                if isinstance(item, dict):
                    price = item.get("price") or item.get("prix")
                    has_make = any(k in item for k in ("make", "brand", "marque", "makeName"))
                    if price and has_make:
                        parsed = self._parse_lc_ad(item)
                        if parsed:
                            candidate_batch.append(parsed)
            if candidate_batch:
                results.extend(candidate_batch)
                return
            for item in obj:
                self._find_ad_objects(item, results, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    self._find_ad_objects(v, results, depth + 1)

    def _parse_lc_ad(self, ad: dict) -> Optional[dict]:
        price = ad.get("price") or ad.get("prix")
        try:
            price = int(price)
        except (TypeError, ValueError):
            return None
        if not (500 <= price <= 150_000):
            return None

        marque = (ad.get("make") or ad.get("brand") or ad.get("marque") or ad.get("makeName") or "").upper().strip()
        modele = (ad.get("model") or ad.get("modele") or ad.get("modelName") or "").upper().strip()
        if not marque or not modele:
            return None

        # Année
        year = ad.get("year") or ad.get("registrationYear") or ad.get("annee")
        if not year:
            for k in ("firstCirculationDate", "firstRegistrationDate", "dateCirculation"):
                val = ad.get(k, "")
                if val:
                    try:
                        year = int(str(val)[:4])
                    except (ValueError, TypeError):
                        pass
                    break
        try:
            year = int(year) if year else None
        except (TypeError, ValueError):
            year = None

        # Kilométrage
        km = ad.get("mileage") or ad.get("kilometrage") or ad.get("km")
        try:
            km = int(km) if km is not None else None
        except (TypeError, ValueError):
            km = None

        ad_id = str(ad.get("adId") or ad.get("id") or ad.get("listingId") or "")

        # URL
        url = ad.get("url") or ad.get("slug") or ad.get("link") or ""
        if url and not url.startswith("http"):
            url = f"https://www.lacentrale.fr{url}"
        elif not url and ad_id:
            slug = f"{year}+{marque}+{modele}".replace(" ", "+")
            url = f"https://www.lacentrale.fr/auto-annonce-occasion-{slug}-{ad_id}.html"

        # Localisation
        location = ad.get("location") or ad.get("localisation") or {}
        if isinstance(location, str):
            location = {}
        city = location.get("city") or location.get("ville") or ad.get("city") or ad.get("ville") or ""
        region = location.get("region") or ad.get("region") or ""

        # Image
        photos = ad.get("photos") or ad.get("images") or ad.get("pictures") or []
        image_url = None
        if photos:
            img = photos[0]
            if isinstance(img, str):
                image_url = img
            elif isinstance(img, dict):
                image_url = img.get("url") or img.get("src") or img.get("href")

        # Vendeur
        seller = str(ad.get("sellerType") or ad.get("ownerType") or ad.get("typeVendeur") or "").lower()
        is_pro = seller in ("pro", "professional", "dealer", "marchand", "garage")

        return {
            "source": "lacentrale",
            "external_id": ad_id or None,
            "url_annonce": url or None,
            "titre": ad.get("title") or ad.get("titre") or f"{marque} {modele}",
            "marque": marque,
            "modele": modele,
            "annee": year,
            "kilometrage": km,
            "prix_annonce": price,
            "energie": (ad.get("fuelType") or ad.get("fuel") or ad.get("energie") or "").lower(),
            "boite": (ad.get("gearboxType") or ad.get("gearbox") or ad.get("boite") or "").lower(),
            "vendeur_type": "Pro" if is_pro else "Particulier",
            "pays": "France",
            "region": region,
            "ville": city,
            "image_url": image_url,
            "date_publication": ad.get("publicationDate") or ad.get("datePublication") or ad.get("date_publication"),
        }
