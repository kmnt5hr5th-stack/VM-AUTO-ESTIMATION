"""Catalogue VM Auto Business : versions exactes (motorisation + finition) par marque / modèle / année / carburant.

Données : vm_ab_catalog.json (généré par build_vm_ab_catalog.py).
Le formulaire du site envoie des noms simples (« Golf », « GLA », « Evoque ») : on les rapproche
des noms du catalogue (« Golf », « Classe GLA », « Range Rover Evoque »).
"""

import json
import os
import re
import unicodedata

_PATH = os.path.join(os.path.dirname(__file__), "vm_ab_catalog.json")
try:
    with open(_PATH, encoding="utf-8") as _f:
        _CATALOG: dict = json.load(_f)
except FileNotFoundError:
    _CATALOG = {}

_FUELS = {
    "essence": "essence", "diesel": "diesel", "hybride": "hybride", "hybride rechargeable": "hybride",
    "électrique": "electrique", "electrique": "electrique", "gpl": "gpl", "gnv": "gpl",
}
_BRAND_ALIASES = {"mercedesbenz": "mercedes", "mgmotor": "mg"}
# Variantes de carrosserie que le catalogue range sous le modèle de base (« Golf SW » → « Golf »)
_VARIANT_SUFFIXES = ["sw", "break", "estate", "touring", "sportback", "variant", "etech", "etron", "crossback", "combi", "tourer", "cabriolet", "coupe"]


def _key(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def _find_brand(marque: str):
    k = _BRAND_ALIASES.get(_key(marque), _key(marque))
    for name, models in _CATALOG.items():
        if _BRAND_ALIASES.get(_key(name), _key(name)) == k:
            return models
    return None


def _find_models(models: dict, k: str) -> list:
    if not k:
        return []
    keys = {name: _key(name) for name in models}
    # 1. Nom identique, ou « Classe X » chez Mercedes
    exact = [n for n, kk in keys.items() if kk in (k, "classe" + k)]
    if exact:
        return exact
    # 2. Variante de carrosserie → modèle de base
    for suffix in _VARIANT_SUFFIXES:
        if k.endswith(suffix) and len(k) > len(suffix):
            base = _find_models(models, k[: -len(suffix)])
            if base:
                return base
    # 3. Version électrique notée à part (« ë-C4 » → « C4 », « 500e » → « 500 »)
    for stripped in (k[1:] if k.startswith("e") else "", k[:-1] if k.endswith("e") else ""):
        if len(stripped) >= 2 and stripped in keys.values():
            return [n for n, kk in keys.items() if kk == stripped]
    # 4. Nom du site contenu dans celui du catalogue (« Evoque » → « Range Rover Evoque »)
    if len(k) >= 4:
        return [n for n, kk in keys.items() if kk.endswith(k) or kk.startswith(k)]
    return []


# Même voiture sous deux noms dans le catalogue Leboncoin : on lit les deux
_MEME_MODELE = [{"crossland", "crosslandx"}, {"grandland", "grandlandx"}, {"scross", "sx4scross"},
                {"evoque", "rangeroverevoque"}, {"velar", "rangerovervelar"}, {"glc", "classeglc"}]


def _avec_jumeaux(models: dict, found: list) -> list:
    keys = {_key(f) for f in found}
    for groupe in _MEME_MODELE:
        if keys & groupe:
            found = found + [n for n in models if _key(n) in groupe and n not in found]
    return found


def _models_with_filter(models: dict, modele: str) -> list:
    """Modèles à lire + mots que doit contenir la version (« Série 2 Active Tourer » → « Série 2 » + « active tourer »)."""
    found = _find_models(models, _key(modele))
    if found:
        return [(n, "") for n in _avec_jumeaux(models, found)]
    words = modele.split()
    for cut in range(len(words) - 1, 0, -1):
        base = _find_models(models, _key(" ".join(words[:cut])))
        if base:
            return [(n, _key(" ".join(words[cut:]))) for n in base]
    return []


def has_brand(marque: str) -> bool:
    return _find_brand(marque) is not None


# Hybrides légers (48V) : la voiture roule à l'essence ou au gazole, et le vendeur la déclare souvent ainsi
_MILD = re.compile(r"48\s?v|mhev|mild|eq\s?boost|shvs|smart\s?hybrid|\betsi\b|e-tsi|\bmht\b", re.I)
_DIESEL = re.compile(r"\b(tdi|hdi|bluehdi|dci|crdi|cdi|tdci|multijet|jtd|jtdm|ecoblue|ddis|i-dtec|dtec|d4|d5|sd4|td4|ed4|bluetec)\b|\d{2,3}d\b|\bd\s?\d{3}\b|\bdiesel\b", re.I)


# Même voiture vendue sous un autre nom (successeur direct, variante)
_MEME_VOITURE = {
    "c4 picasso": ["c4 spacetourer"], "grand c4 picasso": ["grand c4 spacetourer"], "expert": ["traveller"],
    "grande punto": ["punto evo", "punto"], "c-max": ["grand c-max"], "sx4 s-cross": ["s-cross"], "ix1": ["x1"],
}


def _intrus(models: dict, name: str, label: str):
    """Version mal rangée par le vendeur Leboncoin : renvoie le BON modèle (« Classe C 300 e » sous GLC → « Classe C »), sinon None."""
    lab = label.lower()
    propres = {name.lower(), name.lower().removeprefix("classe ")} | set(_MEME_VOITURE.get(name.lower(), []))
    # Un modèle plus précis de la même marque (« Tiguan Allspace » rangée sous « Tiguan ») l'emporte
    plus_long = max((len(p) for p in propres if lab.startswith(p + " ")), default=0)
    if plus_long:
        for autre in models:
            if autre != name and len(autre) > plus_long and lab.startswith(autre.lower() + " ") \
                    and _key(autre) not in {_key(p) for p in propres}:
                return autre
        return None
    # Le nom du modèle figure dans les premiers mots (« Transit Custom » sous Custom, « NP300 Navara » sous Navara)
    debut = re.sub(r"[^a-z0-9 ]", "", lab).split()[:4]
    if any(len(p) >= 3 and any(m.startswith(re.sub(r"[^a-z0-9]", "", p)) for m in debut) for p in propres):
        return None
    bon, long_max = None, 0
    for autre in models:
        if autre == name:
            continue
        for nom in {autre.lower(), autre.lower().removeprefix("classe ")}:
            if len(nom) >= 2 and lab.startswith(nom + " ") and not any(p.startswith(nom) for p in propres) and len(nom) > long_max:
                bon, long_max = autre, len(nom)
    return bon


def _reranger() -> int:
    """Déplace (en mémoire, le fichier n'est pas modifié) chaque version mal rangée vers son bon modèle."""
    deplacees = 0
    for models in _CATALOG.values():
        a_deplacer = []
        for name, vs in models.items():
            for v in vs:
                bon = _intrus(models, name, v["v"])
                if bon:
                    a_deplacer.append((name, bon, v))
        for name, bon, v in a_deplacer:
            models[name] = [x for x in models[name] if x is not v]
            existant = next((x for x in models[bon] if x["v"] == v["v"] and x["c"] == v["c"]), None)
            if existant:
                existant["de"], existant["a"] = min(existant["de"], v["de"]), max(existant["a"], v["a"])
                existant["n"] += v["n"]
            else:
                models[bon].append(v)
            deplacees += 1
    return deplacees


def _fuel_ok(v: dict, fuel: str) -> bool:
    if not fuel or v["c"] == fuel:
        return True
    if v["c"] == "hybride" and fuel in ("essence", "diesel") and _MILD.search(v["v"]):
        return ("diesel" if _DIESEL.search(v["v"]) else "essence") == fuel
    return False


def get_versions(marque: str, modele: str, annee: int, carburant: str, carrosserie: str = "") -> list:
    """Versions du catalogue pour ce véhicule, les plus courantes en premier."""
    models = _find_brand(marque)
    if not models or not annee:
        return []
    fuel = _FUELS.get(carburant.lower().strip(), "")
    found: dict = {}
    for name, must in _models_with_filter(models, modele):
        for v in models[name]:
            if not _fuel_ok(v, fuel):
                continue
            if carrosserie and not est_utilitaire(modele) and carrosserie_version(v["v"], name).lower() != carrosserie.lower().strip():
                continue
            if must and must not in _key(v["v"]):
                continue
            # Une voiture immatriculée en début d'année peut être du millésime précédent : 1 an de tolérance
            if v["de"] - 1 <= annee <= v["a"] + 1:
                found[v["v"]] = max(found.get(v["v"], 0), v["n"])
    return sorted(found, key=lambda label: -found[label])


def fiche_modele(marque: str, modele: str) -> list:
    """Toutes les versions d'un modèle avec carburant, années et nombre d'annonces vues (page Catalogue de l'app)."""
    models = _find_brand(marque)
    if not models:
        return []
    found: dict = {}
    for name, must in _models_with_filter(models, modele):
        for v in models[name]:
            if must and must not in _key(v["v"]):
                continue
            e = found.setdefault(v["v"], {"version": v["v"], "carburant": v["c"], "de": v["de"], "a": v["a"], "annonces": 0,
                                          "carrosserie": "" if est_utilitaire(modele) else carrosserie_version(v["v"], name)})
            e["de"], e["a"] = min(e["de"], v["de"]), max(e["a"], v["a"])
            e["annonces"] += v["n"]
    return sorted(found.values(), key=lambda e: (-e["a"], e["version"]))


def carburants(marque: str, modele: str, annee: int = 0) -> list:
    """Carburants existants pour ce modèle (et cette année si donnée) ; un hybride léger compte aussi en essence / diesel."""
    models = _find_brand(marque)
    if not models:
        return []
    vus = set()
    for name, must in _models_with_filter(models, modele):
        for v in models[name]:
            if must and must not in _key(v["v"]):
                continue
            if annee and not (v["de"] - 1 <= annee <= v["a"] + 1):
                continue
            vus.add(v["c"])
            if v["c"] == "hybride" and _MILD.search(v["v"]):
                vus.add("diesel" if _DIESEL.search(v["v"]) else "essence")
    ordre = ["essence", "diesel", "hybride", "electrique", "gpl"]
    return [c for c in ordre if c in vus]


def stats() -> dict:
    return {"marques": len(_CATALOG), "modeles": sum(len(m) for m in _CATALOG.values()),
            "versions": sum(len(vs) for m in _CATALOG.values() for vs in m.values())}


def list_marques() -> list:
    return sorted(_CATALOG)


def list_modeles(marque: str) -> list:
    models = _find_brand(marque)
    return sorted(models) if models else []


# ── Carrosseries (mêmes types que le filtre Leboncoin du moteur) ──────────────────────────────
_SUV = {
    "2008", "3008", "5008", "4008", "408", "captur", "kadjar", "koleos", "arkana", "austral", "rafale", "symbioz", "scenic e-tech",
    "c3 aircross", "c5 aircross", "c4 aircross", "c5 x", "berlingo", "t-roc", "t-cross", "tiguan", "tiguan allspace", "touareg", "taigo",
    "id.4", "id.5", "id.6", "atlas", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "e-tron", "sq5", "sq7", "sq8", "rs q3", "rs q8",
    "kuga", "puma", "ecosport", "edge", "explorer", "mustang mach-e", "bronco", "mokka", "mokka x", "crossland", "crossland x",
    "grandland", "grandland x", "frontera", "antara", "tucson", "kona", "santa fe", "bayon", "ix35", "ix55", "nexo", "ioniq 5",
    "ioniq 9", "inster", "sportage", "stonic", "niro", "sorento", "ev3", "ev5", "ev6", "ev9", "soul", "xceed", "qashqai", "qashqai+2",
    "juke", "ariya", "x-trail", "murano", "pathfinder", "navara", "rav4", "c-hr", "c-hr+", "yaris cross", "bz4x", "land cruiser",
    "highlander", "hilux", "aygo x", "corolla cross", "urban cruiser", "cx-3", "cx-30", "cx-5", "cx-60", "cx-80", "mx-30", "cx-7",
    "xc40", "xc60", "xc70", "xc90", "c40", "ex30", "ex40", "ex90", "ec40", "asx", "outlander", "eclipse cross", "pajero", "l200",
    "vitara", "grand vitara", "s-cross", "sx4 s-cross", "jimny", "ignis", "across", "ds 3 crossback", "ds 7 crossback", "ds 7",
    "ds 3", "stelvio", "tonale", "junior", "ateca", "arona", "tarraco", "formentor", "terramar", "born", "karoq", "kodiaq",
    "kamiq", "yeti", "enyaq", "elroq", "macan", "cayenne", "renegade", "compass", "cherokee", "grand cherokee", "wrangler",
    "avenger", "defender", "discovery", "discovery sport", "range rover", "range rover sport", "range rover evoque",
    "range rover velar", "evoque", "velar", "duster", "bigster", "spring", "jogger", "forester", "outback", "xv", "crosstrek",
    "solterra", "nx", "rx", "ux", "lbx", "rz", "f-pace", "e-pace", "i-pace", "zs", "hs", "ehs", "marvel r", "cr-v", "hr-v",
    "zr-v", "e:ny1", "countryman", "paceman", "model x", "model y", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "xm", "ix", "ix1",
    "ix2", "ix3", "classe gla", "classe glb", "classe glc", "classe gle", "classe gls", "classe glk", "classe gl", "classe ml",
    "classe m/ml", "classe g", "eqa", "eqb", "eqc", "eqe suv", "eqs suv", "classe eqa", "classe eqb", "classe eqc", "urus",
    "bentayga", "cullinan", "dbx", "purosangue", "levante", "grecale", "atto 3", "seal u", "tang", "kodiaq rs", "xpeng g6",
}
_MONOSPACE = {"grand scenic", "scenic", "c4 picasso", "grand c4 picasso", "c4 spacetourer", "grand c4 spacetourer", "touran",
              "sharan", "zafira", "zafira tourer", "meriva", "lodgy", "dokker", "c-max", "grand c-max", "s-max", "galaxy",
              "classe b", "classe v", "alhambra", "5008 i", "espace", "picasso", "carens", "verso", "corolla verso", "prius+",
              "multipla", "500l", "kangoo", "partner", "rifter", "traveller", "spacetourer", "caddy", "touran", "combo life"}
_UTILITAIRES = {"boxer", "master", "trafic", "jumper", "jumpy", "vito", "sprinter", "ducato", "scudo", "talento", "vivaro",
                "movano", "nv200", "nv300", "nv400", "primastar", "interstar", "townstar", "citan", "combi", "transit",
                "transit custom", "custom", "crafter", "transporter", "daily", "proace", "proace city", "expert", "doblo",
                "fiorino", "nemo", "bipper", "berlingo van", "partner van", "kangoo express", "master iii", "e-transit"}
_MOTS_CARROSSERIE = [
    ("Monospace", re.compile(r"active tourer|gran tourer", re.I)),
    ("Cabriolet", re.compile(r"\b(cabriolet|cabrio|roadster|spider|spyder|convertible|décapotable)\b", re.I)),
    ("Coupé", re.compile(r"\b(coup[ée]|gran coup[ée]|fastback)\b", re.I)),
    ("Break", re.compile(r"\b(sw|break|estate|touring|avant|sports? tourer|sportwagon|variant|combi|shooting brake|tourer|allroad|cross country)\b", re.I)),
    ("Sportback", re.compile(r"\bsportback\b", re.I)),
]


def _type_de_base(modele: str) -> str:
    m = modele.lower().strip()
    if m in _SUV:
        return "SUV / 4x4"
    if m in _MONOSPACE:
        return "Monospace"
    return "Berline"


def carrosserie_version(label: str, modele: str) -> str:
    """Carrosserie d'une version d'après son nom (types Leboncoin : Berline, Break, Coupé, Cabriolet, SUV / 4x4, Monospace)."""
    base = _type_de_base(modele)
    for nom, rx in _MOTS_CARROSSERIE:
        if rx.search(label):
            if nom == "Sportback":
                # Q3 Sportback = SUV coupé ; A3 / A5 Sportback = berline (comme sur Leboncoin)
                return "Coupé" if base == "SUV / 4x4" else "Berline"
            if nom == "Break" and base == "SUV / 4x4":
                return base
            return nom
    return base


def nom_catalogue(marque: str, modele: str) -> str:
    """Nom du modèle dans le catalogue (« GLC » → « Classe GLC »), sinon le nom donné."""
    models = _find_brand(marque)
    found = _find_models(models, _key(modele)) if models else []
    return found[0] if found else modele


def est_utilitaire(modele: str) -> bool:
    return modele.lower().strip() in _UTILITAIRES


def carrosseries(marque: str, modele: str, annee: int = 0) -> list:
    """Carrosseries qui existent pour ce modèle (et cette année si donnée), la plus courante d'abord. Aucune pour un utilitaire."""
    if est_utilitaire(modele):
        return []
    models = _find_brand(marque)
    if not models:
        return []
    compte: dict = {}
    for name, must in _models_with_filter(models, modele):
        for v in models[name]:
            if must and must not in _key(v["v"]):
                continue
            if annee and not (v["de"] - 1 <= annee <= v["a"] + 1):
                continue
            t = carrosserie_version(v["v"], name)
            compte[t] = compte.get(t, 0) + max(v["n"], 1)
    total = sum(compte.values()) or 1
    # Une carrosserie vue sur moins de 2 % des annonces est une erreur de vendeur, sauf si c'est la seule
    garde = [t for t, n in compte.items() if n / total >= 0.02 or len(compte) == 1]
    return sorted(garde, key=lambda t: -compte[t])


REVERSIONS_DEPLACEES = _reranger()
