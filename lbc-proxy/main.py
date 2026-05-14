import uuid, random, logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from curl_cffi.requests import AsyncSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LeBonCoin Proxy")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_URL  = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"


def _mobile_ua():
    if random.choice([True, False]):
        ua = (f"LBC;iOS;{random.choice(['18.4','26.0','26.2'])};iPhone;phone;"
              f"{str(uuid.uuid4()).upper()};wifi;"
              f"{random.choice(['101.44.0','101.45.0'])}")
        return ua, "safari_ios"
    model = random.choice(["Pixel 8", "SM-G991B", "SM-S918B"])
    ua = (f"LBC;Android;14;{model};phone;"
          f"{uuid.uuid4().hex[:16].upper()};wifi;100.85.2")
    return ua, "chrome_android"


class SearchRequest(BaseModel):
    marque: str
    modele: str
    annee: int
    kilometrage: int
    finition: str | None = None
    max_pages: int = 2


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/leboncoin")
async def leboncoin(req: SearchRequest):
    text = f"{req.marque} {req.modele}"
    if req.finition:
        text += f" {req.finition}"
    km_delta = 40_000 if req.kilometrage > 150_000 else 20_000

    prix = []
    for page_num in range(1, req.max_pages + 1):
        payload = {
            "filters": {
                "category": {"id": "2"},
                "enums": {"ad_type": ["offer"]},
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

        ua, impersonate = _mobile_ua()
        headers = {"User-Agent": ua, "Sec-Fetch-Dest": "empty",
                   "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site"}

        try:
            async with AsyncSession(impersonate=impersonate) as s:
                await s.get(HOMEPAGE, headers=headers, timeout=15)
                r = await s.post(API_URL, json=payload, headers=headers, timeout=30)

            if r.status_code == 403:
                logger.warning("DataDome 403 — IP bloquée")
                break
            if not r.ok:
                logger.error(f"API {r.status_code}")
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
            break

    if not prix:
        raise HTTPException(status_code=503, detail="DataDome bloque cette IP ou aucune annonce trouvée")

    return {"prix": prix, "nb_annonces": len(prix)}
