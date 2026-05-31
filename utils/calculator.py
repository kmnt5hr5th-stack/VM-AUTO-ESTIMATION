import statistics
import datetime
from typing import Optional

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

STANDARD_SUVS: dict[str, list[str]] = {
    "RENAULT":   ["KADJAR", "KOLEOS", "CAPTUR", "ARKANA"],
    "PEUGEOT":   ["3008", "5008", "2008"],
    "CITROEN":   ["C5 AIRCROSS", "C3 AIRCROSS"],
    "VOLKSWAGEN":["TIGUAN", "T-ROC", "T-CROSS"],
    "TOYOTA":    ["RAV4", "C-HR", "YARIS CROSS"],
    "HYUNDAI":   ["TUCSON", "SANTA FE", "KONA"],
    "KIA":       ["SPORTAGE", "SORENTO", "STONIC", "NIRO"],
    "FORD":      ["KUGA", "PUMA", "ECOSPORT"],
    "NISSAN":    ["QASHQAI", "X-TRAIL", "JUKE"],
    "SEAT":      ["ATECA", "TARRACO"],
    "SKODA":     ["KODIAQ", "KAROQ", "KAMIQ"],
    "OPEL":      ["GRANDLAND", "MOKKA", "CROSSLAND"],
    "DACIA":     ["DUSTER"],
    "MAZDA":     ["CX-3", "CX-5", "CX-30"],
    "HONDA":     ["CR-V", "HR-V"],
    "JEEP":      ["COMPASS", "RENEGADE"],
    "MITSUBISHI":["ECLIPSE CROSS", "OUTLANDER"],
    "SUBARU":    ["FORESTER", "XV"],
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


def get_discount_rate(
    marque: str,
    modele: str,
    motorisation: Optional[str],
    finition: Optional[str] = None,
    boite: Optional[str] = None,
) -> tuple[float, str]:
    """Retourne (multiplicateur, raison).

    Framework :
      SUV premium      → -5%   (forte demande, prix stables)
      SUV standard     → -8%   (bonne demande)
      Citadine/volume  → -10%  (marché liquide)
      Berline/standard → -12%  (défaut)
      Boîte manuelle   → -3%   supplémentaire
      Moteur à risque  → -20%  supplémentaire
    """
    is_manual = boite and any(
        w in boite.lower() for w in ["mecanique", "mécanique", "manuelle", "bvm", "bm"]
    )

    marque_up = marque.strip().upper()
    modele_up = modele.strip().upper()

    # 1. Moteur à risque (PureTech / EcoBoost) — prioritaire
    if motorisation:
        m = motorisation.lower().replace("-", " ").replace("_", " ")
        if any(k in m for k in WEAK_ENGINE_KEYWORDS):
            base = 0.80
            label = "Moteur à risque (PureTech/EcoBoost) - 20%"
            if is_manual:
                return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
            return base, label

    # 2. SUV premium → -5%
    for brand, suvs in PREMIUM_SUVS.items():
        if brand == marque_up:
            for suv in suvs:
                if suv in modele_up:
                    base, label = 0.95, f"SUV premium ({marque} {suv.title()}) - 5%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 3. SUV standard → -8%
    for brand, suvs in STANDARD_SUVS.items():
        if brand == marque_up:
            for suv in suvs:
                if suv in modele_up:
                    base, label = 0.92, f"SUV standard ({marque} {suv.title()}) - 8%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 4. Citadine/volume → -10%
    for brand, cars in CITY_CARS.items():
        if brand == marque_up:
            for car in cars:
                if car in modele_up:
                    base, label = 0.90, f"Citadine ({marque} {car.title()}) - 10%"
                    if is_manual:
                        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
                    return base, label

    # 5. Berline/break/standard → -12%
    base, label = 0.88, "Berline/standard - 12%"
    if is_manual:
        return round(base * 0.97, 4), label + " + boîte manuelle - 3%"
    return base, label


def supprimer_outliers(prix: list[int]) -> list[int]:
    if len(prix) < 4:
        return prix
    q1 = statistics.quantiles(prix, n=4)[0]
    q3 = statistics.quantiles(prix, n=4)[2]
    iqr = q3 - q1
    borne_basse = q1 - 1.5 * iqr
    borne_haute = q3 + 1.5 * iqr
    return [p for p in prix if borne_basse <= p <= borne_haute]


def _age_factor(annee: int) -> tuple[float, str]:
    """Pénalité ancienneté : les annonces LBC surévaluent d'autant plus que la voiture est vieille."""
    anciennete = datetime.date.today().year - annee
    if anciennete >= 14:
        return 0.68, f"ancienneté {anciennete} ans -32%"
    if anciennete >= 11:
        return 0.77, f"ancienneté {anciennete} ans -23%"
    if anciennete >= 8:
        return 0.92, f"ancienneté {anciennete} ans -8%"
    return 1.0, ""


def calculate_estimation(
    prix_bruts: list[int],
    marque: str = "",
    modele: str = "",
    motorisation: Optional[str] = None,
    finition: Optional[str] = None,
    boite: Optional[str] = None,
    annee: Optional[int] = None,
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
        quantiles       = statistics.quantiles(prix, n=20)
        fourchette_basse = r100(quantiles[2])   # ~15e percentile
        fourchette_haute = r100(quantiles[16])  # ~85e percentile
    else:
        fourchette_basse = r100(min(prix))
        fourchette_haute = r100(max(prix))

    coef, methode = get_discount_rate(marque, modele, motorisation, finition, boite)

    if annee:
        age_f, age_label = _age_factor(annee)
        if age_f < 1.0:
            coef   = round(coef * age_f, 4)
            methode = methode + " + " + age_label

    prix_rachat = r100(prix_median * coef)

    return {
        "nb_annonces":     n,
        "prix_moyen":      prix_moyen,
        "prix_median":     prix_median,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "prix_rachat":     prix_rachat,
        "methode":         methode,
    }
