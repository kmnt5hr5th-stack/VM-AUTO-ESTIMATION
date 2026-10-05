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


# ── Chasse aux bonnes affaires : travaux, immeubles de rapport, colocation ─────────────────────────────
STRATEGIES = {
    "travaux": {"mots": ["travaux", "à rénover", "a renover", "à rafraîchir", "rénovation"], "type": ""},
    "immeuble": {"mots": ["immeuble de rapport", "immeuble"], "type": ""},
    "colocation": {"mots": [], "type": ""},
}
TRAVAUX_M2 = 900          # rénovation complète moyenne (€/m²)
AMENAGEMENT_CHAMBRE = 4000  # meubles + petits travaux par chambre en colocation


REGIONS = {"idf": "12", "ile-de-france": "12", "île-de-france": "12"}


def _lieu(ville: str, code_postal: str, departement: str) -> dict:
    if departement.lower() in REGIONS:
        return {"locations": [{"locationType": "region", "region_id": REGIONS[departement.lower()]}]}
    if departement:
        return {"locations": [{"locationType": "department", "department_id": departement}]}
    l = {"locationType": "city", "city": ville}
    if code_postal:
        l["zipcode"] = code_postal
    return {"locations": [l]}


async def _references(ville: str, code_postal: str) -> dict:
    """Prix de vente médian au m², loyer médian au m² et loyer d'une chambre dans la ville."""
    vente, loc, chambre = await asyncio.gather(
        _requete({"filters": {"category": {"id": "9"}, "enums": {"ad_type": ["offer"]}, "location": _lieu(ville, code_postal, "")}, "limit": 100}),
        loyer_m2(ville, code_postal, ""),
        _requete({"filters": {"category": {"id": "10"}, "enums": {"ad_type": ["offer"]}, "ranges": {"square": {"min": 9, "max": 25}},
                              "location": _lieu(ville, code_postal, "")}, "limit": 100}),
    )
    m2 = []
    for a in (vente or {}).get("ads", []):
        x = _annonce(a)
        if x["prix_m2"] and 500 <= x["prix_m2"] <= 15000 and x["type"] in ("Appartement", "Maison"):
            m2.append(x["prix_m2"])
    loyers_ch = [(_annonce(a)["prix"] or 0) for a in (chambre or {}).get("ads", [])]
    loyers_ch = [p for p in loyers_ch if 250 <= p <= 1100]
    return {
        "prix_m2": statistics.median(m2) if len(m2) >= 5 else None,
        "loyer_m2": (loc or {}).get("loyer_m2"),
        "loyer_chambre": statistics.median(loyers_ch) if len(loyers_ch) >= 4 else None,
        "nb_chambres_louer": len(loyers_ch),
    }


def _noter(b: dict, ref: dict, strategie: str) -> dict:
    prix, surface = b["prix"] or 0, b["surface"] or 0
    chambres = int(b.get("chambres") or max(0, (b["pieces"] or 1) - 1))
    travaux = surface * TRAVAUX_M2 if strategie == "travaux" else (surface * 300 if strategie == "immeuble" else 0)
    decote = (1 - b["prix_m2"] / ref["prix_m2"]) * 100 if b.get("prix_m2") and ref.get("prix_m2") else None
    if strategie == "colocation" and ref.get("loyer_chambre") and chambres >= 2:
        loyer = ref["loyer_chambre"] * chambres
        cout = prix * 1.08 + chambres * AMENAGEMENT_CHAMBRE
    elif ref.get("loyer_m2") and surface:
        loyer = ref["loyer_m2"] * surface * (0.9 if strategie == "immeuble" else 1)
        cout = prix * 1.08 + travaux
    else:
        loyer, cout = None, prix * 1.08 + travaux
    rendement = loyer * 12 / cout * 100 if loyer and cout else None
    # Note sur 100 : rentabilité d'abord, décote sur le marché ensuite (affaire à travaux)
    note = 0
    if rendement:
        note += min(70, max(0, (rendement - 4) * 10))
    if decote:
        note += min(30, max(0, decote))
    verdict = "hyper intéressant" if note >= 70 else "intéressant" if note >= 50 else "à étudier" if note >= 35 else "moyen"
    # Prix anormalement bas (plus de 60 % sous la ville) : part de bien, cave, erreur d'annonce… → à vérifier, pas une affaire
    if decote is not None and decote > 60:
        note, verdict = min(note, 40), "à vérifier (prix anormalement bas)"
    return {**b, "chambres": chambres or None, "travaux_estimes": round(travaux) or None,
            "loyer_estime": round(loyer) if loyer else None, "rentabilite_brute": round(rendement, 1) if rendement else None,
            "decote_vs_ville": round(decote) if decote is not None else None, "note": round(note), "verdict": verdict}


async def chasser(strategie: str, ville: str = "", code_postal: str = "", departement: str = "", prix_max: int = 0,
                  chambres_min: int = 0, limite: int = 12) -> dict:
    """Cherche les biens d'une stratégie dans une ville ou un département, les compare au marché de leur ville
    et renvoie les mieux notés."""
    s = STRATEGIES.get(strategie, STRATEGIES["travaux"])
    base = {"category": {"id": "9"}, "enums": {"ad_type": ["offer"]}, "ranges": {}, "location": _lieu(ville, code_postal, departement)}
    if prix_max:
        base["ranges"]["price"] = {"max": prix_max}
    if strategie == "colocation":
        base["ranges"]["bedrooms"] = {"min": chambres_min or 4}
    requetes = [{**base, "keywords": {"text": m, "type": "all"}} for m in s["mots"]] or [base]
    reponses = await asyncio.gather(*[_requete({"filters": f, "limit": 100, "sort_by": "time", "sort_order": "desc"}) for f in requetes])
    biens, vus = [], set()
    for d in reponses:
        for a in (d or {}).get("ads", []):
            x = _annonce(a)
            brut = {k.get("key"): k.get("value") for k in a.get("attributes", [])}
            x["chambres"] = _nombre(brut.get("bedrooms"))
            if x["id"] in vus or not x["prix"] or not x["surface"] or x["prix"] < 20000:
                continue
            titre = (x["titre"] or "").lower()
            # Viager / nue-propriété : le prix affiché n'est pas le vrai coût → rentabilité faussée
            if any(m in titre for m in ("viager", "nue-propri", "nue propri", "usufruit", "parking", "garage", "terrain")):
                continue
            # Immeuble de rapport : un immeuble entier, pas un appartement « dans un immeuble »
            if strategie == "immeuble" and not ("immeuble" in titre or x["type"] == "Autre"):
                continue
            vus.add(x["id"]); biens.append(x)
    # Références de marché pour les villes les plus représentées (20 au plus, pour rester rapide)
    villes = {}
    for b in biens:
        villes.setdefault((b["ville"], b["code_postal"]), []).append(b)
    principales = sorted(villes, key=lambda k: -len(villes[k]))[:20]
    refs = dict(zip(principales, await asyncio.gather(*[_references(v or "", cp or "") for v, cp in principales])))
    notes = [_noter(b, refs[(b["ville"], b["code_postal"])], strategie) for b in biens if (b["ville"], b["code_postal"]) in refs]
    # Colocation : la ville doit s'y prêter (étudiants, écoles, transports, demande) → note finale pondérée
    if strategie == "colocation" and notes:
        from scrapers import ville as villes_mod
        cles = list(refs)
        analyses = dict(zip(cles, await asyncio.gather(*[
            villes_mod.analyser(v or "", cp or "", refs[(v, cp)].get("loyer_chambre"), refs[(v, cp)].get("nb_chambres_louer", 0))
            for v, cp in cles])))
        for b in notes:
            a = analyses.get((b["ville"], b["code_postal"])) or {}
            if "note_colocation" in a:
                b["ville_colocation"] = {"note": a["note_colocation"], "avis": a["avis"], **a["detail"]}
                b["note"] = round(0.6 * b["note"] + 0.4 * a["note_colocation"])
                b["verdict"] = "hyper intéressant" if b["note"] >= 70 else "intéressant" if b["note"] >= 50 else "à étudier" if b["note"] >= 35 else "moyen"
    notes.sort(key=lambda b: -b["note"])
    return {"strategie": strategie, "biens_analyses": len(notes), "total_trouves": len(biens),
            "references": {f"{v} {cp or ''}".strip(): r for (v, cp), r in refs.items()}, "meilleurs": notes[:limite]}
