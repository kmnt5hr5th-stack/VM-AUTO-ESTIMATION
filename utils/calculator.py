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

    # PureTech 1.2 — Peugeot, Citroën, DS, Opel
    is_puretech = (
        marque_up in ("PEUGEOT", "CITROEN", "DS", "OPEL")
        and ("1.2" in mot or "puretech" in mot)
    )
    if is_puretech:
        if age <= 3 and km <= 70_000:
            return fragile_recent, f"PureTech 1.2 récent (≤3 ans, ≤70k km) → {fragile_recent:.0%}"
        return fragile, f"PureTech 1.2 (chaîne distribution) → {fragile:.0%}"

    # 1.2 TCe — Renault, Dacia, Nissan
    is_tce12 = (
        marque_up in ("RENAULT", "DACIA", "NISSAN")
        and "1.2" in mot
        and "dci" not in mot  # DCi = diesel, pas concerné
    )
    if is_tce12:
        if age <= 3 and km <= 70_000:
            return fragile_recent, f"1.2 TCe récent (≤3 ans, ≤70k km) → {fragile_recent:.0%}"
        return fragile, f"1.2 TCe (chaîne distribution) → {fragile:.0%}"

    # EcoBoost 1.0 / 1.5 — Ford
    is_ecoboost = (
        marque_up == "FORD"
        and "ecoboost" in mot
        and ("1.0" in mot or "1.5" in mot)
    )
    if is_ecoboost:
        return fragile, f"EcoBoost 1.0/1.5 (joint culasse) → {fragile:.0%}"

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
