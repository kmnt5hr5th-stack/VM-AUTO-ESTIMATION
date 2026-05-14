"""
Sélecteur de service de scraping anti-bot.

Priorité : FLARESOLVERR_URL → ZENROWS_KEY → SCRAPERAPI_KEY → aucun

FlareSolverr : https://github.com/FlareSolverr/FlareSolverr  — auto-hébergé, gratuit
ZenRows      : https://www.zenrows.com/     — tier gratuit 1000 req/mois
ScraperAPI   : https://www.scraperapi.com/  — plan Hobby $29/mois
"""
import os
from urllib.parse import quote

FLARESOLVERR_URL  = os.getenv("FLARESOLVERR_URL", "").rstrip("/")
LBC_PROXY_URL     = os.getenv("LBC_PROXY_URL", "").rstrip("/")
SCRAPERAPI_KEY   = os.getenv("SCRAPERAPI_KEY", "")
ZENROWS_KEY      = os.getenv("ZENROWS_KEY", "")


def proxy_available() -> bool:
    return bool(FLARESOLVERR_URL or ZENROWS_KEY or SCRAPERAPI_KEY)


def flaresolverr_available() -> bool:
    return bool(FLARESOLVERR_URL)


def build_url(target: str, js_render: bool = True) -> str:
    """Retourne l'URL finale pour ZenRows ou ScraperAPI (non FlareSolverr)."""
    if ZENROWS_KEY:
        return (
            f"https://api.zenrows.com/v1/"
            f"?apikey={ZENROWS_KEY}"
            f"&url={quote(target, safe='')}"
            + ("&js_render=true&antibot=true&premium_proxy=true" if js_render else "&antibot=true&premium_proxy=true")
        )
    if SCRAPERAPI_KEY:
        return (
            f"http://api.scraperapi.com"
            f"?api_key={SCRAPERAPI_KEY}"
            f"&url={quote(target, safe='')}"
            f"&country_code=fr"
            + ("&render=true&ultra_premium=true" if js_render else "&ultra_premium=true")
        )
    raise RuntimeError("Aucun service proxy configuré")


def service_name() -> str:
    if FLARESOLVERR_URL:
        return "flaresolverr"
    if ZENROWS_KEY:
        return "zenrows"
    if SCRAPERAPI_KEY:
        return "scraperapi"
    return "none"
