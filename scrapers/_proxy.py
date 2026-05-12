"""
Sélecteur de service de scraping anti-bot.

Priorité : ZENROWS_KEY → SCRAPERAPI_KEY (plan payant) → aucun (source ignorée)

ScraperAPI : https://www.scraperapi.com/  — plan Hobby $29/mois requis pour premium proxies
ZenRows    : https://www.zenrows.com/     — tier gratuit inclut antibot=true (1000 req/mois)
"""
import os
from urllib.parse import quote

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY", "")
ZENROWS_KEY    = os.getenv("ZENROWS_KEY", "")


def proxy_available() -> bool:
    return bool(ZENROWS_KEY or SCRAPERAPI_KEY)


def build_url(target: str, js_render: bool = True) -> str:
    """Retourne l'URL finale à appeler selon le service configuré."""
    if ZENROWS_KEY:
        return (
            f"https://api.zenrows.com/v1/"
            f"?apikey={ZENROWS_KEY}"
            f"&url={quote(target, safe='')}"
            + ("&js_render=true&antibot=true&premium_proxy=true" if js_render else "&antibot=true&premium_proxy=true")
        )
    if SCRAPERAPI_KEY:
        # Nécessite le plan Hobby ($29/mois) ou supérieur
        return (
            f"http://api.scraperapi.com"
            f"?api_key={SCRAPERAPI_KEY}"
            f"&url={quote(target, safe='')}"
            f"&country_code=fr"
            + ("&render=true&ultra_premium=true" if js_render else "&ultra_premium=true")
        )
    raise RuntimeError("Aucun service proxy configuré (ZENROWS_KEY ou SCRAPERAPI_KEY requis)")


def service_name() -> str:
    if ZENROWS_KEY:
        return "zenrows"
    if SCRAPERAPI_KEY:
        return "scraperapi"
    return "none"
