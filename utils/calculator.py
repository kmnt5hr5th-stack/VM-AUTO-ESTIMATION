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


# ── Marques premium (plancher de rachat à 72%) ────────────────────────────────
# BMW, Mini, Audi, Mercedes : forte demande à la revente, ne pas sur-pénaliser.
# L'entretien coûteux se gère au diagnostic physique, pas via pénalité systématique.
PREMIUM_BRANDS = {"BMW", "MINI", "AUDI", "MERCEDES"}


def get_rachat_pct(
    marque: str,
    annee: Optional[int],
    kilometrage: Optional[int],
    boite: Optional[str],
) -> tuple[float, str]:
    """
    Retourne (pourcentage_rachat, explication).

    Fourchette cible : 68-77% de la valeur marché.
    Base par défaut  : 73.5%
    """
    current_year = 2026
    age = (current_year - annee) if annee else 10
    km  = kilometrage or 80_000

    is_auto = bool(boite and any(
        w in boite.lower()
        for w in ["auto", "automatique", "dsg", "cvt", "bva", "bva6", "bva7", "bva8"]
    ))
    marque_up  = _normalize_marque(marque)
    is_premium = marque_up in PREMIUM_BRANDS

    # ── km > 200 000 → bas de fourchette (risque panne, revente difficile) ──
    if km > 200_000:
        if is_auto and age > 7:
            pct   = 0.68
            label = f"km très élevé ({km // 1000}k) + boîte auto ancienne ({age} ans) → 68%"
        else:
            pct   = 0.70
            label = f"km très élevé ({km // 1000}k) → 70%"
        return round(pct, 4), label

    # ── Récent (<5 ans) + faible km (<100k) → haut de fourchette ────────────
    if age < 5 and km < 100_000:
        pct   = 0.76
        label = f"récent ({age} ans / {km // 1000}k km) → haut de fourchette 76%"
        return pct, label

    # ── Base 73.5% avec ajustements ──────────────────────────────────────────
    pct   = 0.735
    parts = ["base 73.5%"]

    # Boîte auto sur véhicule ancien (>8 ans) ou km élevé (>150k) : risque mécanique
    if is_auto and (age > 8 or km > 150_000):
        pct   -= 0.015
        parts.append(f"boîte auto + {'âge ' + str(age) + ' ans' if age > 8 else str(km // 1000) + 'k km'} → -1.5%")

    # Plancher premium : BMW, Mini, Audi, Mercedes → min 72%
    if is_premium and pct < 0.72:
        pct = 0.72
        parts.append(f"marque premium ({marque}) → plancher 72%")

    label = " | ".join(parts) + f" → {round(pct * 100, 1)}%"
    return round(pct, 4), label


def supprimer_outliers(prix: list[int]) -> list[int]:
    if len(prix) < 4:
        return prix

    q1 = statistics.quantiles(prix, n=4)[0]
    q3 = statistics.quantiles(prix, n=4)[2]
    iqr = q3 - q1
    borne_basse = q1 - 1.5 * iqr
    borne_haute = q3 + 1.5 * iqr
    filtered = [p for p in prix if borne_basse <= p <= borne_haute]

    # Filtre asymétrique : bonnes affaires OK (-25%), mais exclure les "hors cote" (+10%)
    if filtered:
        med      = statistics.median(filtered)
        filtered = [p for p in filtered if med * 0.75 <= p <= med * 1.10]

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

    n           = len(prix)
    prix_moyen  = r100(statistics.mean(prix))
    prix_median = r100(statistics.median(prix))

    if n >= 4:
        quantiles        = statistics.quantiles(prix, n=20)
        fourchette_basse = r100(quantiles[2])    # ~15e percentile
        fourchette_haute = r100(quantiles[16])   # ~85e percentile
    else:
        fourchette_basse = r100(min(prix))
        fourchette_haute = r100(max(prix))

    pct, methode = get_rachat_pct(marque, annee, kilometrage, boite)
    prix_rachat  = r100(prix_median * pct)

    # Plafond dur pour kilométrages extrêmes
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
        "pct_rachat":       round(pct * 100, 1),
    }
