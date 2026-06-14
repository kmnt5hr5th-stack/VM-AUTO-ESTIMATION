import uuid, random, logging, requests, threading, uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SEARCH_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE   = "https://www.leboncoin.fr/"

LBC_VERSIONS_IOS     = ["101.50.0", "101.49.1", "101.48.0"]
LBC_VERSIONS_ANDROID = ["101.50.0", "101.49.1", "101.48.0"]
IOS_VERSIONS         = ["18.3", "18.4", "17.6", "17.5"]
ANDROID_VERSIONS     = ["14", "13"]
IPHONE_MODELS        = ["iPhone15,2", "iPhone15,3", "iPhone14,2", "iPhone16,1"]
ANDROID_MODELS       = ["Pixel 8", "Pixel 8 Pro", "SM-G991B", "SM-S918B"]

FUEL_MAP = {
    "diesel": "diesel", "gazole": "diesel",
    "essence": "petrol", "sp95": "petrol", "sp98": "petrol",
    "hybride": "hybrid", "electrique": "electric",
    "gpl": "lpg", "gnv": "cng",
}
GEAR_MAP = {
    "mecanique": "manual", "mecanique": "manual", "manuelle": "manual",
    "automatique": "automatic", "auto": "automatic", "bva": "automatic",
}


def _mobile_ua():
    if random.choice([True, False]):
        ios = random.choice(IOS_VERSIONS)
        lbc = random.choice(LBC_VERSIONS_IOS)
        device_id = str(uuid.uuid4()).upper()
        ua = f"LBC;iOS;{ios};{random.choice(IPHONE_MODELS)};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "ios",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
    else:
        android = random.choice(ANDROID_VERSIONS)
        lbc = random.choice(LBC_VERSIONS_ANDROID)
        model = random.choice(ANDROID_MODELS)
        device_id = uuid.uuid4().hex[:16].upper()
        ua = f"LBC;Android;{android};{model};phone;{device_id};wifi;{lbc}"
        headers = {
            "User-Agent": ua,
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "X-LBC-DEVICE-ID": device_id,
            "X-LBC-VERSION": lbc,
            "X-LBC-PLATFORM": "android",
            "Origin": "https://www.leboncoin.fr",
            "Referer": "https://www.leboncoin.fr/",
        }
    return headers


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
def leboncoin(req: SearchRequest):
    text = f"{req.marque} {req.modele}"

    is_util = req.type_vehicule and req.type_vehicule.lower() in ("utilitaire", "fourgon", "van")
    cat_id = "5" if is_util else "2"

    enums = {"ad_type": ["offer"]}
    if req.carburant:
        fuel = FUEL_MAP.get(req.carburant.lower().strip())
        if fuel:
            enums["fuel"] = [fuel]

    km_delta = 10_000

    for attempt in range(3):
        headers = _mobile_ua()
        logger.info(f"Tentative {attempt + 1}")
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
                session = requests.Session()
                session.get(HOMEPAGE, headers=headers, timeout=15)
                r = session.post(SEARCH_URL, json=payload, headers=headers, timeout=30)
                if r.status_code == 403:
                    logger.warning(f"DataDome 403 (tentative {attempt + 1})")
                    blocked = True
                    break
                if not r.ok:
                    blocked = True
                    break
                ads = r.json().get("ads", [])
                modele_lower = req.modele.lower()
                VARIANTS = ["stepway", "stepway 2", "rs", "sport", "gt"]
                exclude = [v for v in VARIANTS if v not in modele_lower]

                page_prix = []
                for ad in ads:
                    title = ad.get("subject", "").lower()
                    if any(v in title for v in exclude):
                        continue
                    raw = ad.get("price", [])
                    p = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, (int, float)) else None)
                    if p and 500 <= int(p) <= 150_000:
                        page_prix.append(int(p))
                logger.info(f"p{page_num}: {len(page_prix)} prix")
                prix.extend(page_prix)
                if not page_prix:
                    break
            except Exception as e:
                logger.error(f"Erreur: {e}")
                blocked = True
                break

        if not blocked and prix:
            return {"prix": prix, "nb_annonces": len(prix)}
        if not blocked:
            break

    raise HTTPException(status_code=503, detail="Aucune annonce trouvee")


def _start_ngrok():
    try:
        from pyngrok import ngrok
        url = ngrok.connect(8080)
        print("\n" + "=" * 50)
        print(f"URL PUBLIQUE : {url}")
        print("=> Copie cette URL dans Railway : LBC_PROXY_URL")
        print("=" * 50 + "\n")
    except Exception as e:
        print(f"ngrok non disponible: {e}")
        print("Le proxy tourne sur le reseau local seulement.")


if __name__ == "__main__":
    threading.Thread(target=_start_ngrok, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8080)
