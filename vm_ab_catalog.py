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


def _models_with_filter(models: dict, modele: str) -> list:
    """Modèles à lire + mots que doit contenir la version (« Série 2 Active Tourer » → « Série 2 » + « active tourer »)."""
    found = _find_models(models, _key(modele))
    if found:
        return [(n, "") for n in found]
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


def _fuel_ok(v: dict, fuel: str) -> bool:
    if not fuel or v["c"] == fuel:
        return True
    if v["c"] == "hybride" and fuel in ("essence", "diesel") and _MILD.search(v["v"]):
        return ("diesel" if _DIESEL.search(v["v"]) else "essence") == fuel
    return False


def get_versions(marque: str, modele: str, annee: int, carburant: str) -> list:
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
            if must and must not in _key(v["v"]):
                continue
            # Une voiture immatriculée en début d'année peut être du millésime précédent : 1 an de tolérance
            if v["de"] - 1 <= annee <= v["a"] + 1:
                found[v["v"]] = max(found.get(v["v"], 0), v["n"])
    return sorted(found, key=lambda label: -found[label])


def list_marques() -> list:
    return sorted(_CATALOG)


def list_modeles(marque: str) -> list:
    models = _find_brand(marque)
    return sorted(models) if models else []
