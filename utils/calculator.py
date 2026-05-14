import statistics
from typing import Optional

# SUV premium : décote -20%
PREMIUM_SUVS: dict[str, list[str]] = {
    "BMW":         ["X3", "X4", "X5", "X6", "X7"],
    "Audi":        ["Q5", "Q7", "Q8"],
    "Mercedes":    ["GLC", "GLE", "GLS", "EQC"],
    "Porsche":     ["Cayenne", "Macan"],
    "Volvo":       ["XC60", "XC90"],
    "Land Rover":  ["Range Rover", "Discovery", "Defender"],
    "Jaguar":      ["F-Pace", "E-Pace", "I-Pace"],
    "Lexus":       ["NX", "RX", "UX", "LX"],
    "Maserati":    ["Levante"],
    "Lamborghini": ["Urus"],
    "Bentley":     ["Bentayga"],
    "Alfa Romeo":  ["Stelvio"],
    "DS":          ["DS 7"],
}

# Moteurs à problèmes connus : décote -35%
WEAK_ENGINE_KEYWORDS = ["puretech", "pure tech", "ecoboost", "eco boost", "ecoboot"]

# Finitions flotte/entrée de gamme : décote -25% (+ malus boîte mécanique)
FLEET_FINITION_KEYWORDS = [
    "france business", "business", "active", "access", "trend",
    "like", "feel", "edition", "club", "confort", "essential",
    "expression", "life", "live", "reference", "urban",
]


def get_discount_rate(
    marque: str,
    modele: str,
    motorisation: Optional[str],
    finition: Optional[str] = None,
    boite: Optional[str] = None,
) -> tuple[float, str]:
    """Retourne (multiplicateur, raison)."""
    # PureTech / EcoBoost prioritaire
    if motorisation:
        m = motorisation.lower().replace("-", " ").replace("_", " ")
        if any(k in m for k in WEAK_ENGINE_KEYWORDS):
            return 0.65, "Moteur à risque (PureTech/EcoBoost) - 35%"

    # SUV premium
    marque_up = marque.strip().upper()
    modele_up = modele.strip().upper()
    for brand, suvs in PREMIUM_SUVS.items():
        if brand.upper() == marque_up:
            for suv in suvs:
                if suv.upper() in modele_up or modele_up in suv.upper():
                    return 0.80, f"SUV premium ({brand} {suv}) - 20%"

    # Finition flotte/entrée de gamme
    if finition:
        f = finition.lower().strip()
        if any(k in f for k in FLEET_FINITION_KEYWORDS):
            is_manual = boite and any(w in boite.lower() for w in ["mecanique", "mécanique", "manuelle", "bvm", "bm"])
            if is_manual:
                return 0.70, f"Finition flotte ({finition}) + boîte mécanique - 30%"
            return 0.75, f"Finition flotte ({finition}) - 25%"

    # Malus boîte mécanique seul (hors flotte)
    if boite and any(w in boite.lower() for w in ["mecanique", "mécanique", "manuelle", "bvm", "bm"]):
        return 0.82, "Standard + boîte mécanique - 18%"

    return 0.85, "Standard - 15%"


def supprimer_outliers(prix: list[int]) -> list[int]:
    """Supprime les valeurs aberrantes via l'IQR (méthode boîte à moustaches)."""
    if len(prix) < 4:
        return prix
    q1 = statistics.quantiles(prix, n=4)[0]
    q3 = statistics.quantiles(prix, n=4)[2]
    iqr = q3 - q1
    borne_basse = q1 - 1.5 * iqr
    borne_haute = q3 + 1.5 * iqr
    return [p for p in prix if borne_basse <= p <= borne_haute]


def calculate_estimation(
    prix_bruts: list[int],
    marque: str = "",
    modele: str = "",
    motorisation: Optional[str] = None,
    finition: Optional[str] = None,
    boite: Optional[str] = None,
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
        fourchette_basse = r100(quantiles[2])   # ~15e percentile
        fourchette_haute = r100(quantiles[16])  # ~85e percentile
    else:
        fourchette_basse = r100(min(prix))
        fourchette_haute = r100(max(prix))

    coef, methode = get_discount_rate(marque, modele, motorisation, finition, boite)
    prix_rachat = r100(prix_moyen * coef)

    return {
        "nb_annonces": n,
        "prix_moyen": prix_moyen,
        "prix_median": prix_median,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "prix_rachat": prix_rachat,
        "methode": methode,
    }
