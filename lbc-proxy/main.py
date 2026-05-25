import uuid, random, logging, asyncio, re
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from curl_cffi.requests import AsyncSession
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LeBonCoin Proxy")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SEARCH_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE   = "https://www.leboncoin.fr/"

LBC_VERSIONS_IOS     = ["101.50.0", "101.49.1", "101.48.0", "101.47.2", "101.46.0"]
LBC_VERSIONS_ANDROID = ["101.50.0", "101.49.1", "101.48.0"]
IOS_VERSIONS         = ["18.3", "18.4", "17.6", "17.5"]
ANDROID_VERSIONS     = ["14", "13"]
IPHONE_MODELS        = ["iPhone15,2", "iPhone15,3", "iPhone14,2", "iPhone16,1"]
ANDROID_MODELS       = ["Pixel 8", "Pixel 8 Pro", "SM-G991B", "SM-S918B", "SM-A546B"]

FUEL_MAP = {
    "diesel": "2", "gazole": "2",
    "essence": "1", "sp95": "1", "sp98": "1", "petrol": "1",
    "hybride": "6", "hybrid": "6",
    "electrique": "4", "électrique": "4", "electric": "4",
    "gpl": "3", "lpg": "3", "gnv": "3", "cng": "3",
}
GEAR_MAP = {
    "mecanique": "1", "mécanique": "1", "manuelle": "1", "bvm": "1", "bm": "1", "manual": "1",
    "automatique": "2", "auto": "2", "bva": "2", "dsg": "2", "edr": "2", "automatic": "2",
}


def _mobile_ua() -> tuple[str, str, dict]:
    if random.choice([True, False]):
        ios = random.choice(IOS_VERSIONS)
        lbc = random.choice(LBC_VERSIONS_IOS)
        device_id = str(uuid.uuid4()).upper()
        ua = f"LBC;iOS;{ios};{random.choice(IPHONE_MODELS)};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "ios",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
        return ua, "safari_ios", headers
    else:
        android = random.choice(ANDROID_VERSIONS)
        lbc = random.choice(LBC_VERSIONS_ANDROID)
        model = random.choice(ANDROID_MODELS)
        device_id = uuid.uuid4().hex[:16].upper()
        ua = f"LBC;Android;{android};{model};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "android",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
        return ua, "chrome_android", headers


def _build_payload(text, annee, km, enums, cat_id, page=1):
    km_delta = 15_000
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
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


def _extract_prix_from_ads(ads: list) -> list[int]:
    prix = []
    for ad in ads:
        raw = ad.get("price", [])
        p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
        if p and 500 <= int(p) <= 150_000:
            prix.append(int(p))
    return prix


def _extract_prix_from_html(html: str) -> list[int]:
    """Extraire les prix depuis le HTML LeBonCoin (fallback Playwright)."""
    prix = set()
    soup = BeautifulSoup(html, "lxml")

    # Sélecteurs LeBonCoin connus
    for selector in [
        {"attrs": {"data-qa-id": "aditem_price"}},
        {"attrs": {"data-test-id": "price"}},
        {"class": re.compile(r"price", re.I)},
    ]:
        for el in soup.find_all(**selector):
            text = el.get_text(" ", strip=True)
            for m in re.finditer(r'(\d[\d\s]{2,5})\s*€', text):
                val = int(m.group(1).replace(" ", "").replace(" ", ""))
                if 500 <= val <= 150_000:
                    prix.add(val)

    # Fallback : tous les prix dans la page
    if not prix:
        for m in re.finditer(r'(\d{4,6})\s*€', soup.get_text(" ")):
            val = int(m.group(1))
            if 500 <= val <= 150_000:
                prix.add(val)

    return list(prix)


async def _fetch_mobile_api(text, annee, km, enums, cat_id, max_pages=2) -> list[int]:
    """Méthode 1 : API mobile LeBonCoin avec User-Agent LBC."""
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(3)
        ua, impersonate, headers = _mobile_ua()
        logger.info(f"[mobile-api] Tentative {attempt+1} — UA: {ua[:50]}")
        prix = []
        blocked = False

        for page_num in range(1, max_pages + 1):
            payload = _build_payload(text, annee, km, enums, cat_id, page_num)
            try:
                async with AsyncSession(impersonate=impersonate) as s:
                    await s.get(HOMEPAGE, headers=headers, timeout=15)
                    r = await s.post(SEARCH_URL, json=payload, headers=headers, timeout=30)

                if r.status_code == 403:
                    logger.warning(f"[mobile-api] DataDome 403 (tentative {attempt+1})")
                    blocked = True
                    break
                if not r.ok:
                    blocked = True
                    break

                ads = r.json().get("ads", [])
                page_prix = _extract_prix_from_ads(ads)
                logger.info(f"[mobile-api] p{page_num}: {len(page_prix)} prix")
                prix.extend(page_prix)
                if not page_prix:
                    break

            except Exception as e:
                logger.error(f"[mobile-api] Erreur: {e}")
                blocked = True
                break

        if not blocked and prix:
            return prix
        if not blocked:
            return []  # 0 résultats légitimes

    return []  # Bloqué après 3 tentatives


async def _fetch_playwright(search_url: str) -> list[int]:
    """Méthode 2 : Playwright avec vrai Chrome — contourne DataDome."""
    logger.info(f"[playwright] Ouverture: {search_url}")
    prix = []
    try:
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
                locale="fr-FR",
                timezone_id="Europe/Paris",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )

            # Masquer les traces d'automatisation
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            """)

            page = await context.new_page()

            # Visiter la homepage d'abord pour récupérer les cookies
            await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # Aller sur la page de recherche
            await page.goto(search_url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(random.uniform(2.0, 4.0))

            html = await page.content()
            prix = _extract_prix_from_html(html)
            logger.info(f"[playwright] {len(prix)} prix extraits")

            await browser.close()
    except Exception as e:
        logger.error(f"[playwright] Erreur: {e}")

    return prix


def _build_search_url(text, annee, km, carburant=None, boite=None, cat_id="2") -> str:
    km_delta = 15_000
    from urllib.parse import urlencode, quote_plus
    params = {
        "category": cat_id,
        "text": text,
        "regdate_min": str(annee - 1),
        "regdate_max": str(annee + 1),
        "mileage_min": str(max(0, km - km_delta)),
        "mileage_max": str(km + km_delta),
        "price": "500-150000",
    }
    if carburant:
        fuel_lbc = {"diesel": "2", "essence": "1", "hybride": "6", "electrique": "4", "électrique": "4"}.get(carburant.lower())
        if fuel_lbc:
            params["fuel"] = fuel_lbc
    if boite:
        gear_lbc = {"manuelle": "1", "manual": "1", "automatique": "2", "automatic": "2"}.get(boite.lower())
        if gear_lbc:
            params["gearbox"] = gear_lbc
    return f"https://www.leboncoin.fr/recherche?{urlencode(params, quote_via=quote_plus)}"


class SearchRequest(BaseModel):
    marque: str
    modele: str
    annee: int
    kilometrage: int
    finition: Optional[str] = None
    motorisation: Optional[str] = None
    carburant: Optional[str] = None
    boite: Optional[str] = None
    type_vehicule: Optional[str] = None
    max_pages: int = 2


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/leboncoin")
async def leboncoin(req: SearchRequest):
    text = f"{req.marque} {req.modele}"
    if req.motorisation:
        text += f" {req.motorisation}"
    if req.finition:
        text += f" {req.finition}"

    is_util = req.type_vehicule and req.type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    cat_id = "3" if is_util else "2"

    enums: dict = {"ad_type": ["offer"]}
    if req.carburant:
        fuel = FUEL_MAP.get(req.carburant.lower().strip())
        if fuel:
            enums["fuel"] = [fuel]
    if req.boite:
        gear = GEAR_MAP.get(req.boite.lower().strip())
        if gear:
            enums["gearbox"] = [gear]

    # Méthode 1 : API mobile (rapide, sans Chrome)
    prix = await _fetch_mobile_api(text, req.annee, req.kilometrage, enums, cat_id, req.max_pages)

    if prix:
        logger.info(f"[proxy] API mobile OK: {len(prix)} prix")
        return {"prix": prix, "nb_annonces": len(prix), "methode": "mobile_api"}

    # Méthode 2 : Playwright (vrai Chrome, contourne DataDome)
    logger.info("[proxy] API mobile bloquée → Playwright")
    search_url = _build_search_url(text, req.annee, req.kilometrage, req.carburant, req.boite, cat_id)
    prix = await _fetch_playwright(search_url)

    if prix:
        logger.info(f"[proxy] Playwright OK: {len(prix)} prix")
        return {"prix": prix, "nb_annonces": len(prix), "methode": "playwright"}

    raise HTTPException(status_code=503, detail="Aucune annonce trouvée ou DataDome non résolu")
