import uuid, random, logging, os
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

LBC_VERSIONS_IOS     = ["101.50.0", "101.49.1", "101.48.0", "101.47.2", "101.46.0"]
LBC_VERSIONS_ANDROID = ["101.50.0", "101.49.1", "101.48.0"]
IOS_VERSIONS         = ["18.3", "18.4", "17.6", "17.5"]
ANDROID_VERSIONS     = ["14", "13"]
IPHONE_MODELS        = ["iPhone15,2", "iPhone15,3", "iPhone14,2", "iPhone16,1"]
ANDROID_MODELS       = ["Pixel 8", "Pixel 8 Pro", "SM-G991B", "SM-S918B", "SM-A546B"]


def _mobile_ua() -> tuple[str, str, dict]:
    """Retourne (User-Agent, impersonate, extra_headers)."""
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
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
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
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
        return ua, "chrome_android", headers


FUEL_MAP = {
    "diesel": "diesel", "gazole": "diesel",
    "essence": "petrol", "sp95": "petrol", "sp98": "petrol",
    "hybride": "hybrid", "hybrid": "hybrid",
    "electrique": "electric", "électrique": "electric",
    "gpl": "lpg", "gnv": "cng",
}
GEAR_MAP = {
    "mecanique": "manual", "mécanique": "manual", "manuelle": "manual", "bvm": "manual", "bm": "manual",
    "automatique": "automatic", "auto": "automatic", "bva": "automatic", "dsg": "automatic", "edr": "automatic",
}


class SearchRequest(BaseModel):
    marque: str
    modele: str
    annee: int
    kilometrage: int
    finition: str | None = None
    motorisation: str | None = None
    carburant: str | None = None
    boite: str | None = None
    type_vehicule: str | None = None
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
    km_delta = 10_000

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

    prix = []
    # Tente jusqu'à 3 fois avec des UA différents si DataDome bloque
    for attempt in range(3):
        ua, impersonate, headers = _mobile_ua()
        logger.info(f"Tentative {attempt+1} — UA: {ua[:60]}")
        prix = []
        blocked = False

        for page_num in range(1, req.max_pages + 1):
            payload = {
                "filters": {
                    "category": {"id": cat_id},
                    "enums": enums,
                    "keywords": {"text": text},
                    "ranges": {
                        "regdate": {"min": req.annee - 1, "max": req.annee + 1},
                        "mileage": {"min": max(0, req.kilometrage - km_delta), "max": req.kilometrage + km_delta},
                    },
                },
                "limit": 35,
                "limit_alu": 3,
                "offset": 35 * (page_num - 1),
                "disable_total": True,
                "extend": True,
                "listing_source": "direct-search" if page_num == 1 else "pagination",
            }

            try:
                async with AsyncSession(impersonate=impersonate) as s:
                    # Warm-up homepage pour récupérer cookies DataDome
                    await s.get(HOMEPAGE, headers=headers, timeout=15)
                    r = await s.post(SEARCH_URL, json=payload, headers=headers, timeout=30)

                if r.status_code == 403:
                    logger.warning(f"DataDome 403 (tentative {attempt+1})")
                    blocked = True
                    break
                if not r.ok:
                    logger.error(f"API {r.status_code}")
                    blocked = True
                    break

                ads = r.json().get("ads", [])
                page_prix = []
                for ad in ads:
                    raw = ad.get("price", [])
                    p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
                    if p and 500 <= int(p) <= 150_000:
                        page_prix.append(int(p))

                logger.info(f"p{page_num}: {len(page_prix)} prix")
                prix.extend(page_prix)
                if not page_prix:
                    break

            except Exception as e:
                logger.error(f"Erreur p{page_num}: {e}")
                blocked = True
                break

        if not blocked and prix:
            logger.info(f"Succès tentative {attempt+1}: {len(prix)} prix")
            return {"prix": prix, "nb_annonces": len(prix)}

        if not blocked:
            # Pas bloqué mais 0 résultats — c'est une recherche sans annonces
            break

    if not prix:
        raise HTTPException(status_code=503, detail="DataDome bloque cette IP ou aucune annonce trouvée")

    return {"prix": prix, "nb_annonces": len(prix)}
