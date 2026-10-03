import json
import statistics
import datetime
from typing import Optional

# ── Normalisation des marques ──────────────────────────────────────────────────

_MARQUE_ALIASES: dict[str, str] = {
    "MERCEDES-BENZ": "MERCEDES",
    "MERCEDES BENZ": "MERCEDES",
    "VW":            "VOLKSWAGEN",
    "LAND-ROVER":    "LAND ROVER",
    "CITROËN":       "CITROEN",
    "ALFA-ROMEO":    "ALFA ROMEO",
}

def _normalize_marque(marque: str) -> str:
    up = marque.strip().upper()
    return _MARQUE_ALIASES.get(up, up)


# ── Land Rover / Range Rover → coefficient fixe 65% ──────────────────────────

def _is_landrover(marque: str) -> bool:
    m = marque.upper().strip().replace("-", " ")
    return m in ("LAND ROVER", "RANGE ROVER", "LANDROVER", "RANGEROVER")


# ── Moteurs à risque → coefficient fixe ──────────────────────────────────────

# Liste par défaut des moteurs à problème. Modifiable dans Réglages de l'app (clé estimation.moteurs_fragiles, JSON).
# Une entrée s'applique si : la marque est dans « marques » (vide = toutes), la version contient TOUS les mots de
# « tous » et AU MOINS UN de « un_de » (vide = aucun requis), aucun mot de « exclure », et l'année est dans les bornes.
MOTEURS_FRAGILES_DEFAUT = [
    {"nom": "1.2 PureTech", "raison": "courroie de distribution humide", "marques": ["PEUGEOT", "CITROEN", "DS", "OPEL"],
     "un_de": ["puretech", "1.2"], "exclure": ["hdi"]},
    {"nom": "1.2 TCe / DIG-T", "raison": "consommation d'huile, chaîne", "marques": ["RENAULT", "DACIA", "NISSAN"],
     "tous": ["1.2"], "un_de": ["tce", "dig-t", "dig t"]},
    {"nom": "EcoBoost 1.0 / 1.5 / 1.6", "raison": "joint de culasse, surchauffe", "marques": ["FORD"],
     "un_de": ["1.0 ecoboost", "1.5 ecoboost", "1.6 ecoboost", "ecoboost 1.0", "ecoboost 1.5", "ecoboost 1.6"]},
    {"nom": "1.6 THP", "raison": "chaîne de distribution, consommation d'huile", "marques": ["PEUGEOT", "CITROEN", "DS"],
     "un_de": ["thp"], "exclure": ["e-thp"]},
    {"nom": "1.2 / 1.4 TSI (avant 2013)", "raison": "chaîne de distribution", "marques": ["VOLKSWAGEN", "AUDI", "SEAT", "SKODA"],
     "un_de": ["1.2 tsi", "1.4 tsi", "1.2 tfsi", "1.4 tfsi"], "annee_max": 2012},
    {"nom": "BMW diesel N47 (avant 2015)", "raison": "chaîne de distribution", "marques": ["BMW"],
     "un_de": ["16d", "18d", "20d", "25d"], "annee_max": 2014},
    {"nom": "Mazda 2.2 Skyactiv-D (avant 2019)", "raison": "encrassement, dilution d'huile", "marques": ["MAZDA"],
     "tous": ["2.2"], "un_de": ["skyactiv-d", "skyactiv d"], "annee_max": 2018},
    {"nom": "Jaguar 2.0 diesel Ingenium (avant 2020)", "raison": "chaîne, injection", "marques": ["JAGUAR"],
     "un_de": ["2.0d", "2.0 d"], "annee_max": 2019},
]


def _liste_moteurs(params: Optional[dict]) -> list:
    brut = (params or {}).get("moteurs_fragiles")
    if brut:
        try:
            liste = json.loads(brut) if isinstance(brut, str) else brut
            if isinstance(liste, list):
                return liste
        except (ValueError, TypeError):
            pass
    return MOTEURS_FRAGILES_DEFAUT


def _detect_risky_engine(
    marque: str,
    motorisation: Optional[str],
    age: int,
    km: int,
    params: Optional[dict] = None,
) -> Optional[tuple[float, str]]:
    fragile, fragile_recent = _p(params, "pct_moteur_fragile") / 100, _p(params, "pct_moteur_fragile_recent") / 100
    if not motorisation:
        return None
    mot = motorisation.lower()
    marque_up = _normalize_marque(marque)
    annee = datetime.date.today().year - age
    for e in _liste_moteurs(params):
        if e.get("actif") is False:
            continue
        marques = [_normalize_marque(m) for m in e.get("marques") or []]
        if marques and marque_up not in marques:
            continue
        if any(str(w).lower() not in mot for w in e.get("tous") or []):
            continue
        un_de = [str(w).lower() for w in e.get("un_de") or []]
        if un_de and not any(w in mot for w in un_de):
            continue
        if any(str(w).lower() in mot for w in e.get("exclure") or []):
            continue
        if e.get("annee_min") and annee < int(e["annee_min"]):
            continue
        if e.get("annee_max") and annee > int(e["annee_max"]):
            continue
        nom = e.get("nom", "moteur à problème")
        raison = f" ({e['raison']})" if e.get("raison") else ""
        if age <= 3 and km <= 70_000:
            return fragile_recent, f"{nom} récent (≤3 ans, ≤70k km){raison} → {fragile_recent:.0%}"
        return fragile, f"{nom}{raison} → {fragile:.0%}"
    return None


# ── Gros SUV (pénalité si boîte manuelle) ────────────────────────────────────
# Crossovers exclus (Captur, 2008, T-Roc, CX-3, Kona, Duster, etc.)

_GROS_SUV_MODELS = {
    # Peugeot
    "5008", "3008",
    # Citroën
    "C5 AIRCROSS", "C5AIRCROSS",
    # Toyota
    "RAV4", "RAV 4",
    # BMW
    "X3", "X5", "X6", "X7",
    # Mercedes
    "GLC", "GLE", "GLS", "ML", "GL",
    # Audi
    "Q5", "Q7", "Q8",
    # Volkswagen
    "TIGUAN", "TOUAREG",
    # Skoda
    "KODIAQ",
    # Hyundai
    "TUCSON", "SANTA FE", "SANTAFE",
    # Kia
    "SPORTAGE", "SORENTO",
    # Renault
    "KOLEOS", "KADJAR",
    # Nissan
    "X-TRAIL", "XTRAIL", "X TRAIL",
    # Ford
    "KUGA",
    # Volvo
    "XC60", "XC90",
    # Mazda
    "CX-5", "CX5", "CX-60", "CX60",
    # Honda
    "CR-V", "CRV",
    # Jeep
    "CHEROKEE", "GRAND CHEROKEE",
    # Seat / Cupra
    "ATECA", "TARRACO", "FORMENTOR",
    # Opel
    "GRANDLAND",
    # DS
    "DS7", "DS 7",
    # Mitsubishi
    "OUTLANDER", "ECLIPSE CROSS", "ECLIPSECROSS",
    # Subaru
    "FORESTER", "OUTBACK",
}

def _is_gros_suv(modele: str) -> bool:
    mod = modele.upper().strip()
    for s in _GROS_SUV_MODELS:
        if s in mod:
            return True
    return False

def _is_manual(boite: Optional[str]) -> bool:
    if not boite:
        return False
    b = boite.lower()
    return not any(w in b for w in ["auto", "automatique", "dsg", "cvt", "bva", "robotis"])


# ── Réglages (page « Référence » de l'app VM) ─────────────────────────────────
# Valeurs par défaut = comportement historique ; la page Référence peut modifier chacune (en %, km ou €).
DEFAULTS: dict[str, float] = {
    "pct_base": 80, "pct_recent": 81, "pct_tres_recent": 83,
    "pct_km150": 76, "pct_km150_ancien": 70, "pct_km200": 72, "pct_km200_ancien": 70, "pct_km300": 70,
    "pct_moteur_fragile": 65, "pct_moteur_fragile_recent": 70, "pct_land_rover": 65,
    "bonus_sous_km": 2, "malus_surkm_30": 2, "malus_surkm_60": 4, "malus_gros_suv_manuel": 4,
    "km_annuel": 15000, "plafond_250k": 5000, "plafond_300k": 4000,
}


def _p(params: Optional[dict], key: str) -> float:
    try:
        return float((params or {}).get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


# ── Coefficient principal ──────────────────────────────────────────────────────

def get_rachat_pct(
    marque: str,
    modele: str,
    annee: Optional[int],
    kilometrage: Optional[int],
    boite: Optional[str],
    motorisation: Optional[str] = None,
    params: Optional[dict] = None,
) -> tuple[float, str]:
    current_year = datetime.date.today().year
    age = (current_year - annee) if annee else 10
    km = kilometrage or 80_000
    parts: list[str] = []

    # ── 1. Land Rover / Range Rover ───────────────────────────────────────────
    if _is_landrover(marque):
        pct = _p(params, "pct_land_rover") / 100
        return pct, f"Land Rover / Range Rover (fiabilité) → {pct:.0%}"

    # ── 2. Moteur à risque (coefficient fixe) ────────────────────────────────
    engine = _detect_risky_engine(marque, motorisation, age, km, params)
    if engine:
        pct, engine_label = engine
        parts.append(engine_label)
        if _is_manual(boite) and _is_gros_suv(modele):
            pct -= _p(params, "malus_gros_suv_manuel") / 100
            parts.append(f"gros SUV + boîte manuelle → -{_p(params, 'malus_gros_suv_manuel'):g}%")
        return round(pct, 4), " | ".join(parts) + f" → {round(pct * 100, 1)}%"

    # ── 3. Coefficient base selon km / âge ───────────────────────────────────
    apply_km_adjust = True

    if km > 300_000:
        pct = _p(params, "pct_km300") / 100
        parts.append(f"km extrême ({km // 1000}k) → plafond {_p(params, 'plafond_300k'):,.0f} €".replace(",", " "))
        apply_km_adjust = False
    elif km > 200_000 and age > 10:
        pct = _p(params, "pct_km200_ancien") / 100
        parts.append(f"km très élevé ({km // 1000}k) + {age} ans → {pct:.0%}")
        apply_km_adjust = False
    elif km > 200_000:
        pct = _p(params, "pct_km200") / 100
        parts.append(f"km très élevé ({km // 1000}k) → {pct:.0%}")
        apply_km_adjust = False
    elif km > 150_000 and age > 10:
        pct = _p(params, "pct_km150_ancien") / 100
        parts.append(f"km élevé ({km // 1000}k) + ancien ({age} ans) → {pct:.0%}")
        apply_km_adjust = False
    elif km > 150_000:
        pct = _p(params, "pct_km150") / 100
        parts.append(f"km élevé ({km // 1000}k) → {pct:.0%}")
        apply_km_adjust = False
    elif age < 3 and km < 50_000:
        pct = _p(params, "pct_tres_recent") / 100
        parts.append(f"très récent ({age} ans / {km // 1000}k km) → {pct:.0%}")
    elif age < 5 and km < 100_000:
        pct = _p(params, "pct_recent") / 100
        parts.append(f"récent ({age} ans / {km // 1000}k km) → {pct:.0%}")
    else:
        pct = _p(params, "pct_base") / 100
        parts.append(f"base → {pct:.0%}")

    # ── 4. Ajustement kilométrage standard (15 000 km/an) ────────────────────
    if apply_km_adjust:
        km_standard = age * _p(params, "km_annuel")
        ecart = km - km_standard
        if ecart < -30_000:
            pct += _p(params, "bonus_sous_km") / 100
            parts.append(f"sous-kilométré ({abs(ecart) // 1000:.0f}k sous standard) → +{_p(params, 'bonus_sous_km'):g}%")
        elif ecart > 60_000:
            pct -= _p(params, "malus_surkm_60") / 100
            parts.append(f"surkilométré ({ecart // 1000:.0f}k au-dessus standard) → -{_p(params, 'malus_surkm_60'):g}%")
        elif ecart > 30_000:
            pct -= _p(params, "malus_surkm_30") / 100
            parts.append(f"surkilométré ({ecart // 1000:.0f}k au-dessus standard) → -{_p(params, 'malus_surkm_30'):g}%")

    # ── 5. Pénalité gros SUV + boîte manuelle ────────────────────────────────
    if _is_manual(boite) and _is_gros_suv(modele):
        pct -= _p(params, "malus_gros_suv_manuel") / 100
        parts.append(f"gros SUV + boîte manuelle → -{_p(params, 'malus_gros_suv_manuel'):g}%")

    return round(pct, 4), " | ".join(parts) + f" → {round(pct * 100, 1)}%"


# ── Suppression des outliers ───────────────────────────────────────────────────

def supprimer_outliers(prix: list[int]) -> list[int]:
    if len(prix) < 4:
        return prix

    q1 = statistics.quantiles(prix, n=4)[0]
    q3 = statistics.quantiles(prix, n=4)[2]
    iqr = q3 - q1
    borne_basse = q1 - 1.5 * iqr
    borne_haute = q3 + 1.5 * iqr
    filtered = [p for p in prix if borne_basse <= p <= borne_haute]

    if filtered:
        med = statistics.median(filtered)
        filtered = [p for p in filtered if med * 0.75 <= p <= med * 1.10]

    return filtered if filtered else prix


# ── Calcul final ──────────────────────────────────────────────────────────────

def calculate_estimation(
    prix_bruts: list[int],
    marque: str = "",
    modele: str = "",
    motorisation: Optional[str] = None,
    finition: Optional[str] = None,
    boite: Optional[str] = None,
    annee: Optional[int] = None,
    kilometrage: Optional[int] = None,
    params: Optional[dict] = None,
) -> dict:
    prix = supprimer_outliers(sorted(prix_bruts))
    if not prix:
        prix = sorted(prix_bruts)

    def r100(v: float) -> int:
        return round(v / 100) * 100

    n = len(prix)
    prix_moyen = r100(statistics.mean(prix))
    prix_median = r100(statistics.median(prix))

    if n >= 4:
        quantiles = statistics.quantiles(prix, n=20)
        fourchette_basse = r100(quantiles[2])
        fourchette_haute = r100(quantiles[16])
    else:
        fourchette_basse = r100(min(prix))
        fourchette_haute = r100(max(prix))

    pct, methode = get_rachat_pct(marque, modele, annee, kilometrage, boite, motorisation, params)
    prix_rachat = r100(prix_median * pct)

    # Plafond dur km extrême
    km = kilometrage or 0
    if km > 300_000:
        prix_rachat = min(prix_rachat, int(_p(params, "plafond_300k")))
    elif km > 250_000:
        prix_rachat = min(prix_rachat, int(_p(params, "plafond_250k")))

    return {
        "nb_annonces":      n,
        "prix_moyen":       prix_moyen,
        "prix_median":      prix_median,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "prix_rachat":      prix_rachat,
        "methode":          methode,
        "pct_rachat":       round(pct * 100, 1),
    }
