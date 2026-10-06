import vm_ab_catalog
import asyncio
import statistics
import base64
import datetime
import json as _json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()  # Charge .env en local ; les variables Railway ont priorité en prod

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
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
    LeboncoinScraper, cote_voiture_identique,
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

def _ip_visiteur(request: Request) -> str:
    """Vraie adresse du visiteur : derrière Render (et son relais Cloudflare), request.client change à chaque demande.
    Cloudflare transmet l'adresse réelle dans CF-Connecting-IP / True-Client-IP (il écrase ce que le visiteur envoie)."""
    for entete in ("cf-connecting-ip", "true-client-ip"):
        if request.headers.get(entete):
            return request.headers[entete].strip()
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() if xff.strip() else get_remote_address(request)


limiter = Limiter(key_func=_ip_visiteur)
app = FastAPI(
    title="VM Auto Estimation API",
    description="API de rachat de véhicules d'occasion — VM Auto Business (Seine-et-Marne)",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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


@app.get("/catalog/marques")
@limiter.limit("30/minute")
async def catalog_marques(request: Request):
    """Marques du catalogue VM Auto Business (listes déroulantes de l'app)."""
    return {"marques": vm_ab_catalog.list_marques()}


@app.get("/catalog/modeles")
@limiter.limit("30/minute")
async def catalog_modeles(request: Request, marque: str = ""):
    return {"modeles": vm_ab_catalog.list_modeles(marque)}


def _cle_ok(request: Request) -> bool:
    secret = os.getenv("CATALOG_SECRET", "")
    return not secret or request.headers.get("x-catalog-key", "") == secret


@app.get("/immo/recherche")
@limiter.limit("20/minute")
async def immo_recherche(request: Request, ville: str, code_postal: str = "", type_bien: str = "appartement",
                         prix_max: int = 0, surface_min: int = 0, pieces_min: int = 0, limite: int = 35):
    """Biens immobiliers à vendre sur Leboncoin (alertes et recherches de Valma). Réservé à l'app (clé secrète)."""
    if not _cle_ok(request):
        raise HTTPException(status_code=403, detail="Accès réservé")
    from scrapers import immo
    return await immo.rechercher(ville, code_postal, type_bien, prix_max, surface_min, pieces_min, limite)


@app.get("/immo/chasse")
@limiter.limit("10/minute")
async def immo_chasse(request: Request, strategie: str = "travaux", ville: str = "", code_postal: str = "", departement: str = "",
                      prix_max: int = 0, chambres_min: int = 0, limite: int = 12):
    """Biens à rénover, immeubles de rapport ou biens pour la colocation, comparés au marché de leur ville et notés. Réservé à l'app."""
    if not _cle_ok(request):
        raise HTTPException(status_code=403, detail="Accès réservé")
    if not ville and not departement:
        raise HTTPException(status_code=400, detail="ville ou departement requis")
    from scrapers import immo
    return await immo.chasser(strategie, ville, code_postal, departement, prix_max, chambres_min, limite)


@app.get("/immo/ville")
@limiter.limit("20/minute")
async def immo_ville(request: Request, ville: str, code_postal: str = ""):
    """La ville se prête-t-elle à la colocation ? (étudiants, écoles, transports, demande locative). Réservé à l'app."""
    if not _cle_ok(request):
        raise HTTPException(status_code=403, detail="Accès réservé")
    from scrapers import immo, ville as villes_mod
    ref = await immo._references(ville, code_postal)
    return await villes_mod.analyser(ville, code_postal, ref.get("loyer_chambre"), ref.get("nb_chambres_louer", 0))


@app.get("/immo/loyer")
@limiter.limit("20/minute")
async def immo_loyer(request: Request, ville: str, code_postal: str = "", type_bien: str = "appartement", surface: int = 0):
    """Loyer médian au m² des locations en ligne de la ville (estimation de rentabilité). Réservé à l'app."""
    if not _cle_ok(request):
        raise HTTPException(status_code=403, detail="Accès réservé")
    from scrapers import immo
    return await immo.loyer_m2(ville, code_postal, type_bien, surface)


@app.get("/catalog/carrosseries")
@limiter.limit("20/minute")
async def catalog_carrosseries(request: Request, marque: str = "", modele: str = "", annee: int = 0):
    await _maj_reglages_catalogue()
    """Carrosseries existantes pour ce modèle (types Leboncoin), la plus courante d'abord ; vide pour un utilitaire."""
    return {"carrosseries": vm_ab_catalog.carrosseries(marque, modele, annee)}


@app.get("/catalog/carburants")
@limiter.limit("20/minute")
async def catalog_carburants(request: Request, marque: str = "", modele: str = "", annee: int = 0):
    await _maj_reglages_catalogue()
    return {"carburants": vm_ab_catalog.carburants(marque, modele, annee)}


@app.get("/catalog/fiche")
@limiter.limit("60/minute")
async def catalog_fiche(request: Request, marque: str = "", modele: str = ""):
    """Toutes les versions d'un modèle (années, carburant, annonces vues) : page Catalogue de l'app VM.
    Réservée à l'app (clé CATALOG_SECRET envoyée par la fonction Supabase « catalogue », après connexion)."""
    secret = os.getenv("CATALOG_SECRET", "")
    if secret and request.headers.get("x-catalog-key", "") != secret:
        raise HTTPException(status_code=403, detail="Accès réservé")
    versions = vm_ab_catalog.fiche_modele(marque, modele)
    return {"versions": versions, "count": len(versions), "catalogue": vm_ab_catalog.stats()}


@app.get("/catalog/versions")
@limiter.limit("10/minute")
async def catalog_versions(request: Request, marque: str = "", modele: str = "", annee: int = 0, carburant: str = "", carrosserie: str = ""):
    await _maj_reglages_catalogue()
    versions = _get_vm_catalog_versions(marque, modele, annee, carburant, carrosserie)
    return {"versions": versions, "count": len(versions)}


DS_CITROEN_MODELS = {"DS3", "DS4", "DS5"}

def _resolve_brand(marque: str, modele: str, annee: Optional[int] = None) -> str:
    """Les DS3/DS4/DS5 d'avant 2016 sont indexées sous Citroën sur Leboncoin ; depuis 2016, sous DS."""
    if annee and annee >= 2016:
        return marque
    if marque.upper() == "DS" and modele.upper().replace(" ", "") in {m.replace(" ", "") for m in DS_CITROEN_MODELS}:
        return "Citroën"
    return marque

# Réglages de la page « Référence » de l'app VM (base vm-administration, lecture publique)
REGLAGES_URL = os.getenv("REGLAGES_URL", "https://ucefxszhdhhthcpqpfms.supabase.co/rest/v1/reglages?select=cle,valeur")
REGLAGES_KEY = os.getenv("REGLAGES_KEY", "sb_publishable_kEPpVm_LL4w4rMM99F9iqQ_bgGPAMY-")  # clé publique (publishable), sans danger
_reglages_cache: dict = {}
_reglages_time: float = 0.0


async def _get_reglages() -> dict:
    """Réglages modifiés dans l'app (cache 60 s) ; les absents gardent leur valeur par défaut."""
    global _reglages_cache, _reglages_time
    if _reglages_time and time.time() - _reglages_time < 60:
        return _reglages_cache
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(REGLAGES_URL, headers={"apikey": REGLAGES_KEY, "Authorization": f"Bearer {REGLAGES_KEY}"})
            if r.status_code == 200:
                _reglages_cache = {row["cle"]: row["valeur"] for row in r.json()}
                _reglages_time = time.time()
    except Exception as e:
        logger.warning(f"[reglages] lecture impossible : {e}")
        _reglages_time = time.time()  # pas de nouvel essai avant 60 s (les listes du site restent rapides)
    return _reglages_cache


async def _estimation_params() -> dict:
    """Paramètres du calcul (clés « estimation.xxx » de la page Référence, sans le préfixe)."""
    reg = await _get_reglages()
    return {k.split(".", 1)[1]: v for k, v in reg.items() if k.startswith("estimation.")}


async def _apply_ajustement_global(prix_rachat: int) -> int:
    """Ajustement global (en %) : page Référence de l'app VM, sinon ancien réglage de l'admin du site vitrine."""
    reg = await _get_reglages()
    settings = await _get_settings()
    ajustement = float(reg.get("estimation.ajustement_global") or settings.get("estimation.ajustement_global", "0") or "0")
    if ajustement:
        return round(prix_rachat * (1 + ajustement / 100) / 100) * 100
    return prix_rachat


async def _site_offer(prices: list[int], marque: str, modele: str, annee: Optional[int],
                      km: Optional[int], boite: Optional[str], motorisation: Optional[str] = None) -> dict:
    """Ce que le site proposerait pour ce véhicule, à partir des prix des annonces comparables
    (la version sert au malus des moteurs à problème)."""
    calc = calculate_estimation(prices, marque, modele, motorisation, None, boite, annee, km, await _estimation_params())
    return {
        "ma_cote": calc["prix_median"],
        "nb_comparables": calc["nb_annonces"],
        "prix_site": await _apply_ajustement_global(calc["prix_rachat"]),
        "fourchette_basse": calc["fourchette_basse"],
        "fourchette_haute": calc["fourchette_haute"],
    }


# Règle de cote (site et bonnes affaires), modifiable dans Réglages de l'app VM (clés « cote.* ») :
# on vise `cible` annonces à ±`fenetre_km` ; sinon fenêtre élargie de `elargissement_pct` % du kilométrage ;
# on garde les plus proches en km ; en dessous de `minimum` annonces, pas de prix.
# Bonnes affaires : au-delà de ±`ecart_max_lbc` % de la cote Leboncoin de l'annonce, la cote est suspecte.
COTE_DEFAUTS = {"cible": 10, "minimum": 4, "fenetre_km": 10_000, "elargissement_pct": 40, "ecart_max_lbc": 25,
                # Méthode « voiture identique » : même version / boîte / année, cotes Leboncoin, droite prix-km (1 = oui, 0 = non)
                "version_identique": 1, "base_lbc": 1, "droite_km": 1,
                # 1 = uniquement des voitures de la même version (jamais « même puissance » ni « toutes versions »)
                "version_stricte": 1,
                # voitures identiques minimum pour donner un prix (1 = une seule suffit, sur sa cote Leboncoin)
                "minimum_identique": 1,
                # 1 = la même version de l'année d'avant / d'après est acceptée quand l'année exacte manque
                "annee_elargie": 1,
                # 1 = SUV coupés (GLC Coupé, Cayenne Coupé, Q3 Sportback…) cotés sur les prix affichés
                "suv_coupe_prix_affiches": 1}
COTE_OUI_NON = {"version_identique", "base_lbc", "droite_km", "version_stricte", "annee_elargie", "suv_coupe_prix_affiches"}
CATALOGUE_DEFAUTS = {"seuil_pct": 2, "hybrides_legers_essence": 1}


async def _maj_reglages_catalogue() -> None:
    """Applique au catalogue les réglages de l'app (rubrique Catalogue)."""
    reg = await _get_reglages()
    try:
        seuil = float(reg.get("catalogue.seuil_pct", CATALOGUE_DEFAUTS["seuil_pct"]))
    except (TypeError, ValueError):
        seuil = CATALOGUE_DEFAUTS["seuil_pct"]
    try:
        legers = float(reg.get("catalogue.hybrides_legers_essence", 1)) >= 1
    except (TypeError, ValueError):
        legers = True
    vm_ab_catalog.REGLAGES.update(seuil=max(0.0, min(20.0, seuil)) / 100, hybrides_legers=legers)


def _suv_coupe(marque: str, modele: str, version: str) -> bool:
    """SUV coupé (GLC Coupé, GLE Coupé, Cayenne Coupé, Q3 / Q5 Sportback…) : Leboncoin lui donne la même cote que le SUV,
    alors que les vendeurs l'affichent plus cher → sa cote se calcule sur les prix affichés, pas sur la cote Leboncoin."""
    if not version:
        return False
    nom = vm_ab_catalog.nom_catalogue(marque, modele)
    if nom.lower() in ("range rover evoque", "evoque"):
        return False  # Evoque « Coupé » = 3 portes, pas plus cher
    return vm_ab_catalog._type_de_base(nom) == "SUV / 4x4" and vm_ab_catalog.carrosserie_version(version, nom) == "Coupé"


async def _cote_params() -> dict:
    reg = await _get_reglages()
    p = dict(COTE_DEFAUTS)
    for k in p:
        try:
            v = float(reg.get(f"cote.{k}", ""))
            if k in COTE_OUI_NON:
                p[k] = 1 if v >= 1 else 0
            elif v > 0:
                p[k] = int(v)
        except (TypeError, ValueError):
            pass
    p["minimum"] = max(1, min(p["minimum"], p["cible"]))
    return p


async def _run_estimation(req: EstimationRequest) -> dict:
    type_vehicule = req.type_vehicule or _detect_type_vehicule(req.modele)
    marque_search = _resolve_brand(req.marque, req.modele, req.annee)
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
    cote = await _cote_params()

    # 1. Voiture identique (version exacte du catalogue, boîte, année) + droite prix / km
    if cote["version_identique"]:
        try:
            ident = await asyncio.wait_for(cote_voiture_identique(
                marque_search, req.modele, req.annee, req.kilometrage, version=req.motorisation, boite=req.boite,
                carburant=req.carburant, minimum=cote["minimum_identique"], elargissement_pct=cote["elargissement_pct"],
                base_lbc=bool(cote["base_lbc"]) and not (cote["suv_coupe_prix_affiches"] and _suv_coupe(req.marque, req.modele, req.motorisation)),
                annee_elargie=bool(cote["annee_elargie"]),
                droite_km=bool(cote["droite_km"]), stricte=bool(cote["version_stricte"])), timeout=60)
        except Exception as e:
            logger.warning(f"[cote identique] erreur : {e}")
            ident = None
        if ident:
            calc = calculate_estimation([ident["valeur"]], req.marque, req.modele, req.motorisation, req.finition, req.boite,
                                        req.annee, req.kilometrage, await _estimation_params())
            calc["prix_rachat"] = await _apply_ajustement_global(calc["prix_rachat"])
            return {
                "vehicule": {
                    "marque": req.marque.upper(), "modele": req.modele.upper(), "annee": req.annee, "kilometrage": req.kilometrage,
                    "finition": req.finition or None, "motorisation": req.motorisation or None, "boite": req.boite or None,
                    "carburant": req.carburant or None, "type_vehicule": type_vehicule,
                },
                "marche": {
                    "nb_annonces": ident["n"], "prix_moyen": calc["prix_median"], "prix_median": calc["prix_median"],
                    "fourchette_basse": round(ident["basse"] / 100) * 100, "fourchette_haute": round(ident["haute"] / 100) * 100,
                },
                "estimation_rachat": {"prix_suggere": calc["prix_rachat"], "methode": calc["methode"]},
                "sources": {"leboncoin": {"annonces": ident["n"], "methode": f"{ident['niveau']} · {ident['base']} · " + (f"{ident['n']} voiture(s) identique(s)" if ident["n"] < 4 or "ajusté" in ident["niveau"] else "droite prix/km")}},
            }

        if cote["version_stricte"] and req.motorisation and req.motorisation.strip().lower() not in ("autre", "je ne sais pas"):
            # Réglage « uniquement la même version » : pas de repli sur d'autres versions
            raise HTTPException(status_code=404, detail="Pas assez de voitures de la même version : estimation à confirmer par téléphone.")

    # 2. Sinon : annonces proches en kilométrage (méthode précédente)
    try:
        # Règle unique de cote : `cible` annonces, sinon km élargi, puis les plus proches en km du client
        lbc_prices = await asyncio.wait_for(
            lbc.get_cote_prices(marque_search, req.modele, req.annee, req.kilometrage,
                                cible=cote["cible"], minimum=cote["minimum"], budget_s=75,
                                fenetre_km=cote["fenetre_km"], elargissement_pct=cote["elargissement_pct"], **lbc_args),
            timeout=95,
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
    if len(all_prices) < cote["minimum"]:
        # Pas assez d'annonces pour une cote fiable : pas de prix (le site propose une réponse sous 24 h)
        raise HTTPException(
            status_code=404,
            detail=f"Seulement {len(all_prices)} annonce(s) comparable(s) : estimation à confirmer par téléphone.",
        )

    calc = calculate_estimation(all_prices, req.marque, req.modele, req.motorisation, req.finition, req.boite, req.annee, req.kilometrage,
                                await _estimation_params())

    calc["prix_rachat"] = await _apply_ajustement_global(calc["prix_rachat"])

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


def cote_version_stricte_ok(version: str) -> bool:
    return bool(version) and version.strip().lower() not in ("autre", "je ne sais pas")


@app.post("/estimation/details")
async def estimation_details(req: EstimationRequest):
    """Comme /estimation mais retourne aussi la liste brute des annonces LBC (prix, km, titre, url)."""
    async def _run():
        type_vehicule = req.type_vehicule or _detect_type_vehicule(req.modele)
        marque_search = _resolve_brand(req.marque, req.modele, req.annee)
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

        # Version choisie : la liste ne garde que cette version (un GLC Coupé n'est pas mêlé aux GLC SUV)
        if req.motorisation and cote_version_stricte_ok(req.motorisation):
            from scrapers.leboncoin import _norm_version
            v = _norm_version(req.motorisation)
            memes = [a for a in listings if a.get("version") and _norm_version(a["version"]) == v]
            if memes:
                listings = memes
            elif any(a.get("version") for a in listings):
                # Pas d'annonce de cette version exacte : on garde au moins la même carrosserie
                nom = vm_ab_catalog.nom_catalogue(req.marque, req.modele)
                an = int(getattr(req, "annee", 0) or 0)
                car = vm_ab_catalog.carrosserie_version(req.motorisation, nom, an)
                listings = [a for a in listings if not a.get("version")
                            or vm_ab_catalog.carrosserie_version(a["version"], nom, an) == car] or listings

        prices = [a["prix"] for a in listings]
        # Mêmes pourcentages que le site (Réglages de l'app) et même ajustement global
        calc = calculate_estimation(prices, req.marque, req.modele, req.motorisation, req.finition, req.boite, req.annee, req.kilometrage,
                                    await _estimation_params())
        calc["prix_rachat"] = await _apply_ajustement_global(calc["prix_rachat"])

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

# Codes Leboncoin vérifiés le 03/10/2026
_FUEL_LABELS_FR = {"1": "Essence", "2": "Diesel", "3": "GPL", "4": "Électrique", "5": "Autre",
                   "6": "Hybride", "7": "GNV", "8": "Hybride rechargeable"}
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


async def _scan_lbc_bonnes_affaires(max_pages: int = 50, seuil_pct: int = 10,
                                    known_ids: Optional[set] = None, age_max: int = 10,
                                    km_max: int = 130_000) -> tuple[list[dict], dict]:
    """Scanne LBC (toute France, toutes marques) et retourne les annonces sous la côte de seuil_pct %.
    S'arrête dès qu'une annonce déjà connue est rencontrée (scan intelligent)."""
    from scrapers.leboncoin import _mobile_ua, _webshare_proxies, API_URL as LBC_API_URL, HOMEPAGE as LBC_HP

    ua, impersonate, headers = _mobile_ua()
    proxies = _webshare_proxies()
    bonnes = []
    stats = {"pages": 0, "raw_ads": 0, "pros_exclus": 0, "sans_cote": 0, "hors_fourchette": 0}

    # Charger les IDs déjà en base pour arrêter dès qu'on retombe sur du connu
    # known_ids : annonces déjà connues de l'appelant (app VM) ; sinon celles de la base du site vitrine
    existing_ids = known_ids if known_ids is not None else await _supabase_get_existing_ids()
    logger.info(f"[scan-ba] {len(existing_ids)} annonces déjà en base")

    async with AsyncSession(impersonate=impersonate, proxies=proxies) as s:
        try:
            await s.get(LBC_HP, headers=headers, timeout=15)
        except Exception as e:
            logger.warning(f"[scan-ba] homepage LBC inaccessible: {e}")
            return [], stats

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

            stats["pages"] += 1
            stats["raw_ads"] += len(ads)
            logger.info(f"[scan-ba] page {page}: {len(ads)} annonces")

            stop_scan = False
            for ad in ads:
              try:
                list_id = str(ad.get("list_id", ""))
                if list_id and list_id in existing_ids:
                    logger.info(f"[scan-ba] annonce déjà connue ({list_id}), arrêt du scan")
                    stop_scan = True
                    break

                # Particuliers uniquement (LBC renvoie "private" pour particulier)
                if ad.get("owner", {}).get("type") == "pro":
                    stats["pros_exclus"] += 1
                    continue

                raw_attrs = ad.get("attributes", [])
                attrs_v = {a["key"]: a.get("value", "") for a in raw_attrs}
                attrs_l = {a["key"]: a.get("value_label", "") for a in raw_attrs}

                # Filtre âge : moins de 10 ans
                annee_v = int(attrs_v["regdate"]) if attrs_v.get("regdate", "").isdigit() else None
                if annee_v and annee_v < datetime.date.today().year - age_max:
                    stats["hors_fourchette"] += 1
                    continue

                # Filtre km : moins de 130 000 km
                km_v = int(attrs_v["mileage"]) if attrs_v.get("mileage", "").isdigit() else None
                if km_v and km_v >= km_max:
                    stats["hors_fourchette"] += 1
                    continue

                cote_min_raw = attrs_v.get("car_price_min")
                cote_max_raw = attrs_v.get("car_price_max")
                if not cote_min_raw:
                    stats["sans_cote"] += 1
                    continue

                try:
                    cote_min = int(cote_min_raw)
                    cote_max = int(cote_max_raw) if cote_max_raw else cote_min
                except (ValueError, TypeError):
                    stats["sans_cote"] += 1
                    continue

                price_raw = ad.get("price", [])
                prix = price_raw[0] if isinstance(price_raw, list) and price_raw else None
                if not prix or not (500 <= int(prix) <= 150_000):
                    stats["hors_fourchette"] += 1
                    continue
                prix = int(prix)

                # Filtre: au moins seuil_pct% sous la côte min
                if prix >= cote_min * (1 - seuil_pct / 100):
                    stats["hors_fourchette"] += 1
                    continue

                ecart_eur = cote_min - prix
                ecart_pct = round((ecart_eur / cote_min) * 100)

                location = ad.get("location", {})
                images = ad.get("images", {}) or {}
                _img_list = images.get("urls_large") or images.get("urls") or []
                image_url = (_img_list[0] if isinstance(_img_list, list) and _img_list else None) or images.get("thumb_url") or ""
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
                    "cote_max": cote_max,
                    "ecart_eur": ecart_eur,
                    "ecart_pct": ecart_pct,
                    "energie": energie,
                    "boite": boite,
                    "version": attrs_l.get("u_car_version") or attrs_v.get("u_car_version") or None,
                    "vendeur_type": owner.get("type", "particulier"),
                    "region": f"{dept_name} ({dept_id})" if dept_id else dept_name,
                    "ville": location.get("city", ""),
                    "image_url": image_url,
                    "date_publication": pub_date,
                    "is_active": True,
                })
              except Exception as e:
                logger.warning(f"[scan-ba] annonce ignorée ({e})")

            if stop_scan:
                break

    logger.info(f"[scan-ba] stats: {stats}")
    return bonnes, stats


class ScanListeRequest(BaseModel):
    max_pages: int = 10
    seuil_pct: int = 10
    known_ids: list[str] = []
    age_max: int = 10
    km_max: int = 130_000


@app.post("/scan/bonnes-affaires/liste")
async def scan_bonnes_affaires_liste(req: ScanListeRequest):
    """Même stratégie que /scan/bonnes-affaires (toute la France, cote LeBonCoin -10 %), sans enregistrement
    ni alerte : l'app VM enregistre et prévient elle-même."""
    try:
        bonnes, stats = await asyncio.wait_for(
            _scan_lbc_bonnes_affaires(max_pages=min(req.max_pages, 20), seuil_pct=req.seuil_pct,
                                      known_ids=set(req.known_ids), age_max=req.age_max, km_max=req.km_max),
            timeout=120,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Scan timeout")
    for b in bonnes:
        b["cote_lbc_min"] = b["valeur_marche"]
        b["cote_lbc_max"] = b.pop("cote_max", b["valeur_marche"])
    return {"listings": bonnes, "stats": stats}


@app.post("/scan/bonnes-affaires")
async def scan_bonnes_affaires():
    """Scanne LBC pour les bonnes affaires (prix < côte -10%) et notifie via Telegram."""
    logger.info("[scan-ba] Démarrage scan bonnes affaires")

    try:
        bonnes, stats = await asyncio.wait_for(_scan_lbc_bonnes_affaires(max_pages=15, seuil_pct=10), timeout=180)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Scan timeout")

    if not bonnes:
        logger.info(f"[scan-ba] Aucune bonne affaire trouvée. Stats: {stats}")
        return {"nouvelles": 0, "total_scanne": 0, "stats": stats}

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

    return {"nouvelles": len(nouvelles), "sauvegardees": saved, "total_scanne": len(bonnes), "stats": stats}


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
_vm_catalog: dict = {}
try:
    _vm_catalog_path = os.path.join(os.path.dirname(__file__), "vm_catalog.json")
    with open(_vm_catalog_path, encoding="utf-8") as _f:
        _vm_catalog = _json.load(_f)
    _total_v = sum(len(vs) for b in _vm_catalog.values() for m in b.values() for y in m.values() for vs in y.values())
    logger.info(f"[vm-catalog] chargé ({len(_vm_catalog)} marques, {_total_v} versions)")
except Exception as _e:
    logger.warning(f"[vm-catalog] non chargé: {_e}")

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

_VARIANT_SUFFIXES = [" sw", " break", " estate", " touring", " sportback", " variant", " e-tech", " combi"]

def _lookup_versions_in_brand(brand_data: dict, m_norm: str, year_str: str, fuel_key: str) -> list[str]:
    versions = []
    for cat_model, year_data in brand_data.items():
        cat_m_norm = _normalize(cat_model)
        if not cat_m_norm.startswith(m_norm):
            continue
        year_entry = year_data.get(year_str, {})
        versions.extend(year_entry.get(fuel_key, []))
    return versions

def _get_vm_catalog_versions(brand: str, model: str, annee: int, carburant: str, carrosserie: str = "") -> list[str]:
    """Versions du catalogue VM Auto Business ; l'ancien catalogue ne sert que si le véhicule y manque."""
    if not brand or not model or not annee:
        return []
    versions = vm_ab_catalog.get_versions(brand, model, annee, carburant, carrosserie)
    if versions:
        return versions
    if carrosserie and vm_ab_catalog.get_versions(brand, model, annee, carburant):
        return []  # le modèle existe mais pas dans cette carrosserie
    return _get_old_catalog_versions(brand, model, annee, carburant)


def _get_old_catalog_versions(brand: str, model: str, annee: int, carburant: str) -> list[str]:
    """Ancien catalogue (secours pour les véhicules absents du catalogue VM Auto Business)."""
    if not _vm_catalog or not brand or not model or not annee:
        return []
    fuel_key = _CARADISIAC_FUEL_MAP.get(carburant.lower(), "essence")
    year_str = str(annee)
    b_norm = _normalize(brand)
    m_norm = _normalize(model)
    brand_data = None
    for b_key in _vm_catalog:
        if _normalize(b_key) == b_norm:
            brand_data = _vm_catalog[b_key]
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


def _to_int(v) -> Optional[int]:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


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
        # Cote affichée par LeBonCoin sur l'annonce (fourchette prix bas – prix haut)
        "cote_lbc_min": _to_int(attrs.get("car_price_min", {}).get("v")),
        "cote_lbc_max": _to_int(attrs.get("car_price_max", {}).get("v")),
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

async def _estimate_market_lbc(marque: str, modele: str, annee: Optional[int], km: Optional[int],
                               carburant: Optional[str] = None, boite: Optional[str] = None,
                               timeout: int = 20, motorisation: Optional[str] = None,
                               min_resultats: int = 1) -> list[int]:
    """Prix des annonces comparables sur LeBonCoin (API mobile).
    min_resultats > 1 : la recherche continue tant qu'elle a trop peu d'annonces et rend le meilleur lot trouvé
    dans le délai (pas de coupure brutale qui ferait tout perdre)."""
    marque_search = _resolve_brand(marque, modele, annee)
    type_vehicule = _detect_type_vehicule(modele)
    annee_eff = annee or 2015
    km_eff = km or 100000

    try:
        lbc = LeboncoinScraper()
        if min_resultats > 1:
            # Cote : même règle que l'estimation du site (10 annonces, km ±40 %, plus proches en km)
            cote = await _cote_params()
            recherche = lbc.get_cote_prices(marque_search, modele, annee_eff, km_eff, cible=cote["cible"], minimum=min_resultats,
                                            type_vehicule=type_vehicule, carburant=carburant or None, boite=boite or None,
                                            motorisation=motorisation or None, budget_s=timeout,
                                            fenetre_km=cote["fenetre_km"], elargissement_pct=cote["elargissement_pct"])
        else:
            recherche = lbc.get_prices(marque_search, modele, annee_eff, km_eff, type_vehicule=type_vehicule,
                                       carburant=carburant or None, boite=boite or None, motorisation=motorisation or None)
        # Avec un budget, get_prices s'arrête seule ; la limite dure ne sert que de filet de sécurité
        prices = await asyncio.wait_for(recherche, timeout=timeout + 45 if min_resultats > 1 else timeout)
        if prices:
            logger.info(f"[enriched] LBC {marque} {modele} {annee} → {len(prices)} prix")
            return prices
    except Exception as e:
        logger.warning(f"[enriched] LBC {marque} {modele} {annee} erreur: {e}")

    logger.warning(f"[enriched] aucun prix trouvé pour {marque} {modele} {annee}")
    return []


class CoteAnnonceRequest(BaseModel):
    marque: str
    modele: str
    annee: Optional[int] = None
    kilometrage: Optional[int] = None
    energie: Optional[str] = None
    boite: Optional[str] = None
    version: Optional[str] = None  # version du catalogue VM Auto Business (puissance → annonces comparables)
    cote_lbc_min: Optional[int] = None  # cote affichée par Leboncoin sur l'annonce (garde-fou)
    cote_lbc_max: Optional[int] = None


@app.post("/cote-annonce")
async def cote_annonce(req: CoteAnnonceRequest):
    """Ma cote + prix que proposerait le site, pour une annonce précise (bonnes affaires)."""
    cote = await _cote_params()
    MIN_COMPARABLES = cote["minimum"]
    args = dict(marque=req.marque, modele=req.modele, annee=req.annee, km=req.kilometrage, min_resultats=MIN_COMPARABLES)
    ident = None
    if cote["version_identique"] and req.annee:
        try:
            ident = await asyncio.wait_for(cote_voiture_identique(
                _resolve_brand(req.marque, req.modele, req.annee), req.modele, req.annee, req.kilometrage, version=req.version,
                boite=req.boite, carburant=req.energie, minimum=cote["minimum_identique"], elargissement_pct=cote["elargissement_pct"],
                base_lbc=bool(cote["base_lbc"]) and not (cote["suv_coupe_prix_affiches"] and _suv_coupe(req.marque, req.modele, req.version)),
                annee_elargie=bool(cote["annee_elargie"]),
                droite_km=bool(cote["droite_km"]), stricte=bool(cote["version_stricte"])), timeout=60)
        except Exception as e:
            logger.warning(f"[cote identique] erreur : {e}")
    # Cote identique trouvée : déjà calculée, répétée pour passer les contrôles de nombre d'annonces
    prices = [ident["valeur"]] * MIN_COMPARABLES if ident else []
    if not prices and cote["version_stricte"] and req.version and cote["version_identique"]:
        raise HTTPException(status_code=404, detail="Pas assez de voitures de la même version pour une cote fiable")
    if not prices:
        prices = await _estimate_market_lbc(**args, carburant=req.energie, boite=req.boite, timeout=55, motorisation=req.version)
    if len(prices) < MIN_COMPARABLES and req.version:
        # Version trop précise : on élargit à toutes les versions du modèle
        plus = await _estimate_market_lbc(**args, carburant=req.energie, boite=req.boite, timeout=40)
        prices = plus if len(plus) > len(prices) else prices
    if len(prices) < MIN_COMPARABLES and (req.energie or req.boite):
        # Sans filtre boîte (le carburant reste : essence et diesel n'ont pas la même cote)
        plus = await _estimate_market_lbc(**args, carburant=req.energie, timeout=35)
        prices = plus if len(plus) > len(prices) else prices

    lbc_mid = (req.cote_lbc_min + req.cote_lbc_max) / 2 if req.cote_lbc_min and req.cote_lbc_max else None
    base = "comparables"
    if prices:
        median = statistics.median(prices)
        suspecte = lbc_mid is not None and abs(median - lbc_mid) / lbc_mid > cote["ecart_max_lbc"] / 100
        if lbc_mid is not None and (len(prices) < MIN_COMPARABLES or suspecte):
            logger.info(f"[cote-annonce] {req.marque} {req.modele} : {len(prices)} comparables, médiane {median:.0f} "
                        f"vs cote LBC {lbc_mid:.0f} → base cote LBC")
            prices, base = [int(lbc_mid)], "cote_lbc"
    elif lbc_mid is not None:
        prices, base = [int(lbc_mid)], "cote_lbc"
    if not prices:
        raise HTTPException(status_code=404, detail="Aucune annonce comparable trouvée")
    if base == "comparables" and len(prices) < MIN_COMPARABLES:
        raise HTTPException(status_code=404, detail=f"Seulement {len(prices)} annonce(s) comparable(s), pas assez pour une cote fiable")
    offre = await _site_offer(prices, req.marque, req.modele, req.annee, req.kilometrage, req.boite, req.version)
    nb = 0 if base == "cote_lbc" else (ident["n"] if ident else offre["nb_comparables"])
    methode = "cote_lbc" if base == "cote_lbc" else ("identique" if ident else "comparables")
    return {**offre, "base": methode, "nb_comparables": nb,
            "detail": f"{ident['niveau']} · {ident['base']}" if ident and base != "cote_lbc" else None}


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
    group_prices: dict[str, list[int]] = {}
    sem = asyncio.Semaphore(6)

    async def _est(key: str, g: dict):
        async with sem:
            group_prices[key] = await _estimate_market_lbc(g["marque"], g["modele"], g["annee"], g["km"])

    await asyncio.gather(*[_est(k, v) for k, v in groups.items()])

    # Enrichir les listings : ma cote + ce que le site proposerait (même calcul que l'estimation du site)
    enriched = []
    for l in listings:
        key = f"{(l.get('marque') or '').upper()}|{(l.get('modele') or '').upper()}|{l.get('annee') or ''}"
        prices = group_prices.get(key)
        offer = await _site_offer(prices, l.get("marque") or "", l.get("modele") or "", l.get("annee"),
                                  l.get("kilometrage"), l.get("boite")) if prices else {}
        enriched.append({**l, "valeur_marche": offer.get("ma_cote"), **offer})

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
