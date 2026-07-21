import asyncio
import base64
import logging
import re
from dotenv import load_dotenv

load_dotenv()  # Charge .env en local ; les variables Railway ont priorité en prod

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from curl_cffi.requests import AsyncSession

import os
from scrapers.histovec import get_histovec_pdf
from scrapers.leboncoin import (
    LeboncoinScraper,
    _mobile_ua, _webshare_proxies,
    API_URL as LBC_API_URL, HOMEPAGE as LBC_HOMEPAGE,
)
from scrapers.lacentrale import LaCentraleScraper
from scrapers.autoscout24 import AutoScout24Scraper
from utils.calculator import calculate_estimation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Modèles utilitaires légers courants — détection automatique si type_vehicule non fourni
_MODELES_UTILITAIRES = {
    # Toyota
    "proace", "pro-ace",
    # Renault
    "trafic", "master", "kangoo", "express",
    # Peugeot
    "partner", "expert", "boxer",
    # Citroën
    "berlingo", "jumpy", "jumper", "dispatch",
    # Ford
    "transit",
    # Volkswagen
    "transporter", "crafter", "caddy",
    # Mercedes
    "sprinter", "vito", "citan",
    # Opel / Vauxhall
    "vivaro", "movano",
    # Fiat
    "ducato", "doblo", "scudo", "fiorino", "qubo",
    # Nissan
    "nv200", "nv300", "nv400", "primastar", "interstar",
    # Iveco
    "daily",
    # Dacia
    "dokker",
    # Citroën / Peugeot petits utilitaires
    "nemo", "bipper",
    # Maxus / LDV
    "deliver",
    # Mitsubishi
    "l200", "l300",
    # Toyota
    "hiace", "hilux",
    # Hyundai
    "h1", "h350",
}


def _detect_type_vehicule(modele: str) -> str:
    """Retourne 'utilitaire' si le modèle correspond à un utilitaire connu."""
    normalized = modele.lower().replace(" ", "").replace("-", "")
    for kw in _MODELES_UTILITAIRES:
        if kw.replace("-", "") in normalized:
            return "utilitaire"
    return "voiture"

app = FastAPI(
    title="VM Auto Estimation API",
    description="API de rachat de véhicules d'occasion — VM Auto Business (Seine-et-Marne)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EstimationRequest(BaseModel):
    marque: str = Field(..., example="Peugeot")
    modele: str = Field(..., example="308")
    annee: int = Field(..., ge=1990, le=2030, example=2020)
    kilometrage: int = Field(..., ge=0, le=500000, example=80000)
    finition: Optional[str] = Field(None, example="S-Line")
    motorisation: Optional[str] = Field(None, example="1.2 PureTech 130")
    boite: Optional[str] = Field(None, example="mecanique")
    carburant: Optional[str] = Field(None, example="diesel")
    type_vehicule: Optional[str] = Field(None, example="utilitaire")  # "voiture" ou "utilitaire"


@app.get("/")
async def root():
    return {"status": "ok", "service": "VM Auto Estimation API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


DS_CITROEN_MODELS = {"DS3", "DS4", "DS5"}

def _resolve_brand(marque: str, modele: str) -> str:
    """DS3/DS4/DS5 sont indexés sous Citroën sur LeBonCoin."""
    if marque.upper() == "DS" and modele.upper().replace(" ", "") in {m.replace(" ", "") for m in DS_CITROEN_MODELS}:
        return "Citroën"
    return marque

@app.post("/estimation")
async def estimation(req: EstimationRequest):
    type_vehicule = req.type_vehicule or _detect_type_vehicule(req.modele)
    marque_search = _resolve_brand(req.marque, req.modele)
    if marque_search != req.marque:
        logger.info(f"Marque résolue : {req.marque} → {marque_search} pour {req.modele}")
    logger.info(f"Demande reçue : {req.marque} {req.modele} {req.annee} {req.kilometrage} km | type={type_vehicule}")

    scraper_args = dict(
        finition=req.finition, carburant=req.carburant,
        boite=req.boite, motorisation=req.motorisation,
        type_vehicule=type_vehicule,
    )

    all_prices: list[int] = []
    sources_detail: dict = {}

    # 1. LeBonCoin en priorité
    lbc = LeboncoinScraper()
    try:
        lbc_prices = await asyncio.wait_for(
            lbc.get_prices(marque_search, req.modele, req.annee, req.kilometrage, **scraper_args),
            timeout=60,
        )
        sources_detail["leboncoin"] = {"annonces": len(lbc_prices)}
        all_prices.extend(lbc_prices)
        logger.info(f"[leboncoin] {len(lbc_prices)} prix récupérés")
    except Exception as e:
        logger.error(f"[leboncoin] Erreur : {e}")
        sources_detail["leboncoin"] = {"annonces": 0, "erreur": str(e)}

    # 2. Fallback AutoScout24 + La Centrale si LBC n'a rien retourné
    if not all_prices:
        logger.info("LBC vide — fallback AutoScout24 + La Centrale")
        fallback_scrapers = [AutoScout24Scraper(), LaCentraleScraper()]
        tasks = [
            s.get_prices(marque_search, req.modele, req.annee, req.kilometrage, **scraper_args)
            for s in fallback_scrapers
        ]
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=60,
        )
        for scraper, result in zip(fallback_scrapers, results):
            if isinstance(result, Exception):
                logger.error(f"[{scraper.name}] Erreur : {result}")
                sources_detail[scraper.name] = {"annonces": 0, "erreur": str(result)}
            else:
                logger.info(f"[{scraper.name}] {len(result)} prix récupérés")
                sources_detail[scraper.name] = {"annonces": len(result)}
                all_prices.extend(result)

    if not all_prices:
        raise HTTPException(
            status_code=404,
            detail="Aucune annonce trouvée pour ce véhicule. Vérifiez la marque et le modèle.",
        )

    calc = calculate_estimation(all_prices, req.marque, req.modele, req.motorisation, req.finition, req.boite, req.annee, req.kilometrage)

    return {
        "vehicule": {
            "marque": req.marque.upper(),
            "modele": req.modele.upper(),
            "annee": req.annee,
            "kilometrage": req.kilometrage,
            "finition": req.finition or None,
            "motorisation": req.motorisation or None,
            "boite": req.boite or None,
            "carburant": req.carburant or None,
            "type_vehicule": type_vehicule,
        },
        "marche": {
            "nb_annonces": calc["nb_annonces"],
            "prix_moyen": calc["prix_moyen"],
            "prix_median": calc["prix_median"],
            "fourchette_basse": calc["fourchette_basse"],
            "fourchette_haute": calc["fourchette_haute"],
        },
        "estimation_rachat": {
            "prix_suggere": calc["prix_rachat"],
            "methode": calc["methode"],
        },
        "sources": sources_detail,
    }


# ─── Geo scan (Bonnes Affaires) ───────────────────────────────────────────────

class GeoScanRequest(BaseModel):
    lat: float = 48.8359857
    lng: float = 2.5860974
    radius: int = 20000
    prix_max: int = 25000
    km_max: int = 180000
    max_pages: int = 5
    tout_france: bool = False


def _build_geo_payload(lat, lng, radius, prix_max, km_max, page=1, tout_france=False):
    filters: dict = {
        "category": {"id": "2"},
        "enums": {"ad_type": ["offer"]},
        "ranges": {"price": {"max": prix_max}, "mileage": {"max": km_max}},
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
        "sort_by": "time",
        "sort_order": "desc",
    }


_STOP_WORDS = {
    "OCCASION", "VOITURE", "AUTO", "VEHICULE", "VÉHICULE", "DIESEL", "ESSENCE",
    "HYBRIDE", "ELECTRIQUE", "ÉLECTRIQUE", "GARANTIE", "ENTRETIEN", "REVISION",
    "CONTROLE", "TECHNIQUE", "VENTE", "URGENT", "BONNE", "BON", "ETAT", "ÉTAT",
    "TRÈS", "TRES", "BELLE", "BEAU", "PROPRE", "NEUF", "NEUVE", "RÉCENT",
}


def _model_from_subject(subject: str, marque: str) -> Optional[str]:
    text = subject.upper().strip()
    for part in sorted([marque] + marque.split(), key=len, reverse=True):
        text = re.sub(r'\b' + re.escape(part.upper()) + r'\b', ' ', text)
    text = re.sub(r'[^A-ZÀ-Ÿ0-9\s\-]', ' ', text)
    words = [w for w in text.split() if len(w) >= 2 and w not in _STOP_WORDS]
    return " ".join(words[:2]) if words else None


def _parse_geo_listing(ad: dict) -> Optional[dict]:
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
                await s.get(LBC_HOMEPAGE, headers=headers, timeout=15)
                r = await s.post(LBC_API_URL, json=payload, headers=headers, timeout=30)
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
                parsed = _parse_geo_listing(ad)
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


# ─── La Centrale scan (Bonnes Affaires) ──────────────────────────────────────

class LaCentraleScanRequest(BaseModel):
    lat: float = 48.8359857
    lng: float = 2.5860974
    prix_max: int = 25000
    km_max: int = 180000
    max_pages: int = 5
    dept_code: Optional[str] = None  # code département explicite (ex: "77"), sinon déduit de lat/lng


async def _lat_lng_to_dept(lat: float, lng: float) -> Optional[str]:
    """Convertit des coordonnées GPS en code département français via Nominatim."""
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json"
    try:
        async with AsyncSession(impersonate="chrome120") as s:
            r = await s.get(url, headers={"User-Agent": "vmautobusiness/1.0 (contact@vmautobusiness.fr)"}, timeout=10)
        if not r.ok:
            return None
        data = r.json()
        postcode = (data.get("address") or {}).get("postcode", "")
        if postcode and len(postcode) >= 2:
            code = postcode[:2]
            if code == "97":
                code = postcode[:3]
            return code
        # Fallback: code depuis county
        county = (data.get("address") or {}).get("county", "")
        m = re.search(r'\b(\d{2,3})\b', county)
        return m.group(1) if m else None
    except Exception as e:
        logger.error(f"[nominatim] Erreur géocodage inverse: {e}")
        return None


@app.post("/scan-lacentrale")
async def scan_lacentrale(req: LaCentraleScanRequest):
    dept = req.dept_code
    if not dept:
        dept = await _lat_lng_to_dept(req.lat, req.lng)
    if not dept:
        raise HTTPException(status_code=400, detail="Impossible de déterminer le département depuis les coordonnées fournies")

    logger.info(f"[scan-lacentrale] Département: {dept}, prix_max={req.prix_max}, km_max={req.km_max}")
    lc = LaCentraleScraper()
    listings = await lc.scan_by_dept(dept, req.prix_max, req.km_max, req.max_pages)
    logger.info(f"[scan-lacentrale] Terminé : {len(listings)} annonces (dept {dept})")
    return {"listings": listings, "count": len(listings), "dept": dept}


# ─── Scan géo enrichi : scan LBC + estimation marché LBC par modèle ──────────

async def _estimate_market_lbc(marque: str, modele: str, annee: Optional[int], km: Optional[int]) -> Optional[int]:
    """Estime la valeur marché : LeBonCoin en priorité, AutoScout24 en fallback."""
    marque_search = _resolve_brand(marque, modele)
    type_vehicule = _detect_type_vehicule(modele)
    annee_eff = annee or 2015
    km_eff = km or 100000

    # 1. LeBonCoin
    try:
        lbc = LeboncoinScraper()
        prices = await asyncio.wait_for(
            lbc.get_prices(marque_search, modele, annee_eff, km_eff, type_vehicule=type_vehicule),
            timeout=20,
        )
        if prices:
            s = sorted(prices)
            logger.info(f"[enriched] LBC {marque} {modele} {annee} → {s[len(s)//2]}€ ({len(s)} prix)")
            return s[len(s) // 2]
    except Exception as e:
        logger.warning(f"[enriched] LBC {marque} {modele} {annee} erreur: {e}")

    # 2. Fallback AutoScout24 si LBC n'a rien trouvé
    try:
        as24 = AutoScout24Scraper()
        prices2 = await asyncio.wait_for(
            as24.get_prices(marque_search, modele, annee_eff, km_eff, type_vehicule=type_vehicule),
            timeout=15,
        )
        if prices2:
            s2 = sorted(prices2)
            logger.info(f"[enriched] AS24 {marque} {modele} {annee} → {s2[len(s2)//2]}€ ({len(s2)} prix)")
            return s2[len(s2) // 2]
    except Exception as e:
        logger.warning(f"[enriched] AS24 {marque} {modele} {annee} erreur: {e}")

    logger.warning(f"[enriched] aucun prix trouvé pour {marque} {modele} {annee}")
    return None


@app.post("/scan-geo-enriched")
async def scan_geo_enriched(req: GeoScanRequest):
    """Scan LBC géographique + estimation valeur marché LBC pour chaque modèle unique."""
    listings = await _fetch_geo_listings(req)
    logger.info(f"[geo-enriched] {len(listings)} annonces scannées")

    # Groupes uniques marque/modèle/année — compter les occurrences pour prioriser
    group_counts: dict[str, int] = {}
    group_meta: dict[str, dict] = {}
    for l in listings:
        key = f"{(l.get('marque') or '').upper()}|{(l.get('modele') or '').upper()}|{l.get('annee') or ''}"
        group_counts[key] = group_counts.get(key, 0) + 1
        if key not in group_meta:
            group_meta[key] = {"marque": l.get("marque"), "modele": l.get("modele"),
                                "annee": l.get("annee"), "km": l.get("kilometrage")}

    # Limiter à 15 groupes les plus fréquents (éviter timeout edge function)
    top_keys = sorted(group_counts, key=lambda k: group_counts[k], reverse=True)[:15]
    groups = {k: group_meta[k] for k in top_keys}
    logger.info(f"[geo-enriched] {len(group_meta)} groupes uniques, estimation sur top {len(groups)}")

    # Estimation en parallèle (max 6 simultanées pour ne pas surcharger Render free tier)
    market_values: dict[str, Optional[int]] = {}
    sem = asyncio.Semaphore(6)

    async def _est(key: str, g: dict):
        async with sem:
            val = await _estimate_market_lbc(g["marque"], g["modele"], g["annee"], g["km"])
        market_values[key] = val
        logger.info(f"[geo-enriched] {g['marque']} {g['modele']} {g['annee']} → {val}")

    await asyncio.gather(*[_est(k, v) for k, v in groups.items()])

    # Enrichir les listings
    enriched = []
    for l in listings:
        key = f"{(l.get('marque') or '').upper()}|{(l.get('modele') or '').upper()}|{l.get('annee') or ''}"
        valeur_marche = market_values.get(key)
        enriched.append({**l, "valeur_marche": valeur_marche})

    logger.info(f"[geo-enriched] Terminé — {len(enriched)} annonces enrichies")
    return {"listings": enriched, "count": len(enriched)}


# ---------------------------------------------------------------------------
# Histovec — OCR carte grise + rapport automatique
# ---------------------------------------------------------------------------

class HistovecRequest(BaseModel):
    immatriculation: str
    nom: str
    prenom: Optional[str] = ""
    formule: str

@app.post("/histovec-debug")
async def histovec_debug(req: HistovecRequest):
    """Retourne la réponse JSON brute d'Histovec pour débug des clés."""
    from scrapers.histovec import _get_jwt, _format_immat_siv
    import uuid as uuid_lib
    from curl_cffi.requests import AsyncSession

    immat_siv = _format_immat_siv(req.immatriculation)
    formule_clean = req.formule.upper().replace(" ", "")
    token = await _get_jwt()
    if not token:
        raise HTTPException(status_code=502, detail="Impossible d'obtenir le JWT Histovec")

    payload = {
        "nom": req.nom.upper(),
        "prenom": req.prenom.strip() if req.prenom else "",
        "numeroFormule": formule_clean,
        "immat": immat_siv,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://histovec.interieur.gouv.fr",
        "Referer": "https://histovec.interieur.gouv.fr/histovec/",
    }
    async with AsyncSession(impersonate="chrome120") as s:
        r = await s.post(
            f"https://histovec.interieur.gouv.fr/public/v1/report_by_data/{uuid_lib.uuid4()}",
            json=payload, headers=headers, timeout=30,
        )
    return {"status": r.status_code, "payload_sent": payload, "response": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text[:2000]}


@app.post("/histovec-debug-full")
async def histovec_debug_full(req: HistovecRequest):
    """Debug complet : report_by_data + get_csa avec logs de chaque étape."""
    from scrapers.histovec import _get_jwt, _format_immat_siv, _compute_holder_id
    import uuid as uuid_lib
    from urllib.parse import quote
    from curl_cffi.requests import AsyncSession

    immat_siv = _format_immat_siv(req.immatriculation)
    formule_clean = req.formule.upper().replace(" ", "")
    prenom_api = req.prenom.strip() if req.prenom and req.prenom.strip() else " "

    token = await _get_jwt()
    if not token:
        raise HTTPException(status_code=502, detail="JWT échoué")

    user_id = str(uuid_lib.uuid4())
    holder_id = _compute_holder_id(req.nom, prenom_api, immat_siv, formule_clean)
    holder_id_encoded = quote(holder_id, safe="")

    payload = {
        "nom": req.nom.upper(),
        "prenom": prenom_api,
        "numeroFormule": formule_clean,
        "immat": immat_siv,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://histovec.interieur.gouv.fr",
        "Referer": "https://histovec.interieur.gouv.fr/histovec/",
    }

    result = {
        "immat_siv": immat_siv,
        "holder_id": holder_id,
        "holder_id_encoded": holder_id_encoded,
        "payload_sent": payload,
    }

    async with AsyncSession(impersonate="chrome120") as s:
        r = await s.post(
            f"https://histovec.interieur.gouv.fr/public/v1/report_by_data/{user_id}",
            json=payload, headers=headers, timeout=30,
        )
        result["report_status"] = r.status_code
        result["report_ct"] = r.headers.get("content-type", "?")
        try:
            result["report_json"] = r.json()
        except Exception:
            result["report_text"] = r.text[:1000]

        # Extraire clefAcheteur de la réponse
        try:
            resp_json = r.json()
        except Exception:
            resp_json = {}
        clef_acheteur = (resp_json.get("hubimmat") or {}).get("clefAcheteur", "")
        result["clef_acheteur"] = clef_acheteur

        # get_csa avec clefAcheteur (pas holderId calculé)
        r_csa = await s.get(
            f"https://histovec.interieur.gouv.fr/public/v1/get_csa/{user_id}/{clef_acheteur}",
            headers={**headers, "Accept": "application/pdf,*/*"},
            timeout=30,
        )
        result["csa_status"] = r_csa.status_code
        result["csa_ct"] = r_csa.headers.get("content-type", "?")
        result["csa_size"] = len(r_csa.content)
        result["csa_first4"] = r_csa.content[:4].decode(errors="replace")
        result["csa_is_pdf"] = r_csa.content[:4] == b"%PDF"
        if not result["csa_is_pdf"]:
            result["csa_body_preview"] = r_csa.content[:500].decode(errors="replace")

    return result


@app.post("/histovec-intercept")
async def histovec_intercept(req: HistovecRequest):
    """Playwright: intercepte les requêtes réseau lors du téléchargement CSA propriétaire."""
    from playwright.async_api import async_playwright
    from playwright_stealth import stealth_async

    immat_siv_clean = req.immatriculation.upper().replace(" ", "")
    formule_clean = req.formule.upper().replace(" ", "")
    prenom = req.prenom.strip() if req.prenom else ""

    captured = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="fr-FR",
        )
        page = await context.new_page()
        await stealth_async(page)

        # Intercepter tous les appels réseau
        async def on_request(request):
            url = request.url
            if "histovec" in url or "interieur.gouv.fr" in url:
                captured.append({
                    "type": "request",
                    "method": request.method,
                    "url": url,
                    "post_data": request.post_data,
                })

        async def on_response(response):
            url = response.url
            if "get_csa" in url or "pdf" in url.lower() or "certificat" in url.lower():
                try:
                    body = await response.body()
                    captured.append({
                        "type": "response",
                        "url": url,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", "?"),
                        "size": len(body),
                        "is_pdf": body[:4] == b"%PDF",
                        "preview": body[:100].decode(errors="replace"),
                    })
                except Exception as e:
                    captured.append({"type": "response_error", "url": url, "error": str(e)})

        page.on("request", on_request)
        page.on("response", on_response)

        await page.goto("https://histovec.interieur.gouv.fr/histovec/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)

        # Cliquer sur "Propriétaire" si bouton présent
        for txt in ["Propriétaire", "propriétaire", "Je suis le propriétaire"]:
            try:
                btn = page.get_by_text(txt, exact=False).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    await asyncio.sleep(2)
                    break
            except Exception:
                pass

        # Remplir le formulaire
        for kws, val in [
            (["immatriculation", "immat", "SIV"], immat_siv_clean),
            (["formule", "numeroFormule", "numéro de formule"], formule_clean),
            (["nom"], req.nom.upper()),
            (["prénom", "prenom"], prenom),
        ]:
            for kw in kws:
                for loc in [
                    page.get_by_label(kw, exact=False),
                    page.get_by_placeholder(kw, exact=False),
                    page.locator(f'input[id*="{kw}"]'),
                    page.locator(f'input[name*="{kw}"]'),
                ]:
                    try:
                        if await loc.count() > 0:
                            await loc.first.fill(val)
                            break
                    except Exception:
                        pass
            await asyncio.sleep(0.2)

        # Soumettre
        for sel in ['button[type="submit"]', 'button:has-text("Accéder")', 'button:has-text("Consulter")']:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    break
            except Exception:
                pass

        await asyncio.sleep(10)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # Chercher bouton télécharger CSA
        for txt in ["Télécharger", "télécharger", "CSA", "certificat", "Certificat", "situation administrative"]:
            try:
                btn = page.get_by_text(txt, exact=False).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    await asyncio.sleep(5)
                    break
            except Exception:
                pass

        # Screenshot final
        screenshot = await page.screenshot(full_page=True)
        await browser.close()

    return {
        "captured_requests": [c for c in captured if c["type"] == "request"],
        "captured_responses": [c for c in captured if c["type"] != "request"],
        "screenshot_size": len(screenshot),
    }


@app.post("/histovec")
async def histovec(req: HistovecRequest):
    """
    Ouvre Histovec avec Playwright, remplit le formulaire et retourne le PDF en base64.
    Les données (nom, formule) proviennent du scan de carte grise fait à l'étape 1.
    """
    logger.info(f"[histovec] Démarrage pour immat={req.immatriculation} nom={req.nom}")

    try:
        pdf_bytes = await get_histovec_pdf(req.nom, req.prenom or "", req.formule, req.immatriculation)
    except Exception as e:
        logger.error(f"[histovec] Erreur Playwright: {e}")
        raise HTTPException(status_code=502, detail=f"Erreur navigation Histovec: {e}")

    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="Histovec n'a retourné aucun résultat pour ce véhicule")

    is_pdf = pdf_bytes[:4] == b"%PDF"
    content_type = "application/pdf" if is_pdf else "image/png"
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode()
    logger.info(f"[histovec] Fichier généré ({len(pdf_bytes)} bytes) type={content_type}")

    return {"success": True, "pdf_base64": pdf_b64, "content_type": content_type}
