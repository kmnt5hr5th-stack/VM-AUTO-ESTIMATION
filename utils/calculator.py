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
) -> Optional[tuple[float, str]]:
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
            return 0.70, "PureTech 1.2 récent (≤3 ans, ≤70k km) → 70%"
        return 0.65, "PureTech 1.2 (chaîne distribution) → 65%"

    # 1.2 TCe — Renault, Dacia, Nissan
    is_tce12 = (
        marque_up in ("RENAULT", "DACIA", "NISSAN")
        and "1.2" in mot
        and "dci" not in mot  # DCi = diesel, pas concerné
    )
    if is_tce12:
        if age <= 3 and km <= 70_000:
            return 0.70, "1.2 TCe récent (≤3 ans, ≤70k km) → 70%"
        return 0.65, "1.2 TCe (chaîne distribution) → 65%"

    # EcoBoost 1.0 / 1.5 — Ford
    is_ecoboost = (
        marque_up == "FORD"
        and "ecoboost" in mot
        and ("1.0" in mot or "1.5" in mot)
    )
    if is_ecoboost:
        return 0.65, "EcoBoost 1.0/1.5 (joint culasse) → 65%"

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


# ── Coefficient principal ──────────────────────────────────────────────────────

def get_rachat_pct(
    marque: str,
    modele: str,
    annee: Optional[int],
    kilometrage: Optional[int],
    boite: Optional[str],
    motorisation: Optional[str] = None,
) -> tuple[float, str]:
    current_year = datetime.date.today().year
    age = (current_year - annee) if annee else 10
    km = kilometrage or 80_000
    parts: list[str] = []

    # ── 1. Land Rover / Range Rover ───────────────────────────────────────────
    if _is_landrover(marque):
        return 0.65, "Land Rover / Range Rover (fiabilité) → 65%"

    # ── 2. Moteur à risque (coefficient fixe) ────────────────────────────────
    engine = _detect_risky_engine(marque, motorisation, age, km)
    if engine:
        pct, engine_label = engine
        parts.append(engine_label)
        if _is_manual(boite) and _is_gros_suv(modele):
            pct -= 0.04
            parts.append("gros SUV + boîte manuelle → -4%")
        return round(pct, 4), " | ".join(parts) + f" → {round(pct * 100, 1)}%"

    # ── 3. Coefficient base selon km / âge ───────────────────────────────────
    apply_km_adjust = True

    if km > 300_000:
        pct = 0.70
        parts.append(f"km extrême ({km // 1000}k) → plafond 4 000 €")
        apply_km_adjust = False
    elif km > 200_000 and age > 10:
        pct = 0.70
        parts.append(f"km très élevé ({km // 1000}k) + {age} ans → 70%")
        apply_km_adjust = False
    elif km > 200_000:
        pct = 0.72
        parts.append(f"km très élevé ({km // 1000}k) → 72%")
        apply_km_adjust = False
    elif km > 150_000 and age > 10:
        pct = 0.70
        parts.append(f"km élevé ({km // 1000}k) + ancien ({age} ans) → 70%")
        apply_km_adjust = False
    elif km > 150_000:
        pct = 0.76
        parts.append(f"km élevé ({km // 1000}k) → 76%")
        apply_km_adjust = False
    elif age < 3 and km < 50_000:
        pct = 0.83
        parts.append(f"très récent ({age} ans / {km // 1000}k km) → 83%")
    elif age < 5 and km < 100_000:
        pct = 0.81
        parts.append(f"récent ({age} ans / {km // 1000}k km) → 81%")
    else:
        pct = 0.80
        parts.append("base → 80%")

    # ── 4. Ajustement kilométrage standard (15 000 km/an) ────────────────────
    if apply_km_adjust:
        km_standard = age * 15_000
        ecart = km - km_standard
        if ecart < -30_000:
            pct += 0.02
            parts.append(f"sous-kilométré ({abs(ecart) // 1000}k sous standard) → +2%")
        elif ecart > 60_000:
            pct -= 0.04
            parts.append(f"surkilométré ({ecart // 1000}k au-dessus standard) → -4%")
        elif ecart > 30_000:
            pct -= 0.02
            parts.append(f"surkilométré ({ecart // 1000}k au-dessus standard) → -2%")

    # ── 5. Pénalité gros SUV + boîte manuelle ────────────────────────────────
    if _is_manual(boite) and _is_gros_suv(modele):
        pct -= 0.04
        parts.append("gros SUV + boîte manuelle → -4%")

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

    pct, methode = get_rachat_pct(marque, modele, annee, kilometrage, boite, motorisation)
    prix_rachat = r100(prix_median * pct)

    # Plafond dur km extrême
    km = kilometrage or 0
    if km > 300_000:
        prix_rachat = min(prix_rachat, 4_000)
    elif km > 250_000:
        prix_rachat = min(prix_rachat, 5_000)

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
