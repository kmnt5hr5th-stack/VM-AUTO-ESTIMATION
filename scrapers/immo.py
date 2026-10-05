"""Immobilier Leboncoin : biens à vendre (rubrique 9) et loyers du marché (rubrique 10), pour les alertes et
les analyses de rentabilité de Valma. Mêmes précautions que les voitures (appareil simulé + proxy, relances)."""
import asyncio
import random
import statistics
from typing import Optional

from curl_cffi.requests import AsyncSession

from scrapers.leboncoin import API_URL, HOMEPAGE, _mobile_ua, _webshare_proxies

TYPES = {"maison": "1", "appartement": "2", "terrain": "3", "parking": "4", "autre": "5"}


async def _requete(payload: dict) -> Optional[dict]:
    for tentative in range(4):
        ua, impersonate, headers = _mobile_ua()
        try:
            async with AsyncSession(impersonate=impersonate, proxies=_webshare_proxies()) as s:
                await s.get(HOMEPAGE, headers=headers, timeout=15)
                r = await s.post(API_URL, json=payload, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        await asyncio.sleep(random.uniform(1.5, 3.5) * (tentative + 1))
    return None


def _filtres(categorie: str, ville: str, code_postal: str, type_bien: str, prix_max: int, surface_min: int, pieces_min: int) -> dict:
    enums = {"ad_type": ["offer"]}
    if type_bien in TYPES:
        enums["real_estate_type"] = [TYPES[type_bien]]
    ranges = {}
    if prix_max:
        ranges["price"] = {"max": prix_max}
    if surface_min:
        ranges["square"] = {"min": surface_min}
    if pieces_min:
        ranges["rooms"] = {"min": pieces_min}
    lieu = {"locationType": "city", "city": ville}
    if code_postal:
        lieu["zipcode"] = code_postal
    return {"category": {"id": categorie}, "enums": enums, "ranges": ranges, "location": {"locations": [lieu]}}


def _nombre(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ".").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _annonce(a: dict) -> dict:
    at = {x.get("key"): x.get("value_label") or x.get("value") for x in a.get("attributes", [])}
    brut = {x.get("key"): x.get("value") for x in a.get("attributes", [])}
    prix = (a.get("price") or [None])[0]
    surface = _nombre(brut.get("square"))
    return {
        "id": str(a.get("list_id") or a.get("id") or ""),
        "titre": a.get("subject", ""),
        "prix": prix,
        "surface": surface,
        "pieces": _nombre(brut.get("rooms")),
        "prix_m2": round(prix / surface) if prix and surface else None,
        "type": at.get("real_estate_type"),
        "ville": (a.get("location") or {}).get("city"),
        "code_postal": (a.get("location") or {}).get("zipcode"),
        "dpe": at.get("energy_rate") or at.get("energy"),
        "etage": at.get("floor_number"),
        "annee_construction": at.get("building_year"),
        "etat": at.get("global_condition"),
        "pro": (a.get("owner") or {}).get("type") == "pro",
        "publiee": a.get("first_publication_date"),
        "url": a.get("url"),
        "photo": ((a.get("images") or {}).get("urls_large") or [None])[0],
    }


async def rechercher(ville: str, code_postal: str = "", type_bien: str = "appartement", prix_max: int = 0,
                     surface_min: int = 0, pieces_min: int = 0, limite: int = 35) -> dict:
    """Biens à vendre, les plus récents d'abord."""
    payload = {"filters": _filtres("9", ville, code_postal, type_bien, prix_max, surface_min, pieces_min),
               "limit": max(1, min(limite, 100)), "sort_by": "time", "sort_order": "desc"}
    d = await _requete(payload)
    if d is None:
        return {"erreur": "Leboncoin ne répond pas"}
    return {"total": d.get("total", 0), "annonces": [_annonce(a) for a in d.get("ads", [])]}


async def loyer_m2(ville: str, code_postal: str = "", type_bien: str = "appartement", surface: int = 0) -> dict:
    """Loyer médian au m² (charges comprises, tel qu'affiché) des locations en ligne de la ville."""
    filtres = _filtres("10", ville, code_postal, type_bien, 0, 0, 0)
    if surface:
        filtres["ranges"]["square"] = {"min": int(surface * 0.6), "max": int(surface * 1.5)}
    d = await _requete({"filters": filtres, "limit": 100, "sort_by": "time", "sort_order": "desc"})
    if d is None:
        return {"erreur": "Leboncoin ne répond pas"}
    valeurs = []
    for a in d.get("ads", []):
        x = _annonce(a)
        if x["prix"] and x["surface"] and 8 <= x["surface"] <= 400:
            m2 = x["prix"] / x["surface"]
            if 4 <= m2 <= 60:   # écarte les erreurs (prix de vente, parkings…)
                valeurs.append(m2)
    if len(valeurs) < 3:
        return {"loyer_m2": None, "nb_annonces": len(valeurs)}
    return {"loyer_m2": round(statistics.median(valeurs), 1), "nb_annonces": len(valeurs),
            "fourchette": [round(sorted(valeurs)[len(valeurs) // 4], 1), round(sorted(valeurs)[3 * len(valeurs) // 4], 1)]}
