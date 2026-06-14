import uuid, random, logging, asyncio, re
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from curl_cffi.requests import AsyncSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LeBonCoin Proxy")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SEARCH_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE   = "https://www.leboncoin.fr/"

WEBSHARE_PASS = "nomkg04o6fsd"
WEBSHARE_HOST = "p.webshare.io:80"
WEBSHARE_COUNTRIES = ["fr", "de", "gb", "nl", "be", "es", "it", "pl", "pt", "ro"]

def _webshare_proxies() -> dict:
    country = random.choice(WEBSHARE_COUNTRIES)
    session = random.randint(1, 99999)
    user = f"lmgdmysu-{country}-{session}"
    proxy = f"http://{user}:{WEBSHARE_PASS}@{WEBSHARE_HOST}"
    return {"http": proxy, "https": proxy}

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
        return ua, "safari18_0_ios", headers
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
        return ua, "chrome131_android", headers


def _build_payload(text, annee, km, enums, cat_id, page=1, km_delta=15_000, annee_delta=0, no_km_filter=False):
    ranges = {"regdate": {"min": annee - annee_delta, "max": annee + annee_delta}}
    if not no_km_filter:
        ranges["mileage"] = {"min": max(0, km - km_delta), "max": km + km_delta}
    return {
        "filters": {
            "category": {"id": cat_id},
            "enums": enums,
            "keywords": {"text": text},
            "ranges": ranges,
        },
        "limit": 35,
        "limit_alu": 3,
        "offset": 35 * (page - 1),
        "disable_total": True,
        "extend": True,
        "listing_source": "direct-search" if page == 1 else "pagination",
    }


def _extract_prix_from_ads(ads: list, modele_filter: str = None, exclude_variants: list = None) -> list[int]:
    prix = []
    for ad in ads:
        if modele_filter:
            attrs = {a["key"]: a.get("value_label", a.get("value", "")).upper().strip()
                     for a in ad.get("attributes", [])}
            model_attr = attrs.get("model", "")
            mf = modele_filter.upper().strip()
            if mf not in model_attr and model_attr not in mf:
                continue
        if exclude_variants:
            title = ad.get("subject", "").lower()
            if any(v in title for v in exclude_variants):
                continue
        raw = ad.get("price", [])
        p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
        if p and 500 <= int(p) <= 150_000:
            prix.append(int(p))
    return prix


async def _fetch_one_page(text, annee, km, enums, cat_id, page_num, modele_filter=None, exclude_variants=None) -> tuple[list[int], bool]:
    """Fetch une page avec une IP fraîche. Retourne (prix, blocked)."""
    ua, impersonate, headers = _mobile_ua()
    payload = _build_payload(text, annee, km, enums, cat_id, page_num, km_delta=10_000, annee_delta=1)
    try:
        async with AsyncSession(impersonate=impersonate, proxies=_webshare_proxies()) as s:
            await s.get(HOMEPAGE, headers=headers, timeout=15)
            r = await s.post(SEARCH_URL, json=payload, headers=headers, timeout=30)
        if r.status_code == 403:
            logger.warning(f"[mobile-api] DataDome 403 p{page_num}")
            return [], True
        if not r.ok:
            return [], True
        ads = r.json().get("ads", [])
        page_prix = _extract_prix_from_ads(ads, modele_filter, exclude_variants)
        logger.info(f"[mobile-api] p{page_num}: {len(page_prix)} prix")
        return page_prix, not ads
    except Exception as e:
        logger.error(f"[mobile-api] p{page_num} erreur: {e}")
        return [], True


async def _fetch_mobile_api(text, annee, km, enums, cat_id, max_pages=2, modele_filter=None, exclude_variants=None) -> list[int]:
    prix = []

    for page_num in range(1, max_pages + 1):
        page_prix, stop = await _fetch_one_page(text, annee, km, enums, cat_id, page_num, modele_filter, exclude_variants)

        if not page_prix and stop:
            logger.info(f"[mobile-api] p{page_num}: retry sans filtres carburant/boîte")
            await asyncio.sleep(2)
            fallback_enums = {"ad_type": ["offer"]}
            page_prix, stop = await _fetch_one_page(text, annee, km, fallback_enums, cat_id, page_num, modele_filter, exclude_variants)

        prix.extend(page_prix)

        if stop:
            break

    logger.info(f"[mobile-api] Total: {len(prix)} prix")
    return prix


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


# ─── Geo scan ─────────────────────────────────────────────────────────────────

class GeoScanRequest(BaseModel):
    lat: float = 48.8359857
    lng: float = 2.5860974
    radius: int = 20000
    prix_max: int = 25000
    km_max: int = 180000
    max_pages: int = 5
    tout_france: bool = False  # si True, ignore lat/lng/radius — recherche nationale


def _build_geo_payload(lat: float, lng: float, radius: int, prix_max: int, km_max: int, page: int = 1, tout_france: bool = False) -> dict:
    filters: dict = {
        "category": {"id": "2"},
        "enums": {"ad_type": ["offer"]},
        "ranges": {
            "price": {"max": prix_max},
            "mileage": {"max": km_max},
        },
    }
    if not tout_france:
        filters["location"] = {"area": {"lat": lat, "lng": lng, "radius": radius}}
    return {
        "filters": filters,
        "limit": 100,
        "limit_alu": 3,
        "offset": 100 * (page - 1),
        "disable_total": True,
        "extend": True,
        "listing_source": "direct-search" if page == 1 else "pagination",
    }


_STOP_WORDS = {
    "OCCASION", "VOITURE", "AUTO", "VEHICULE", "VÉHICULE", "DIESEL", "ESSENCE",
    "HYBRIDE", "ELECTRIQUE", "ÉLECTRIQUE", "GARANTIE", "ENTRETIEN", "REVISION",
    "CONTROLE", "TECHNIQUE", "VENTE", "URGENT", "BONNE", "BON", "ETAT", "ÉTAT",
    "TRÈS", "TRES", "BELLE", "BEAU", "PROPRE", "NEUF", "NEUVE", "RÉCENT",
}

def _model_from_subject(subject: str, marque: str) -> str | None:
    """Extrait le modèle depuis le titre quand LBC retourne 'Autres'."""
    text = subject.upper().strip()
    for part in sorted([marque] + marque.split(), key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(part.upper()) + r'\b', ' ', text)
    text = re.sub(r'[^A-ZÀ-Ÿ0-9\s\-]', ' ', text)
    words = [w for w in text.split() if len(w) >= 2 and w not in _STOP_WORDS]
    return " ".join(words[:2]) if words else None


def _parse_listing(ad: dict) -> dict | None:
    if ad.get("owner", {}).get("type", "").lower() == "pro":
        return None

    attrs = {
        a["key"]: {"v": a.get("value", ""), "l": a.get("value_label", a.get("value", ""))}
        for a in ad.get("attributes", [])
    }

    price_raw = ad.get("price", [])
    price = price_raw[0] if isinstance(price_raw, list) and price_raw else price_raw
    try:
        price = int(price) if price else None
    except (ValueError, TypeError):
        price = None
    if not price or not (500 <= price <= 150_000):
        return None

    marque = attrs.get("brand", {}).get("v", "").upper().strip()
    modele = attrs.get("model", {}).get("v", "").upper().strip()

    regdate = attrs.get("regdate", {}).get("v", "")
    try:
        annee = int(str(regdate)[:4]) if regdate else None
    except (ValueError, TypeError):
        annee = None

    mileage = attrs.get("mileage", {}).get("v", "")
    try:
        km = int(mileage) if mileage else None
    except (ValueError, TypeError):
        km = None

    if marque.upper() in ("AUTRES", "AUTRE"):
        return None
    if modele.upper() in ("AUTRES", "AUTRE"):
        modele = _model_from_subject(ad.get("subject", ""), marque) or ""
    if not marque or not modele or not annee or km is None:
        return None

    list_id = ad.get("list_id")
    location = ad.get("location", {})
    images = ad.get("images", {})
    image_urls = images.get("urls_large", images.get("urls", []))

    return {
        "source": "leboncoin",
        "external_id": str(list_id),
        "url_annonce": ad.get("url") or f"https://www.leboncoin.fr/ad/voitures/{list_id}",
        "titre": ad.get("subject", ""),
        "marque": marque,
        "modele": modele,
        "annee": annee,
        "kilometrage": km,
        "prix_annonce": price,
        "energie": attrs.get("fuel", {}).get("l", ""),
        "boite": attrs.get("gearbox", {}).get("l", ""),
        "vendeur_type": "Particulier",
        "pays": "France",
        "region": location.get("region_name", ""),
        "ville": location.get("city", ""),
        "image_url": image_urls[0] if image_urls else None,
        "date_publication": ad.get("first_publication_date"),
    }


async def _fetch_geo_listings(params: GeoScanRequest) -> list[dict]:
    listings: list[dict] = []
    blocked_pages = 0

    for page_num in range(1, params.max_pages + 1):
        ua, impersonate, headers = _mobile_ua()
        payload = _build_geo_payload(
            params.lat, params.lng, params.radius,
            params.prix_max, params.km_max, page_num,
            tout_france=params.tout_france,
        )
        try:
            async with AsyncSession(impersonate=impersonate, proxies=_webshare_proxies()) as s:
                await s.get(HOMEPAGE, headers=headers, timeout=15)
                r = await s.post(SEARCH_URL, json=payload, headers=headers, timeout=30)

            if r.status_code == 403:
                logger.warning(f"[geo-scan] DataDome 403 p{page_num}")
                blocked_pages += 1
                if blocked_pages >= 2:
                    break
                await asyncio.sleep(3)
                continue
            if not r.ok:
                logger.warning(f"[geo-scan] HTTP {r.status_code} p{page_num}")
                break

            ads = r.json().get("ads", [])
            logger.info(f"[geo-scan] p{page_num}: {len(ads)} annonces brutes")
            for ad in ads:
                parsed = _parse_listing(ad)
                if parsed:
                    listings.append(parsed)
            logger.info(f"[geo-scan] p{page_num}: {len(listings)} total parsées")

            if len(ads) < 100:
                break
            blocked_pages = 0

        except Exception as e:
            logger.error(f"[geo-scan] p{page_num} erreur: {e}")
            break

    return listings


@app.post("/scan-geo")
async def scan_geo(req: GeoScanRequest):
    listings = await _fetch_geo_listings(req)
    logger.info(f"[geo-scan] Terminé : {len(listings)} annonces")
    return {"listings": listings, "count": len(listings)}


# ─── Leboncoin classique ───────────────────────────────────────────────────────

FUEL_MAP_ENUM = {
    "diesel": "diesel", "gazole": "diesel",
    "essence": "petrol", "sp95": "petrol", "sp98": "petrol",
    "hybride": "hybrid", "hybrid": "hybrid",
    "electrique": "electric", "électrique": "electric",
    "gpl": "lpg", "gnv": "cng",
}
GEAR_MAP_ENUM = {
    "mecanique": "manual", "mécanique": "manual", "manuelle": "manual", "bvm": "manual", "bm": "manual",
    "automatique": "automatic", "auto": "automatic", "bva": "automatic", "dsg": "automatic",
}

VARIANTS_A_EXCLURE = ["stepway", "rs", "sport", "gt"]


def _lbc_search_text(marque: str, modele: str) -> str:
    """Construit le texte de recherche LBC — marque + modèle uniquement."""
    m = modele.strip()
    if marque.upper().replace("-", " ").replace("BENZ", "").strip() == "MERCEDES":
        if m.upper().startswith("CLASSE "):
            suffix = m[7:].strip().upper()
            if suffix and suffix[0] == "G":
                m = m[7:].strip()
    return f"{marque} {m}"


@app.post("/leboncoin")
async def leboncoin(req: SearchRequest):
    text = _lbc_search_text(req.marque, req.modele)

    is_util = req.type_vehicule and req.type_vehicule.lower() in ("utilitaire", "fourgon", "van", "camionnette")
    cat_id = "5" if is_util else "2"

    enums: dict = {"ad_type": ["offer"]}
    if req.carburant:
        fuel = FUEL_MAP_ENUM.get(req.carburant.lower().strip())
        if fuel:
            enums["fuel"] = [fuel]
    if req.boite:
        gear = GEAR_MAP_ENUM.get(req.boite.lower().strip())
        if gear:
            enums["gearbox"] = [gear]

    modele_lower = req.modele.lower()
    exclude_variants = [v for v in VARIANTS_A_EXCLURE if v not in modele_lower]

    prix = await _fetch_mobile_api(text, req.annee, req.kilometrage, enums, cat_id, req.max_pages, modele_filter=req.modele, exclude_variants=exclude_variants)

    if prix:
        logger.info(f"[proxy] {len(prix)} prix")
        return {"prix": prix, "nb_annonces": len(prix), "methode": "mobile_api"}

    raise HTTPException(status_code=503, detail="Aucune annonce trouvée ou DataDome non résolu")
