"""Ventes immobilières réelles (DVF, base officielle et gratuite de l'État, la même que Pappers Immo) :
prix réellement payés autour d'un bien, ou dans un immeuble précis à partir d'une adresse.
Fichiers par commune : files.data.gouv.fr/geo-dvf (mis à jour deux fois par an)."""
import asyncio
import csv
import io
import math
import statistics
import time
import unicodedata
from datetime import date
from typing import Optional

import httpx

DVF = "https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/communes/{dep}/{insee}.csv"
GEO = "https://geo.api.gouv.fr/communes"
ADRESSE = "https://api-adresse.data.gouv.fr/search/"

_cache: dict = {}            # insee → (horodatage, ventes)
_insees: dict = {}           # (ville, code postal) → insee
_CACHE_DUREE = 7 * 86400
_CACHE_MAX = 60           # mémoire limitée sur Render


def _annees() -> list:
    # 3 dernières années publiées (l'année en cours n'est jamais complète)
    a = date.today().year
    return [a - 1, a - 2, a - 3]


def _departement(insee: str) -> str:
    return insee[:3] if insee.startswith("97") else insee[:2]


async def _insee(client: httpx.AsyncClient, ville: str, code_postal: str) -> Optional[str]:
    cle = (ville.lower(), code_postal)
    if cle not in _insees:
        _insees[cle] = await _chercher_insee(client, ville, code_postal)
    return _insees[cle]


async def _chercher_insee(client: httpx.AsyncClient, ville: str, code_postal: str) -> Optional[str]:
    params = {"fields": "code,nom", "format": "json"}
    if code_postal:
        params["codePostal"] = code_postal
    if ville:
        params["nom"] = ville
    try:
        r = await client.get(GEO, params=params, timeout=10)
        communes = r.json() if r.status_code == 200 else []
    except Exception:
        return None
    if not communes and code_postal and ville:      # nom écrit autrement : le code postal seul suffit souvent
        return await _chercher_insee(client, "", code_postal)
    return communes[0]["code"] if communes else None


def _lire(texte: str) -> list:
    """Une vente = une mutation avec un seul logement (appartement ou maison), sinon le prix est celui d'un lot."""
    par_mutation: dict = {}
    for r in csv.DictReader(io.StringIO(texte)):
        if r.get("nature_mutation") == "Vente":
            par_mutation.setdefault(r["id_mutation"], []).append(r)
    ventes = []
    for lignes in par_mutation.values():
        logements = [r for r in lignes if r.get("type_local") in ("Appartement", "Maison")]
        if len(logements) != 1:
            continue
        r = logements[0]
        try:
            prix, surface = float(r["valeur_fonciere"]), float(r["surface_reelle_bati"])
        except (TypeError, ValueError):
            continue
        if surface < 12 or not (500 <= prix / surface <= 20000):
            continue
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError):
            lat = lon = None
        ventes.append({
            "date": r["date_mutation"], "type": r["type_local"], "prix": round(prix), "surface": round(surface),
            "pieces": int(float(r["nombre_pieces_principales"] or 0)) or None, "prix_m2": round(prix / surface),
            "numero": (r.get("adresse_numero") or "") + (r.get("adresse_suffixe") or ""),
            "voie": r.get("adresse_nom_voie") or "", "code_postal": r.get("code_postal") or "",
            "ville": r.get("nom_commune") or "", "lat": lat, "lon": lon,
        })
    return ventes


async def ventes_commune(insee: str) -> list:
    c = _cache.get(insee)
    if c and time.time() - c[0] < _CACHE_DUREE:
        return c[1]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        reps = await asyncio.gather(*[client.get(DVF.format(annee=a, dep=_departement(insee), insee=insee), timeout=30)
                                      for a in _annees()], return_exceptions=True)
    ventes = []
    for r in reps:
        if isinstance(r, httpx.Response) and r.status_code == 200:
            ventes += _lire(r.text)
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    _cache[insee] = (time.time(), ventes)
    return ventes


def _distance(lat1, lon1, lat2, lon2) -> float:
    """Distance en mètres (assez précis à l'échelle d'une ville)."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return math.hypot(x, y) * 6371000


def _resume(ventes: list) -> dict:
    return {"prix_m2": round(statistics.median(v["prix_m2"] for v in ventes)), "nb": len(ventes)} if ventes else {}


def _norm(voie: str) -> str:
    s = unicodedata.normalize("NFD", voie.upper()).encode("ascii", "ignore").decode()
    for long, court in (("AVENUE", "AV"), ("BOULEVARD", "BD"), ("PLACE", "PL"), ("ALLEE", "ALL"), ("CHEMIN", "CHE"),
                        ("IMPASSE", "IMP"), ("ROUTE", "RTE"), ("SQUARE", "SQ")):
        if s.startswith(long + " "):
            s = court + s[len(long):]
    return " ".join(s.replace("-", " ").replace("'", " ").split())


async def autour(ville: str, code_postal: str, type_bien: str = "", lat: float = None, lon: float = None) -> dict:
    """Prix réel au m² autour d'un bien : 300 m, sinon 600 m, sinon toute la commune (5 ventes au moins).
    type_bien « Appartement » ou « Maison » (sinon les deux)."""
    async with httpx.AsyncClient() as client:
        insee = await _insee(client, ville, code_postal)
    if not insee:
        return {}
    ventes = await ventes_commune(insee)
    if type_bien in ("Appartement", "Maison"):
        ventes = [v for v in ventes if v["type"] == type_bien] or ventes
    if lat and lon:
        for rayon in (300, 600):
            proches = [v for v in ventes if v["lat"] and _distance(lat, lon, v["lat"], v["lon"]) <= rayon]
            if len(proches) >= 5:
                return {**_resume(proches), "zone": f"{rayon} m"}
    return {**_resume(ventes), "zone": "commune"} if len(ventes) >= 5 else {}


async def par_adresse(adresse: str) -> dict:
    """Ventes de l'immeuble exact (même numéro et même rue) et des environs (300 m) sur les 3 dernières années."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(ADRESSE, params={"q": adresse, "limit": 1}, timeout=10)
            f = (r.json().get("features") or [None])[0]
        except Exception:
            f = None
    if not f:
        return {"erreur": "Adresse introuvable"}
    p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
    ventes = await ventes_commune(p.get("citycode", ""))
    numero, rue = (p.get("housenumber") or "").upper(), _norm(p.get("street") or p.get("name") or "")
    immeuble = [v for v in ventes if numero and v["numero"].upper() == numero and _norm(v["voie"]) == rue]
    proches = [dict(v, distance=round(_distance(lat, lon, v["lat"], v["lon"]))) for v in ventes
               if v["lat"] and v not in immeuble and _distance(lat, lon, v["lat"], v["lon"]) <= 300]
    proches.sort(key=lambda v: v["distance"])
    immeuble.sort(key=lambda v: v["date"], reverse=True)
    return {
        "adresse": p.get("label"), "numero_exact": p.get("type") == "housenumber",
        "immeuble": immeuble, "immeuble_resume": _resume(immeuble),
        "environs": proches[:40], "environs_resume": _resume(proches),
        "periode": f"{min(_annees())}-{max(_annees())}",
    }
