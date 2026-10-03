"""Récupération COMPLÈTE des versions Leboncoin à partir des annonces en ligne (toutes les pages).

Pour chaque modèle de la liste officielle Leboncoin (lbc_catalog_raw.json), lit toutes les annonces 2012-2026
par paquets de 100 (API mobile, mêmes précautions que scrape_lbc_finitions.py : appareil simulé + proxy, relances).
Si un modèle dépasse la limite de pages de Leboncoin, la recherche est découpée par année, puis par carburant.

Le résultat est FUSIONNÉ dans lbc_finitions_v2.json (rien n'est jamais effacé : années élargies, versions ajoutées),
puis build_vm_ab_catalog.py reconstruit vm_ab_catalog.json.

Usage : python scrape_catalogue_complet.py            (reprend où il s'était arrêté)
        python scrape_catalogue_complet.py --nouveau  (repart de zéro)
"""
import asyncio, json, os, random, sys, time
from curl_cffi.requests import AsyncSession
from scrapers.leboncoin import _mobile_ua, _webshare_proxies, API_URL, HOMEPAGE

HERE = os.path.dirname(os.path.abspath(__file__))
MODELES_FILE = os.path.join(HERE, "lbc_catalog_raw.json")
CATALOGUE_FILE = os.path.join(HERE, "lbc_finitions_v2.json")
PROGRES_FILE = os.path.join(HERE, "catalogue_complet_progres.json")
AN_MIN, AN_MAX = 2012, 2026
PAR_PAGE = 100
PARALLELE = 4
CARBURANTS = {"1": "essence", "2": "diesel", "3": "electrique", "4": "hybride", "6": "hybride", "5": "gpl"}
sem = asyncio.Semaphore(PARALLELE)


async def requete(filtres: dict, offset: int) -> dict | None:
    payload = {"filters": {"category": {"id": "2"}, **filtres}, "limit": PAR_PAGE, "offset": offset}
    async with sem:
        for tentative in range(4):
            ua, impersonate, headers = _mobile_ua()
            try:
                async with AsyncSession(impersonate=impersonate, proxies=_webshare_proxies()) as s:
                    await s.get(HOMEPAGE, headers=headers, timeout=15)
                    r = await s.post(API_URL, json=payload, headers=headers, timeout=30)
                if r.status_code == 200:
                    await asyncio.sleep(random.uniform(0.4, 1.2))
                    return r.json()
            except Exception:
                pass
            await asyncio.sleep(random.uniform(2, 5) * (tentative + 1))
    return None


def lire(ads: list, versions: dict, finitions: dict):
    for ad in ads:
        at = {a.get("key"): (a.get("value"), a.get("value_label") or a.get("value")) for a in ad.get("attributes", [])}
        try:
            an = int(str((at.get("regdate") or ("", ""))[0])[:4])
        except ValueError:
            continue
        fuel_code, fuel_label = at.get("fuel") or ("", "")
        carburant = CARBURANTS.get(str(fuel_code), str(fuel_label or "").lower())
        for cle, cible in (("u_car_version", versions), ("u_car_finition", finitions)):
            if cle not in at or not at[cle][0]:
                continue
            valeur, label = at[cle]
            e = cible.setdefault(valeur, {"value": valeur, "label": label, "min_year": an, "max_year": an, "count": 0})
            e["min_year"], e["max_year"] = min(e["min_year"], an), max(e["max_year"], an)
            e["count"] += 1
            if cle == "u_car_version" and carburant:
                e.setdefault("fuel", carburant)


async def toutes_les_pages(filtres: dict, versions: dict, finitions: dict) -> tuple[int, bool]:
    """Lit toutes les pages d'une recherche. Renvoie (total, complet) ; complet=False si la limite de pages est atteinte."""
    premiere = await requete(filtres, 0)
    if not premiere:
        return 0, True
    total, max_pages = int(premiere.get("total") or 0), int(premiere.get("max_pages") or 100)
    lire(premiere.get("ads", []), versions, finitions)
    pages = min(-(-total // PAR_PAGE), max_pages)
    lots = await asyncio.gather(*[requete(filtres, p * PAR_PAGE) for p in range(1, pages)])
    for lot in lots:
        if lot:
            lire(lot.get("ads", []), versions, finitions)
    return total, total <= max_pages * PAR_PAGE


async def modele(marque_code: str, modele_code: str) -> dict:
    versions, finitions = {}, {}
    base = {"enums": {"ad_type": ["offer"], "u_car_brand": [marque_code], "u_car_model": [modele_code]}}
    total, complet = await toutes_les_pages({**base, "ranges": {"regdate": {"min": AN_MIN, "max": AN_MAX}}}, versions, finitions)
    if not complet:
        # Trop d'annonces pour la limite de pages : découpage par année, puis par carburant
        for an in range(AN_MIN, AN_MAX + 1):
            filtre_an = {**base, "ranges": {"regdate": {"min": an, "max": an}}}
            _, ok = await toutes_les_pages(filtre_an, versions, finitions)
            if not ok:
                for code in ("1", "2", "3", "4", "5", "6"):
                    f = {"enums": {**base["enums"], "fuel": [code]}, "ranges": filtre_an["ranges"]}
                    await toutes_les_pages(f, versions, finitions)
    return {"versions": list(versions.values()), "finitions": list(finitions.values()), "total_ads": total}


def fusionner(ancien: dict, nouveau: dict) -> dict:
    """Ajoute les versions / finitions nouvelles et élargit les années ; ne retire jamais rien."""
    for cle in ("versions", "finitions"):
        par_valeur = {e["value"]: e for e in ancien.get(cle, [])}
        for e in nouveau.get(cle, []):
            if e["value"] in par_valeur:
                a = par_valeur[e["value"]]
                a["min_year"], a["max_year"] = min(a["min_year"], e["min_year"]), max(a["max_year"], e["max_year"])
                a["count"] = max(a.get("count", 0), e["count"])
                if e.get("fuel") and not a.get("fuel"):
                    a["fuel"] = e["fuel"]
            else:
                par_valeur[e["value"]] = e
        ancien[cle] = list(par_valeur.values())
    ancien["total_ads"] = max(ancien.get("total_ads", 0), nouveau.get("total_ads", 0))
    return ancien


async def main():
    liste = json.load(open(MODELES_FILE))
    a_faire = [(marque, o["value"]) for marque, d in liste.items() for o in d.get("modelObjects", [])
               if not o["value"].endswith("_Autre")]
    progres = {} if "--nouveau" in sys.argv or not os.path.exists(PROGRES_FILE) else json.load(open(PROGRES_FILE))
    catalogue = json.load(open(CATALOGUE_FILE)) if os.path.exists(CATALOGUE_FILE) else {}
    debut, nb_av = time.time(), sum(len(v.get("versions", [])) for v in catalogue.values())
    print(f"{len(a_faire)} modèles, {len(progres)} déjà faits, catalogue actuel : {nb_av} versions", flush=True)
    for i, (marque, code) in enumerate(a_faire, 1):
        if code in progres:
            continue
        res = await modele(marque, code)
        catalogue[code] = fusionner(catalogue.get(code, {}), res)
        progres[code] = {"versions": len(res["versions"]), "annonces": res["total_ads"]}
        json.dump(progres, open(PROGRES_FILE, "w"), ensure_ascii=False)
        json.dump(catalogue, open(CATALOGUE_FILE, "w"), ensure_ascii=False)
        print(f"[{i}/{len(a_faire)}] {code} : {res['total_ads']} annonces, {len(res['versions'])} versions "
              f"({(time.time() - debut) / 60:.0f} min)", flush=True)
    nb_ap = sum(len(v.get("versions", [])) for v in catalogue.values())
    print(f"Terminé : {nb_av} → {nb_ap} versions (+{nb_ap - nb_av})", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
