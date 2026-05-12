import statistics


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


def calculate_estimation(prix_bruts: list[int]) -> dict:
    prix = supprimer_outliers(sorted(prix_bruts))

    if not prix:
        prix = sorted(prix_bruts)

    n = len(prix)
    prix_moyen = round(statistics.mean(prix))
    prix_median = round(statistics.median(prix))

    # Fourchette : percentile 15 → 85 pour refléter le marché réel
    if n >= 4:
        quantiles = statistics.quantiles(prix, n=20)
        fourchette_basse = round(quantiles[2])   # ~15e percentile
        fourchette_haute = round(quantiles[16])  # ~85e percentile
    else:
        fourchette_basse = min(prix)
        fourchette_haute = max(prix)

    # Prix de rachat : prix moyen marché - 15%
    prix_rachat = round(prix_moyen * 0.85)

    return {
        "nb_annonces": n,
        "prix_moyen": prix_moyen,
        "prix_median": prix_median,
        "fourchette_basse": fourchette_basse,
        "fourchette_haute": fourchette_haute,
        "prix_rachat": prix_rachat,
    }
