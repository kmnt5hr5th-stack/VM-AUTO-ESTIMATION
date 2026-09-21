"""
Scrape le catalogue LeBonCoin : marque → modèles → finitions
Stratégie :
  1. Naviguer vers /recherche?category=2&u_car_brand={BRAND}
  2. Ouvrir le panel filtre "Modèle", lire les options (valeur + label)
  3. Pour chaque modèle : ouvrir le panel "Finition", lire les options
  4. Sauvegarder dans lbc_catalog.json après chaque marque

Lancer localement (headless=False pour passer DataDome) :
  python -m scrapers.lbc_catalog_scraper
"""

import asyncio
import json
import random
from pathlib import Path
from playwright.async_api import async_playwright, Page

OUTPUT_FILE = Path(__file__).parent.parent / "lbc_catalog.json"
BRANDS_FILE = Path(__file__).parent.parent / "lbc_api_discovery.json"
SEARCH_BASE = "https://www.leboncoin.fr/recherche?category=2"


# ── Utilitaires ───────────────────────────────────────────────────────────────

def load_brands() -> list[str]:
    with open(BRANDS_FILE) as f:
        data = json.load(f)
    brands = list(data[0]["body"]["aggregations"]["u_car_brand"].keys())
    return sorted(b for b in brands if b != "AUTRE")


def load_catalog() -> dict:
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            return json.load(f)
    return {}


def save_catalog(catalog: dict):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


async def wait(a=0.5, b=1.2):
    await asyncio.sleep(random.uniform(a, b))


# ── Lecture des options d'un panel filtre ────────────────────────────────────

async def read_panel_options(page: Page) -> dict[str, str]:
    """
    Lit les options affichées dans le panel filtre ouvert.
    Retourne {label: code_lbc} ex: {"308": "PEUGEOT_308", "3008": "PEUGEOT_3008"}
    Essaie plusieurs stratégies de lecture DOM.
    """
    options: dict[str, str] = {}

    # Stratégie 1 : intercepter les URL des liens (option = lien href avec param)
    try:
        hrefs = await page.evaluate("""
            () => {
                const links = [...document.querySelectorAll('a[href*="u_car_model"], a[href*="u_car_finition"]')];
                return links.map(a => a.href);
            }
        """)
        for href in hrefs:
            for param in ["u_car_model", "u_car_finition"]:
                if param in href:
                    import urllib.parse as up
                    qs = up.urlparse(href).query
                    params = up.parse_qs(qs)
                    val = params.get(param, [None])[0]
                    if val:
                        label = val.split("_", 2)[-1] if val.count("_") >= 2 else val
                        options[label] = val
        if options:
            return options
    except Exception:
        pass

    # Stratégie 2 : lire les checkboxes (value = code LBC)
    try:
        items = await page.evaluate("""
            () => {
                const boxes = [...document.querySelectorAll('input[type="checkbox"]')];
                return boxes
                    .filter(b => b.value && b.value.includes('_'))
                    .map(b => {
                        const lbl = b.closest('label') || document.querySelector(`label[for="${b.id}"]`);
                        return {value: b.value, label: lbl ? lbl.textContent.trim() : b.value};
                    });
            }
        """)
        for item in items:
            options[item["label"]] = item["value"]
        if options:
            return options
    except Exception:
        pass

    # Stratégie 3 : lire les data-value ou data-key des list items
    try:
        items = await page.evaluate("""
            () => {
                const els = [...document.querySelectorAll('[data-value], [data-key]')];
                return els.map(el => ({
                    value: el.dataset.value || el.dataset.key,
                    label: el.textContent.trim()
                })).filter(x => x.value && x.value.includes('_'));
            }
        """)
        for item in items:
            options[item["label"]] = item["value"]
    except Exception:
        pass

    return options


# ── Ouverture du panel filtre ─────────────────────────────────────────────────

async def open_filter_panel(page: Page, filter_name: str) -> bool:
    """Clique sur le bouton de filtre et retourne True si le panel s'ouvre."""
    selectors = [
        f"button[aria-label*='{filter_name}']",
        f"[data-qa-id*='{filter_name.lower()}']",
        f"button:has-text('{filter_name}')",
        f"[aria-label='Ouvrir le filtre {filter_name}']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await wait(1.0, 1.8)
                return True
        except Exception:
            continue
    return False


async def close_filter_panel(page: Page):
    """Ferme le panel filtre ouvert."""
    try:
        close_btn = page.locator("[aria-label='Fermer'], button:has-text('Annuler')").first
        if await close_btn.count() > 0:
            await close_btn.click()
        else:
            await page.keyboard.press("Escape")
    except Exception:
        await page.keyboard.press("Escape")
    await wait(0.5, 0.8)


# ── Récupération modèles d'une marque ────────────────────────────────────────

async def get_models_for_brand(page: Page, brand: str) -> dict[str, str]:
    """Retourne {nom_modele: code_lbc} pour une marque donnée."""
    url = f"{SEARCH_BASE}&u_car_brand={brand}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await wait(2.0, 3.0)

    # Intercepter les réponses API pour récupérer les agrégations u_car_model
    models_from_api: dict[str, str] = {}

    async def on_response(response):
        if "finder/search" in response.url and response.status == 200:
            try:
                data = await response.json()
                aggs = data.get("aggregations", {}).get("u_car_model", {})
                for code in aggs.keys():
                    parts = code.split("_", 1)
                    label = parts[1] if len(parts) > 1 else code
                    models_from_api[label] = code
            except Exception:
                pass

    page.on("response", on_response)
    await wait(1.0, 2.0)
    page.remove_listener("response", on_response)

    if models_from_api:
        return models_from_api

    # Fallback : ouvrir le panel Modèle et lire les options
    opened = await open_filter_panel(page, "Modèle")
    if not opened:
        return {}
    await wait(1.0, 1.5)
    options = await read_panel_options(page)
    await close_filter_panel(page)
    return options


# ── Récupération finitions d'un modèle ───────────────────────────────────────

async def get_finitions_for_model(page: Page, brand: str, model_code: str) -> dict[str, str]:
    """Retourne {nom_finition: code_lbc} pour un modèle donné."""
    url = f"{SEARCH_BASE}&u_car_brand={brand}&u_car_model={model_code}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await wait(1.5, 2.5)

    finitions_from_api: dict[str, str] = {}

    async def on_response(response):
        if "finder/search" in response.url and response.status == 200:
            try:
                data = await response.json()
                aggs = data.get("aggregations", {}).get("u_car_finition", {})
                for code in aggs.keys():
                    parts = code.split("_", 2)
                    label = parts[2] if len(parts) > 2 else code
                    finitions_from_api[label] = code
            except Exception:
                pass

    page.on("response", on_response)
    await wait(1.0, 1.5)
    page.remove_listener("response", on_response)

    if finitions_from_api:
        return finitions_from_api

    # Fallback : ouvrir le panel Finition
    opened = await open_filter_panel(page, "Finition")
    if not opened:
        return {}
    await wait(1.0, 1.5)
    options = await read_panel_options(page)
    await close_filter_panel(page)
    return options


# ── Main ──────────────────────────────────────────────────────────────────────

async def run():
    all_brands = load_brands()
    catalog = load_catalog()

    # Ne garder que les marques présentes dans notre catalogue Caradisiac
    try:
        caradisiac_path = Path(__file__).parent.parent / "caradisiac_catalog.json"
        with open(caradisiac_path) as f:
            cara = json.load(f)
        our_brands = set(cara.keys())
        brands = [b for b in all_brands if b in our_brands or b.title() in our_brands]
    except Exception:
        brands = all_brands

    # Reprendre depuis l'état existant
    done = set(catalog.keys())
    brands = [b for b in brands if b not in done]
    print(f"Marques à traiter : {len(brands)} (déjà faites : {len(done)})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # Chargement initial — attendre 30s pour DataDome
        print("Chargement initial LeBonCoin (attente 30s DataDome)...")
        await page.goto(SEARCH_BASE, wait_until="domcontentloaded", timeout=40_000)
        await asyncio.sleep(30)
        print("Démarrage du scraping...\n")

        for i, brand in enumerate(brands):
            print(f"[{i+1}/{len(brands)}] {brand}...", end=" ", flush=True)
            try:
                models = await get_models_for_brand(page, brand)
                print(f"{len(models)} modèles")

                brand_catalog: dict = {}
                for model_label, model_code in models.items():
                    await wait(0.5, 1.0)
                    try:
                        finitions = await get_finitions_for_model(page, brand, model_code)
                        brand_catalog[model_label] = {
                            "code": model_code,
                            "finitions": finitions,
                        }
                        if finitions:
                            print(f"    └ {model_label} ({model_code}) : {len(finitions)} finitions")
                    except Exception as e:
                        brand_catalog[model_label] = {"code": model_code, "finitions": {}}
                        print(f"    └ {model_label} erreur finitions: {e}")

                catalog[brand] = brand_catalog
            except Exception as e:
                print(f"ERREUR: {e}")
                catalog[brand] = {}

            save_catalog(catalog)
            await wait(2.0, 4.0)

        await browser.close()

    total_models = sum(len(v) for v in catalog.values())
    print(f"\nTerminé : {len(catalog)} marques, {total_models} modèles")
    print(f"Sauvegardé : {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(run())
