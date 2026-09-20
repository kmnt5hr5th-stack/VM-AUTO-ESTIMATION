import asyncio
import base64
import json as _json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()  # Charge .env en local ; les variables Railway ont priorité en prod

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from curl_cffi.requests import AsyncSession
import httpx

import os

# ── Paramètres Supabase ────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── Telegram ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8303548439:AAHipFPA6R6dgqoLn-RR9Aw-r_Hgy9wZ1Yo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "538022992")

_settings_cache: dict = {}
_settings_cache_time: float = 0
_SETTINGS_TTL = 600  # 10 minutes

async def _get_settings() -> dict:
    global _settings_cache, _settings_cache_time
    now = time.time()
    if _settings_cache and (now - _settings_cache_time) < _SETTINGS_TTL:
        return _settings_cache
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return _settings_cache
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/site_settings?select=key,value",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"},
            )
            if resp.status_code == 200:
                _settings_cache = {row["key"]: row["value"] for row in resp.json()}
                _settings_cache_time = now
    except Exception as e:
        logging.getLogger("vm-api").warning(f"[settings] Supabase fetch failed: {e}")
    return _settings_cache
from scrapers.histovec import get_histovec_pdf
from scrapers.leboncoin import (
    LeboncoinScraper,
    _mobile_ua, _webshare_proxies,
    warm_up_playwright,
    API_URL as LBC_API_URL, HOMEPAGE as LBC_HOMEPAGE,
)
from scrapers.lacentrale import LaCentraleScraper
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


@app.on_event("startup")
async def _warmup():
    """Lance le contexte Playwright persistant au démarrage."""
    await warm_up_playwright()


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
    carrosserie: Optional[str] = Field(None, example="Coupé")  # Berline, Break, Coupé, Cabriolet, SUV / 4x4, Monospace


@app.get("/")
@app.head("/")
async def root():
    return {"status": "ok", "service": "VM Auto Estimation API", "version": "1.0.0"}


@app.get("/health")
@app.head("/health")
async def health():
    return {"status": "healthy"}


@app.get("/catalog/versions")
async def catalog_versions(marque: str = "", modele: str = "", annee: int = 0, carburant: str = ""):
    versions = _get_caradisiac_versions(marque, modele, annee, carburant)
    return {"versions": versions, "count": len(versions)}


DS_CITROEN_MODELS = {"DS3", "DS4", "DS5"}

def _resolve_brand(marque: str, modele: str) -> str:
    """DS3/DS4/DS5 sont indexés sous Citroën sur LeBonCoin."""
    if marque.upper() == "DS" and modele.upper().replace(" ", "") in {m.replace(" ", "") for m in DS_CITROEN_MODELS}:
        return "Citroën"
    return marque

async def _run_estimation(req: EstimationRequest) -> dict:
    type_vehicule = req.type_vehicule or _detect_type_vehicule(req.modele)
    marque_search = _resolve_brand(req.marque, req.modele)
    if marque_search != req.marque:
        logger.info(f"Marque résolue : {req.marque} → {marque_search} pour {req.modele}")
    logger.info(f"Demande reçue : {req.marque} {req.modele} {req.annee} {req.kilometrage} km | type={type_vehicule}")

    lbc_args = dict(
        finition=req.finition, carburant=req.carburant,
        boite=req.boite, motorisation=req.motorisation,
        type_vehicule=type_vehicule,
        carrosserie=req.carrosserie,
    )

    all_prices: list[int] = []
    sources_detail: dict = {}

    lbc = LeboncoinScraper()
    try:
        lbc_prices = await asyncio.wait_for(
            lbc.get_prices(marque_search, req.modele, req.annee, req.kilometrage, **lbc_args),
            timeout=45,
        )
        sources_detail["leboncoin"] = {"annonces": len(lbc_prices)}
        all_prices.extend(lbc_prices)
        logger.info(f"[leboncoin] {len(lbc_prices)} prix récupérés")
    except Exception as e:
        logger.error(f"[leboncoin] Erreur : {e}")
        sources_detail["leboncoin"] = {"annonces": 0, "erreur": str(e)}

    if not all_prices:
        raise HTTPException(
            status_code=404,
            detail="Aucune annonce trouvée pour ce véhicule. Vérifiez la marque et le modèle.",
        )

    calc = calculate_estimation(all_prices, req.marque, req.modele, req.motorisation, req.finition, req.boite, req.annee, req.kilometrage)

    # Ajustement global configurable depuis l'admin
    settings = await _get_settings()
    ajustement = float(settings.get("estimation.ajustement_global", "0") or "0")
    if ajustement:
        calc["prix_rachat"] = round(calc["prix_rachat"] * (1 + ajustement / 100) / 100) * 100

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


@app.post("/estimation")
async def estimation(req: EstimationRequest):
    try:
        return await asyncio.wait_for(_run_estimation(req), timeout=100)
    except asyncio.TimeoutError:
        logger.error("[estimation] Timeout global 100s dépassé")
        raise HTTPException(status_code=504, detail="Délai de scraping dépassé — réessayez dans quelques secondes.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[estimation] Erreur inattendue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/estimation/details")
async def estimation_details(req: EstimationRequest):
    """Comme /estimation mais retourne aussi la liste brute des annonces LBC (prix, km, titre, url)."""
    async def _run():
        type_vehicule = req.type_vehicule or _detect_type_vehicule(req.modele)
        marque_search = _resolve_brand(req.marque, req.modele)
        lbc_args = dict(
            finition=req.finition, carburant=req.carburant,
            boite=req.boite, motorisation=req.motorisation,
            type_vehicule=type_vehicule, carrosserie=req.carrosserie,
        )
        lbc = LeboncoinScraper()
        try:
            listings = await lbc.get_listings(marque_search, req.modele, req.annee, req.kilometrage, **lbc_args)
        except Exception as e:
            logger.error(f"[estimation/details] LBC erreur: {e}")
            listings = []

        if not listings:
            raise HTTPException(status_code=404, detail="Aucune annonce trouvée pour ce véhicule.")

        prices = [a["prix"] for a in listings]
        calc = calculate_estimation(prices, req.marque, req.modele, req.motorisation, req.finition, req.boite, req.annee, req.kilometrage)

        return {
            "vehicule": {
                "marque": req.marque.upper(),
                "modele": req.modele.upper(),
                "annee": req.annee,
                "kilometrage": req.kilometrage,
                "motorisation": req.motorisation or None,
                "boite": req.boite or None,
                "carburant": req.carburant or None,
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
            "listings": listings,
        }

    try:
        return await asyncio.wait_for(_run(), timeout=180)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Délai de scraping dépassé.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[estimation/details] Erreur: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/debug/lbc-raw")
async def debug_lbc_raw(marque: str = "Audi", modele: str = "Q2", annee: int = 2018, km: int = 101000):
    """Debug — log toutes les requêtes POST faites par LBC pendant une recherche."""
    from scrapers.leboncoin import _build_search_url, _get_pw_context
    import asyncio as _asyncio
    ctx = await _asyncio.wait_for(_get_pw_context(), timeout=40)
    page = await ctx.new_page()
    captured_requests = []
    def on_req(req):
        if req.method == "POST":
            captured_requests.append({
                "url": req.url,
                "method": req.method,
                "body_preview": (req.post_data or "")[:200],
            })
    all_requests = []
    def on_req(req):
        all_requests.append({"url": req.url[:120], "method": req.method})
    page.on("request", on_req)
    url = _build_search_url(marque, modele, annee)
    page_title = ""
    page_html_preview = ""
    try:
        await page.goto(url, wait_until="networkidle", timeout=40_000)
        page_title = await page.title()
        page_html_preview = (await page.content())[:500]
    except Exception as e:
        page_title = f"ERROR: {e}"
    await page.close()
    post_reqs = [r for r in all_requests if r["method"] == "POST"]
    api_reqs = [r for r in all_requests if "api.leboncoin" in r["url"] or "finder" in r["url"]]
    return {
        "url_used": url,
        "page_title": page_title,
        "page_html_preview": page_html_preview,
        "post_requests": post_reqs,
        "api_related_requests": api_reqs,
        "total_requests": len(all_requests),
    }


@app.get("/debug/lbc-cote")
async def debug_lbc_cote(ad_id: str = "3079197187"):
    """
    Teste plusieurs endpoints API LBC pour récupérer les détails d'une annonce
    (incluant éventuellement la côte / prix équitable).
    """
    from scrapers.leboncoin import _mobile_ua, _webshare_proxies
    from curl_cffi.requests import AsyncSession

    ua, impersonate, headers = _mobile_ua()
    proxies = _webshare_proxies()

    # Cherche l'annonce via finder/search avec list_id — retourne le JSON brut complet
    payload = {
        "filters": {
            "category": {"id": "2"},
            "keywords": {},
            "location": {"regions": [], "departments": [], "cities": [], "area": None},
            "ranges": {},
            "enums": {},
        },
        "include_locations_nearby": False,
        "list_ids": [int(ad_id)],
        "limit": 1,
        "offset": 0,
        "pivot": None,
        "sort_by": "time",
        "sort_order": "desc",
    }

    result = {}
    async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
        await s.get("https://www.leboncoin.fr/", headers=headers, timeout=15)
        try:
            r = await s.post("https://api.leboncoin.fr/finder/search",
                             json=payload, headers=headers, timeout=20)
            result["status"] = r.status_code
            if r.ok:
                data = r.json()
                ads = data.get("ads", [])
                result["nb_ads"] = len(ads)
                result["ad_raw"] = ads[0] if ads else None
                result["all_keys"] = list(ads[0].keys()) if ads else []
            else:
                result["body"] = r.text[:500]
        except Exception as e:
            result["error"] = str(e)

    return {"ad_id": ad_id, "result": result}


# ─── Bonnes Affaires Scanner ──────────────────────────────────────────────────

_FUEL_LABELS_FR = {"1": "Essence", "2": "Diesel", "3": "Hybride", "4": "Électrique",
                   "5": "GPL", "6": "GNV", "8": "Hybride rechargeable"}
_GEAR_LABELS_FR = {"1": "Manuelle", "2": "Automatique"}


async def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
    except Exception as e:
        logger.warning(f"[telegram] Erreur envoi: {e}")


async def _supabase_get_existing_ids() -> set:
    """Retourne les external_id LBC déjà en base pour éviter les doublons."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return set()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/bonnes_affaires?select=external_id&source=eq.leboncoin&is_active=eq.true",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                },
            )
            if r.status_code == 200:
                return {row["external_id"] for row in r.json()}
    except Exception as e:
        logger.warning(f"[supabase] get existing ids: {e}")
    return set()


async def _supabase_upsert_bonnes_affaires(records: list[dict]) -> int:
    """Insère les nouvelles bonnes affaires en Supabase. Retourne le nombre inséré."""
    if not records or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/bonnes_affaires",
                json=records,
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=ignore-duplicates",
                },
            )
            if r.status_code in (200, 201):
                return len(records)
            logger.warning(f"[supabase] upsert status {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[supabase] upsert error: {e}")
    return 0


async def _scan_lbc_bonnes_affaires(max_pages: int = 15, seuil_pct: int = 10) -> list[dict]:
    """Scanne LBC (toute France, toutes marques) et retourne les annonces sous la côte de seuil_pct %."""
    from scrapers.leboncoin import _mobile_ua, _webshare_proxies, API_URL as LBC_API_URL, HOMEPAGE as LBC_HP

    ua, impersonate, headers = _mobile_ua()
    proxies = _webshare_proxies()
    bonnes = []

    async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
        await s.get(LBC_HP, headers=headers, timeout=15)

        for page in range(1, max_pages + 1):
            payload = {
                "filters": {
                    "category": {"id": "2"},
                    "keywords": {},
                    "location": {"regions": [], "departments": [], "cities": [], "area": None},
                    "ranges": {},
                    "enums": {},
                },
                "include_locations_nearby": False,
                "limit": 35,
                "offset": (page - 1) * 35,
                "sort_by": "time",
                "sort_order": "desc",
            }
            try:
                r = await s.post(LBC_API_URL, json=payload, headers=headers, timeout=30)
            except Exception as e:
                logger.warning(f"[scan-ba] page {page} erreur: {e}")
                break

            if not r.ok:
                logger.warning(f"[scan-ba] page {page} status {r.status_code}")
                break

            ads = r.json().get("ads", [])
            if not ads:
                break

            logger.info(f"[scan-ba] page {page}: {len(ads)} annonces")

            for ad in ads:
                # Particuliers uniquement
                if ad.get("owner", {}).get("type") != "particulier":
                    continue

                raw_attrs = ad.get("attributes", [])
                attrs_v = {a["key"]: a.get("value", "") for a in raw_attrs}
                attrs_l = {a["key"]: a.get("value_label", "") for a in raw_attrs}

                cote_min_raw = attrs_v.get("car_price_min")
                cote_max_raw = attrs_v.get("car_price_max")
                if not cote_min_raw:
                    continue

                try:
                    cote_min = int(cote_min_raw)
                    cote_max = int(cote_max_raw) if cote_max_raw else cote_min
                except (ValueError, TypeError):
                    continue

                price_raw = ad.get("price", [])
                prix = price_raw[0] if isinstance(price_raw, list) and price_raw else None
                if not prix or not (500 <= int(prix) <= 150_000):
                    continue
                prix = int(prix)

                # Filtre: au moins seuil_pct% sous la côte min
                if prix >= cote_min * (1 - seuil_pct / 100):
                    continue

                ecart_eur = cote_min - prix
                ecart_pct = round((ecart_eur / cote_min) * 100)

                list_id = str(ad.get("list_id", ""))
                location = ad.get("location", {})
                images = ad.get("images", {})
                image_url = images.get("thumb_url") or (images.get("urls", [None])[0] or "")
                owner = ad.get("owner", {})
                dept_id = location.get("department_id", "")
                dept_name = location.get("department_name", "")

                energie_code = attrs_v.get("fuel", "")
                energie = _FUEL_LABELS_FR.get(energie_code, attrs_l.get("fuel", ""))
                boite_code = attrs_v.get("gearbox", "")
                boite = _GEAR_LABELS_FR.get(boite_code, attrs_l.get("gearbox", ""))

                pub_date = (ad.get("first_publication_date") or "")[:10] or None

                bonnes.append({
                    "source": "leboncoin",
                    "external_id": list_id,
                    "url_annonce": ad.get("url") or f"https://www.leboncoin.fr/ad/voitures/{list_id}",
                    "titre": ad.get("subject", ""),
                    "marque": attrs_l.get("brand", attrs_v.get("brand", "")),
                    "modele": attrs_l.get("model", attrs_v.get("model", "")),
                    "annee": int(attrs_v["regdate"]) if attrs_v.get("regdate", "").isdigit() else None,
                    "kilometrage": int(attrs_v["mileage"]) if attrs_v.get("mileage", "").isdigit() else None,
                    "prix_annonce": prix,
                    "valeur_marche": cote_min,
                    "ecart_eur": ecart_eur,
                    "ecart_pct": ecart_pct,
                    "energie": energie,
                    "boite": boite,
                    "vendeur_type": owner.get("type", "particulier"),
                    "region": f"{dept_name} ({dept_id})" if dept_id else dept_name,
                    "ville": location.get("city", ""),
                    "image_url": image_url,
                    "date_publication": pub_date,
                    "is_active": True,
                })

    return bonnes


@app.post("/scan/bonnes-affaires")
async def scan_bonnes_affaires():
    """Scanne LBC pour les bonnes affaires (prix < côte -10%) et notifie via Telegram."""
    logger.info("[scan-ba] Démarrage scan bonnes affaires")

    try:
        bonnes = await asyncio.wait_for(_scan_lbc_bonnes_affaires(max_pages=15, seuil_pct=10), timeout=180)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Scan timeout")

    if not bonnes:
        logger.info("[scan-ba] Aucune bonne affaire trouvée")
        return {"nouvelles": 0, "total_scanne": 0}

    # Filtrer les doublons déjà en base
    existing_ids = await _supabase_get_existing_ids()
    nouvelles = [b for b in bonnes if b["external_id"] not in existing_ids]

    logger.info(f"[scan-ba] {len(bonnes)} trouvées, {len(nouvelles)} nouvelles")

    # Sauvegarder en Supabase
    saved = await _supabase_upsert_bonnes_affaires(nouvelles)

    # Notifier via Telegram
    if nouvelles:
        lines = [f"🔥 <b>{len(nouvelles)} bonne(s) affaire(s) LBC détectée(s) !</b>\n"]
        for b in nouvelles[:5]:
            km_str = f"{b['kilometrage']:,} km".replace(",", " ") if b.get("kilometrage") else "km n/c"
            lines.append(
                f"<b>{b['marque']} {b['modele']}</b> {b.get('annee', '')} — "
                f"<b>{b['prix_annonce']:,}€</b> (côte {b['valeur_marche']:,}€, -{b['ecart_pct']}%)\n"
                f"📍 {b['ville']} {b['region']}\n"
                f"🔗 {b['url_annonce']}\n"
            )
        if len(nouvelles) > 5:
            lines.append(f"... et {len(nouvelles) - 5} autre(s). Voir l'onglet Bonnes Affaires dans l'admin.")
        await _send_telegram("\n".join(lines))

    return {"nouvelles": len(nouvelles), "sauvegardees": saved, "total_scanne": len(bonnes)}


# ─── Immatriculation lookup ───────────────────────────────────────────────────

IMMAT_API_USERNAME = os.getenv("IMMAT_API_USERNAME", "Macken97")
IMMAT_API_KEY = os.getenv("IMMAT_API_KEY", "")

# Catalog (finitions) chargé une seule fois au démarrage
_catalog: dict = {}
try:
    _catalog_path = os.path.join(os.path.dirname(__file__), "catalog.json")
    with open(_catalog_path, encoding="utf-8") as _f:
        _catalog = _json.load(_f)
    logger.info(f"[catalog] chargé ({len(_catalog.get('finitions', {}))} marques avec finitions)")
except Exception as _e:
    logger.warning(f"[catalog] non chargé: {_e}")

# Caradisiac catalog (versions par marque/modele/annee/carburant)
_caradisiac_catalog: dict = {}
try:
    _caradisiac_path = os.path.join(os.path.dirname(__file__), "caradisiac_catalog.json")
    with open(_caradisiac_path, encoding="utf-8") as _f:
        _caradisiac_catalog = _json.load(_f)
    _total_v = sum(len(vs) for b in _caradisiac_catalog.values() for m in b.values() for y in m.values() for vs in y.values())
    logger.info(f"[caradisiac] chargé ({len(_caradisiac_catalog)} marques, {_total_v} versions)")
except Exception as _e:
    logger.warning(f"[caradisiac] non chargé: {_e}")

def _normalize(s: str) -> str:
    """Normalise un nom pour comparaison souple (lowercase, sans accents ni tirets)."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("-", " ").replace("_", " ").strip()

_CARADISIAC_FUEL_MAP = {
    "essence": "essence",
    "diesel": "diesel",
    "hybride": "hybride",
    "hybride rechargeable": "hybride",
    "électrique": "electrique",
    "electrique": "electrique",
    "gpl": "gpl",
    "gnv": "gpl",
}

_VARIANT_SUFFIXES = [" sw", " break", " estate", " touring", " sportback", " variant", " e-tech", " combi", " active tourer"]

def _lookup_versions_in_brand(brand_data: dict, m_norm: str, year_str: str, fuel_key: str) -> list[str]:
    versions = []
    for cat_model, year_data in brand_data.items():
        cat_m_norm = _normalize(cat_model)
        if not cat_m_norm.startswith(m_norm):
            continue
        year_entry = year_data.get(year_str, {})
        versions.extend(year_entry.get(fuel_key, []))
    return versions

def _get_caradisiac_versions(brand: str, model: str, annee: int, carburant: str) -> list[str]:
    """Retourne les versions Caradisiac pour brand/model/annee/carburant."""
    if not _caradisiac_catalog or not brand or not model or not annee:
        return []
    fuel_key = _CARADISIAC_FUEL_MAP.get(carburant.lower(), "essence")
    year_str = str(annee)
    b_norm = _normalize(brand)
    m_norm = _normalize(model)
    brand_data = None
    for b_key in _caradisiac_catalog:
        if _normalize(b_key) == b_norm:
            brand_data = _caradisiac_catalog[b_key]
            break
    if not brand_data:
        return []

    # Essai direct
    versions = _lookup_versions_in_brand(brand_data, m_norm, year_str, fuel_key)
    if versions:
        return sorted(set(versions))

    # Fallback : supprimer les suffixes de variante (SW, Break, Sportback…)
    for suffix in _VARIANT_SUFFIXES:
        if m_norm.endswith(suffix):
            base_norm = m_norm[:-len(suffix)].strip()
            if base_norm:
                versions = _lookup_versions_in_brand(brand_data, base_norm, year_str, fuel_key)
                if versions:
                    return sorted(set(versions))

    return []


def _get_finitions_from_catalog(brand: str, model: str) -> list[str]:
    finitions_db = _catalog.get("finitions", {})
    b_norm = _normalize(brand)
    m_norm = _normalize(model)
    for b_key, models in finitions_db.items():
        if _normalize(b_key) == b_norm:
            for m_key, fins in models.items():
                if _normalize(m_key) == m_norm:
                    return [f for f in fins if f and f != "Autre"]
    return []

_FUEL_MAP: dict[str, str] = {
    # Noms directs API
    "ESSENCE": "Essence",
    "DIESEL": "Diesel",
    "ELECTRIQUE": "Électrique",
    "ÉLECTRIQUE": "Électrique",
    "HYBRIDE RECHARGEABLE": "Hybride rechargeable",
    "HYBRIDE PLUG-IN": "Hybride rechargeable",
    "PLUG-IN HYBRID": "Hybride rechargeable",
    "PLUG IN HYBRID": "Hybride rechargeable",
    "PHEV": "Hybride rechargeable",
    "HYBRIDE": "Hybride",
    "GPL": "GPL",
    "GNV": "GNV",
    "GAZ NATUREL": "GNV",
    "HYDROGENE": "Hydrogène",
    "HYDROGÈNE": "Hydrogène",
    # Codes carte grise française (SIV)
    "GO": "Diesel",
    "GAZOLE": "Diesel",
    "GASOIL": "Diesel",
    "GAS-OIL": "Diesel",
    "ES": "Essence",
    "SUPERETHANOL": "Essence",
    "PETROL": "Essence",
    "GASOLINE": "Essence",
    "EH": "Hybride",
    "ESSENCE-ELEC": "Hybride",
    "ESSENCE-ELEC RECHARGEABLE": "Hybride rechargeable",
    "ESSENCE/ELECTRIQUE": "Hybride rechargeable",
    "ELECTRIC/ESSENCE": "Hybride rechargeable",
    "ELECTRIC/DIESEL": "Hybride rechargeable",
    "EL": "Électrique",
    "ELECTRIC": "Électrique",
    "GP": "GPL",
    "GN": "GNV",
    "H2": "Hydrogène",
    "FE": "Essence",
}

_BOITE_MAP: dict[str, str] = {
    "MECANIQUE": "Manuelle",
    "MANUELLE": "Manuelle",
    "MANUAL": "Manuelle",
    "BVM": "Manuelle",
    "AUTOMATIQUE": "Automatique",
    "AUTOMATIC": "Automatique",
    "BVA": "Automatique",
    "CVT": "Automatique",
    "DCT": "Automatique",
    "ROBOTISEE": "Automatique",
    "ROBOTISÉE": "Automatique",
}


@app.get("/lookup-plate")
async def lookup_plate(plate: str):
    if not IMMAT_API_KEY:
        raise HTTPException(status_code=503, detail="API immatriculation non configurée (clé manquante)")

    plate_clean = plate.upper().replace(" ", "").replace("-", "")
    url = (
        f"https://www.immatriculationapi.com/api/reg.asmx/CheckFrance"
        f"?RegistrationNumber={plate_clean}"
        f"&username={IMMAT_API_USERNAME}"
        f"&licensekey={IMMAT_API_KEY}"
    )

    try:
        async with AsyncSession() as session:
            resp = await asyncio.wait_for(session.get(url), timeout=10)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout API immatriculation")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Erreur API immatriculation ({resp.status_code})")

    try:
        root = ET.fromstring(resp.text)
        ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
        prefix = f"{{{ns}}}" if ns else ""
        vehicle_json_el = root.find(f"{prefix}vehicleJson")
        if vehicle_json_el is None or not vehicle_json_el.text:
            raise ValueError("vehicleJson manquant dans la réponse")
        data = _json.loads(vehicle_json_el.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur parsing réponse: {e}")

    def _text(field: str) -> str:
        val = data.get(field)
        if isinstance(val, dict):
            return val.get("CurrentTextValue", "") or ""
        return str(val) if val else ""

    marque = _text("CarMake")
    modele = _text("CarModel")
    fuel_raw = _text("FuelType").upper().strip()
    boite_raw = _text("Transmission").upper().strip()
    annee_raw = _text("RegistrationYear") or _text("YearOfManufacture") or ""
    nb_portes_raw = _text("NumberOfDoors") or _text("Doors") or ""

    # Motorisation : on essaie plusieurs champs de l'API
    # On exclut les valeurs qui répètent juste marque+modèle (inutile)
    motorisation_raw = (
        _text("EngineDescription")
        or _text("Trim")
        or _text("ModelVariant")
        or ""
    )
    # Si la valeur contient juste marque/modèle, on vide
    _marque_clean = marque.lower().strip()
    _modele_clean = modele.lower().strip()
    if motorisation_raw and (_marque_clean in motorisation_raw.lower() or _modele_clean in motorisation_raw.lower()):
        motorisation_raw = ""

    # Données étendues SIV
    extended = data.get("ExtendedData") or {}
    lib_version = extended.get("libVersion", "") if isinstance(extended, dict) else ""
    puissance_kw_raw = str(extended.get("puissanceDyn", "") or "").strip()
    engine_cc = str(extended.get("EngineCC", "") or "").strip()

    # Litrage arrondi (ex: 1991 → 2.0L)
    litrage = ""
    try:
        if engine_cc:
            litrage = f"{int(engine_cc) / 1000:.1f}L"
    except Exception:
        pass

    # puissanceDyn est en kW → convertir en CV (1 kW = 1.3596 CV)
    puissance_cv = ""
    try:
        if puissance_kw_raw:
            cv = round(int(puissance_kw_raw) * 1.3596)
            puissance_cv = f"{cv}ch"
    except Exception:
        pass

    # Motorisation complète : version SIV — litrage CV
    specs = " ".join(filter(None, [litrage, puissance_cv]))
    motorisation_complete = " — ".join(filter(None, [lib_version, specs]))

    carburant = _FUEL_MAP.get(fuel_raw, fuel_raw.capitalize() if fuel_raw else "")
    boite = _BOITE_MAP.get(boite_raw, "Automatique")

    try:
        annee = int(str(annee_raw)[:4]) if annee_raw else None
    except Exception:
        annee = None

    try:
        nb_portes = int(nb_portes_raw) if nb_portes_raw else None
    except Exception:
        nb_portes = None

    # Finitions depuis catalogue backend (Renault, Peugeot, Dacia, etc.)
    finitions = _get_finitions_from_catalog(marque, modele)

    # Log pour debug — affiche tous les champs retournés par l'API
    logger.info(f"[lookup-plate] {plate_clean} → {marque} {modele} {annee} {carburant} {boite} | {motorisation_complete}")

    return {
        "marque": marque,
        "modele": modele,
        "annee": annee,
        "carburant": carburant,
        "boite": boite,
        "nb_portes": nb_portes,
        "motorisation": motorisation_complete,
        "finitions": finitions,
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
    """Estime la valeur marché via LeBonCoin API mobile uniquement."""
    marque_search = _resolve_brand(marque, modele)
    type_vehicule = _detect_type_vehicule(modele)
    annee_eff = annee or 2015
    km_eff = km or 100000

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
