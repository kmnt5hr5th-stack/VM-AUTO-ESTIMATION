"""La ville est-elle bonne pour la colocation ? Données publiques gratuites : population (geo.api.gouv.fr),
étudiants inscrits dans la commune (ministère de l'Enseignement supérieur), gares RER / métro / train / tram
(Île-de-France Mobilités) et distance de Paris. Note sur 100, mise en cache pour la journée."""
import math
import time
from typing import Optional

import httpx

ESR = "https://data.enseignementsup-recherche.gouv.fr/api/explore/v2.1/catalog/datasets/fr-esr-atlas_regional-effectifs-d-etudiants-inscrits/records"
GARES = "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/emplacement-des-gares-idf-data-generalisee/records"
PARIS = (48.8566, 2.3522)
_cache: dict = {}


def _km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


async def analyser(ville: str, code_postal: str = "", loyer_chambre: Optional[float] = None, nb_chambres_louer: int = 0) -> dict:
    cle = f"{ville}|{code_postal}".lower()
    if cle in _cache and time.time() - _cache[cle][0] < 86400:
        base = _cache[cle][1]
    else:
        base = await _donnees(ville, code_postal)
        _cache[cle] = (time.time(), base)
    if "erreur" in base:
        return base
    return _noter(base, loyer_chambre, nb_chambres_louer)


async def _donnees(ville: str, code_postal: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        q = {"nom": ville, "fields": "code,nom,population,centre", "limit": 1, "boost": "population"}
        if code_postal:
            q["codePostal"] = code_postal
        r = await c.get("https://geo.api.gouv.fr/communes", params=q)
        communes = r.json() if r.status_code == 200 else []
        if not communes:
            return {"erreur": f"Commune « {ville} » introuvable"}
        com = communes[0]
        lon, lat = com["centre"]["coordinates"]
        code = com["code"]
        # Étudiants inscrits dans la commune (dernière rentrée disponible)
        etudiants = 0
        try:
            e = await c.get(ESR, params={"select": "rentree,sum(effectif) as n", "where": f'geo_id="{code}" and regroupement="TOTAL"',
                                         "group_by": "rentree", "order_by": "rentree desc", "limit": 1})
            res = e.json().get("results", [])
            etudiants = int(res[0]["n"]) if res else 0
        except Exception:
            pass
        # Gares à moins de 3 km du centre
        gares = []
        try:
            g = await c.get(GARES, params={"select": "nom_long,metro,rer,train,tramway,geo_point_2d",
                                           "where": f"within_distance(geo_point_2d, geom'POINT({lon} {lat})', 3km)", "limit": 50})
            gares = g.json().get("results", [])
        except Exception:
            pass
    modes = {m: any(str(x.get(m)) == "1" for x in gares) for m in ("metro", "rer", "train", "tramway")}
    return {"commune": com["nom"], "code_insee": code, "population": com.get("population") or 0, "etudiants": etudiants,
            "gares": sorted({x["nom_long"] for x in gares})[:8], "modes": modes, "km_paris": round(_km((lat, lon), PARIS), 1)}


def _noter(b: dict, loyer_chambre: Optional[float], nb_chambres_louer: int) -> dict:
    # Jeunes / écoles (35 pts) : nombre d'étudiants inscrits dans la commune
    pts_etu = min(35, 12 * math.log10(1 + b["etudiants"] / 200))
    # Transports (45 pts) : métro / RER / train / tram à moins de 3 km + distance de Paris
    m = b["modes"]
    pts_tr = min(30, (25 if m["metro"] else 0) + (20 if m["rer"] else 0) + (12 if m["train"] else 0) + (8 if m["tramway"] else 0))
    pts_tr += 15 if b["km_paris"] <= 10 else 10 if b["km_paris"] <= 20 else 5 if b["km_paris"] <= 35 else 0
    # Demande locative (20 pts) : loyer d'une chambre et nombre de chambres / studios à louer
    pts_dem = 0
    if loyer_chambre:
        pts_dem += 10 if loyer_chambre >= 550 else 6 if loyer_chambre >= 450 else 2
    pts_dem += 10 if nb_chambres_louer >= 15 else 5 if nb_chambres_louer >= 5 else 0
    note = round(pts_etu + pts_tr + pts_dem)
    lignes = [k.upper() if k != "tramway" else "tram" for k, v in m.items() if v]
    avis = ("très bonne ville pour la colocation" if note >= 65 else "ville correcte pour la colocation" if note >= 45
            else "colocation risquée (peu d'étudiants ou mal desservie)")
    return {**b, "note_colocation": note, "avis": avis,
            "detail": {"etudiants": f"{b['etudiants']:,} étudiants inscrits".replace(",", " "),
                       "transports": (", ".join(lignes) or "aucune gare à moins de 3 km") + f" · {b['km_paris']} km de Paris",
                       "demande": (f"chambre ≈ {round(loyer_chambre)} €/mois" if loyer_chambre else "loyer des chambres inconnu")
                       + f" · {nb_chambres_louer} chambres/studios à louer"}}
