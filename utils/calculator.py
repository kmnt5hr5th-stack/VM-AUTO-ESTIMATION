import statistics
from typing import Optional

# ── Normalisation des marques (aliases courants) ──────────────────────────────

_MARQUE_ALIASES: dict[str, str] = {
    "MERCEDES-BENZ": "MERCEDES",
    "MERCEDES BENZ": "MERCEDES",
    "VW":            "VOLKSWAGEN",
    "LAND-ROVER":    "LAND ROVER",
    "CITROËN":       "CITROEN",
    "CITROËN":       "CITROEN",
    "ALFA-ROMEO":    "ALFA ROMEO",
}


def _normalize_marque(marque: str) -> str:
    up = marque.strip().upper()
    return _MARQUE_ALIASES.get(up, up)


# ── Catégories véhicules ──────────────────────────────────────────────────────

PREMIUM_SUVS: dict[str, list[str]] = {
    "BMW":         ["X3", "X4", "X5", "X6", "X7"],
    "AUDI":        ["Q5", "Q7", "Q8"],
    "MERCEDES":    ["GLC", "GLE", "GLS", "EQC"],
    "PORSCHE":     ["CAYENNE", "MACAN"],
    "VOLVO":       ["XC60", "XC90"],
    "LAND ROVER":  ["RANGE ROVER", "DISCOVERY", "DEFENDER"],
    "JAGUAR":      ["F-PACE", "E-PACE", "I-PACE"],
    "LEXUS":       ["NX", "RX", "UX", "LX"],
    "MASERATI":    ["LEVANTE"],
    "LAMBORGHINI": ["URUS"],
    "BENTLEY":     ["BENTAYGA"],
    "ALFA ROMEO":  ["STELVIO"],
    "DS":          ["DS 7", "DS7"],
}

PREMIUM_SEDANS: dict[str, list[str]] = {
    "AUDI":      ["A1", "A3", "A4", "A5", "A6", "A7", "A8", "TT", "TTS", "TTRS"],
    "BMW":       ["SERIE 3", "SERIE 4", "SERIE 5", "SERIE 6", "SERIE 7",
                  "SERIE 8", "M2", "M3", "M4", "M5", "Z4", "I3", "I4", "I8"],
    "MERCEDES":  ["CLASSE A", "CLASSE B", "CLASSE C", "CLASSE E", "CLASSE S",
                  "CLA", "CLS", "SL", "SLK", "AMG", "EQA", "EQB", "EQC", "EQE", "EQS"],
    "VOLKSWAGEN": ["PASSAT", "ARTEON", "CC", "PHAETON"],
    "VOLVO":     ["S60", "S90", "V60", "V90"],
    "LEXUS":     ["IS", "ES", "GS", "LS", "RC", "LC", "CT"],
    "JAGUAR":    ["XE", "XF", "XJ", "F-TYPE"],
    "PORSCHE":   ["911", "718", "PANAMERA", "TAYCAN"],
    "MASERATI":  ["GHIBLI", "QUATTROPORTE", "GRECALE"],
    "GENESIS":   ["G70", "G80", "G90"],
    "ALFA ROMEO": ["GIULIA", "GIULIETTA", "BRERA", "159", "156"],
    "DS":        ["DS 4", "DS4", "DS 9", "DS9"],
}

STANDARD_SUVS: dict[str, list[str]] = {
    "RENAULT":    ["KADJAR", "KOLEOS", "CAPTUR", "ARKANA"],
    "PEUGEOT":    ["3008", "5008", "2008"],
    "CITROEN":    ["C5 AIRCROSS", "C3 AIRCROSS"],
    "VOLKSWAGEN": ["TIGUAN", "T-ROC", "T-CROSS"],
    "TOYOTA":     ["RAV4", "C-HR", "YARIS CROSS"],
    "HYUNDAI":    ["TUCSON", "SANTA FE", "KONA"],
    "KIA":        ["SPORTAGE", "SORENTO", "STONIC", "NIRO"],
    "FORD":       ["KUGA", "PUMA", "ECOSPORT"],
    "NISSAN":     ["QASHQAI", "X-TRAIL", "JUKE"],
    "SEAT":       ["ATECA", "TARRACO"],
    "SKODA":      ["KODIAQ", "KAROQ", "KAMIQ"],
    "OPEL":       ["GRANDLAND", "MOKKA", "CROSSLAND"],
    "DACIA":      ["DUSTER"],
    "MAZDA":      ["CX-3", "CX-5", "CX-30"],
    "HONDA":      ["CR-V", "HR-V"],
    "JEEP":       ["COMPASS", "RENEGADE"],
    "MITSUBISHI": ["ECLIPSE CROSS", "OUTLANDER"],
    "SUBARU":     ["FORESTER", "XV", "OUTBACK"],
    "FIAT":       ["500X", "500 X"],
}

# Marques premium à entretien coûteux — difficiles à revendre pour un pro, décote plus forte
PREMIUM_COSTLY: dict[str, list[str]] = {
    "MINI":       ["COUNTRYMAN", "CLUBMAN", "PACEMAN"],
    "ALFA ROMEO": ["STELVIO", "TONALE", "GIULIA", "GIULIETTA"],
    "DS":         ["DS 7", "DS7", "DS 4", "DS4", "DS 9", "DS9"],
    "JEEP":       ["WRANGLER", "GRAND CHEROKEE"],
    "LAND ROVER": ["DISCOVERY SPORT", "FREELANDER"],
}

CITY_CARS: dict[str, list[str]] = {
    "RENAULT":    ["CLIO", "TWINGO", "ZOE"],
    "PEUGEOT":    ["208", "107", "108"],
    "CITROEN":    ["C1", "C2", "C3"],
    "OPEL":       ["CORSA", "ADAM"],
    "VOLKSWAGEN": ["POLO", "UP"],
    "FORD":       ["FIESTA", "KA"],
    "TOYOTA":     ["YARIS", "AYGO"],
    "HYUNDAI":    ["I10", "I20"],
    "KIA":        ["PICANTO", "RIO"],
    "DACIA":      ["SANDERO"],
    "FIAT":       ["500", "PANDA", "PUNTO"],
    "SEAT":       ["IBIZA", "ARONA"],
    "SKODA":      ["FABIA", "CITIGO"],
    "SMART":      ["FORTWO", "FORFOUR"],
    "MINI":       ["MINI", "ONE", "COOPER"],
}

WEAK_ENGINE_KEYWORDS = ["puretech", "pure tech", "ecoboost", "eco boost", "ecoboot"]


def _km_penalty(kilometrage: Optional[int]) -> tuple[float, str]:
    # Le prix marché est déjà calculé sur des annonces au km proche (±10k) →
    # la pénalité km sert uniquement à ajuster la marge pro, pas à re-déprécier le véhicule.
    if kilometrage is None or kilometrage <= 150_000:
        return 1.0, ""
    elif kilometrage <= 200_000:
        return 0.96, " + kilométrage élevé -4%"
    elif kilometrage <= 250_000:
        return 0.90, " + kilométrage très élevé -10%"
    else:
        return 0.82, " + kilométrage excessif -18%"


def get_discount_rate(
    marque: str,
    modele: str,
    motorisation: Optional[str],
    finition: Optional[str] = None,
    boite: Optional[str] = None,
) -> tuple[float, str]:
    """Retourne (multiplicateur, raison).

    Framework (estimation attractive pour faire venir le client — ajustement en RDV) :
      SUV premium      → -14%  (forte demande, prix stables)
      SUV standard     → -15%  (bonne demande)
      Citadine/volume  → -10%  (marché liquide)
      Berline/standard → -12%  (défaut)
      Boîte manuelle   → -3%   supplémentaire
      Moteur à risque  → -22%  supplémentaire
    """
    is_manual = boite and any(
        w in boite.lower() for w in ["mecanique", "mécanique", "manuelle", "bvm", "bm"]
    )

    marque_up = _normalize_marque(marque)
    modele_up = modele.strip().upper()

    # 1. Moteur à risque (PureTech / EcoBoost) — prioritaire
    if motorisation:
        m = motorisation.lower().replace("-", " ").replace("_", " ")
        if any(k in m for k in WEAK_ENGINE_KEYWORDS):
            base = 0.78
            label = "Moteur à risque (PureTech/EcoBoost) - 22%"
            if is_manual:
                return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
            return base, label

    # 2. SUV premium → -14%
    for brand, suvs in PREMIUM_SUVS.items():
        if brand == marque_up:
            for suv in suvs:
                if suv in modele_up:
                    base, label = 0.86, f"SUV premium ({marque} {suv.title()}) - 14%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 3. Premium entretien coûteux (Mini, Alfa, DS, Jeep Wrangler…) → -32%
    for brand, models in PREMIUM_COSTLY.items():
        if brand == marque_up:
            for m in models:
                if m in modele_up:
                    base, label = 0.68, f"Premium entretien coûteux ({marque} {m.title()}) - 32%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 3b. Berline premium → -22%
    for brand, models in PREMIUM_SEDANS.items():
        if brand == marque_up:
            for m in models:
                if m in modele_up:
                    base, label = 0.78, f"Berline premium ({marque} {m.title()}) - 22%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 4. SUV standard → -15%
    for brand, suvs in STANDARD_SUVS.items():
        if brand == marque_up:
            for suv in suvs:
                if suv in modele_up:
                    base, label = 0.85, f"SUV standard ({marque} {suv.title()}) - 15%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 5. Citadine/volume → -10%
    for brand, cars in CITY_CARS.items():
        if brand == marque_up:
            for car in cars:
                if car in modele_up:
                    base, label = 0.90, f"Citadine ({marque} {car.title()}) - 10%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 6. Berline/break/standard → -12%
    base, label = 0.88, "Berline/standard - 12%"
    if is_manual:
        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
    return base, label


def supprimer_outliers(prix: list[int]) -> list[int]:
    if len(prix) < 4:
        return prix

    # Filtre IQR standard
    q1 = statistics.quantiles(prix, n=4)[0]
    q3 = statistics.quantiles(prix, n=4)[2]
    iqr = q3 - q1
    borne_basse = q1 - 1.5 * iqr
    borne_haute = q3 + 1.5 * iqr
    filtered = [p for p in prix if borne_basse <= p <= borne_haute]

    # Filtre supplémentaire : exclure tout prix > 30% au-dessus de la médiane
    # (protège contre les annonces aberrantes sur petits échantillons)
    if filtered:
        med = statistics.median(filtered)
        filtered = [p for p in filtered if med * 0.85 <= p <= med * 1.15]

    return filtered if filtered else prix


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
    prix_moyen  = r100(statistics.mean(prix))
    prix_median = r100(statistics.median(prix))

    if n >= 4:
        quantiles        = statistics.quantiles(prix, n=20)
        fourchette_basse = r100(quantiles[2])   # ~15e percentile
        fourchette_haute = r100(quantiles[16])  # ~85e percentile
    else:
        fourchette_basse = r100(min(prix))
        fourchette_haute = r100(max(prix))

    coef, methode = get_discount_rate(marque, modele, motorisation, finition, boite)
    km_coef, km_label = _km_penalty(kilometrage)
    prix_rachat = r100(prix_median * coef * km_coef * 0.90)
    methode += km_label + " + marge pro -10%"

    # Plafond dur uniquement pour les kilométrages extrêmes
    if kilometrage is not None:
        if kilometrage > 300_000:
            prix_rachat = min(prix_rachat, 3_000)
        elif kilometrage > 250_000:
            prix_rachat = min(prix_rachat, 5_000)

    return {
        "nb_annonces":      n,
        "prix_moyen":       prix_moyen,
        "prix_median":      prix_median,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "prix_rachat":      prix_rachat,
        "methode":          methode,
    }
