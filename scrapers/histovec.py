import asyncio
import base64
import logging
from playwright.async_api import async_playwright, Page
from playwright_stealth import stealth_async

logger = logging.getLogger(__name__)

HISTOVEC_URL = "https://histovec.interieur.gouv.fr/histovec/"


async def get_histovec_pdf(nom: str, prenom: str, formule: str, immatriculation: str) -> bytes | None:
    """Ouvre Histovec avec Playwright, remplit le formulaire et retourne le PDF en bytes."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="fr-FR",
        )
        page = await context.new_page()
        await stealth_async(page)

        try:
            logger.info(f"[histovec] Ouverture de {HISTOVEC_URL}")
            await page.goto(HISTOVEC_URL, wait_until="domcontentloaded", timeout=30000)

            # Attendre que le JS Vue.js charge le contenu
            await asyncio.sleep(4)

            # Debug : lister tous les inputs visibles
            inputs = await page.locator("input:visible").all()
            logger.info(f"[histovec] {len(inputs)} input(s) visible(s) trouvé(s)")
            for i, inp in enumerate(inputs):
                attrs = await inp.evaluate(
                    "(el) => ({ id: el.id, name: el.name, placeholder: el.placeholder, type: el.type, "
                    "ariaLabel: el.getAttribute('aria-label'), label: el.getAttribute('label') })"
                )
                logger.info(f"[histovec] input[{i}]: {attrs}")

            # Chercher et cliquer sur un bouton "Propriétaire" ou "Accéder" si présent
            for btn_text in ["propriétaire", "Propriétaire", "Accéder", "accéder", "Connexion"]:
                btn = page.get_by_text(btn_text, exact=False).first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        await asyncio.sleep(2)
                        logger.info(f"[histovec] Cliqué sur '{btn_text}'")
                        break
                    except Exception:
                        pass

            # Attendre que les champs soient visibles
            await asyncio.sleep(2)

            # Normaliser l'immatriculation
            immat_clean = immatriculation.replace("-", "").replace(" ", "").upper()
            formule_clean = formule.replace(" ", "").upper()

            # Remplir — stratégie 1 : par label
            filled = await _fill_by_label(page, ["immatriculation", "immat", "plaque", "SIV"], immat_clean)
            if not filled:
                await _fill_by_position(page, 0, immat_clean)

            await asyncio.sleep(0.3)

            filled = await _fill_by_label(page, ["formule", "numéro de formule", "numero"], formule_clean)
            if not filled:
                await _fill_by_position(page, 1, formule_clean)

            await asyncio.sleep(0.3)

            filled = await _fill_by_label(page, ["nom", "titulaire"], nom.upper())
            if not filled:
                await _fill_by_position(page, 2, nom.upper())

            await asyncio.sleep(0.3)

            if prenom:
                filled = await _fill_by_label(page, ["prénom", "prenom"], prenom)
                if not filled:
                    await _fill_by_position(page, 3, prenom)

            # Soumettre
            logger.info("[histovec] Soumission du formulaire...")
            submitted = await _submit(page)
            if not submitted:
                logger.warning("[histovec] Bouton submit non trouvé, tentative Enter")
                await page.keyboard.press("Enter")

            await asyncio.sleep(6)

            # Attendre que la page se stabilise après soumission
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass

            # Attendre encore un peu pour le rendu Vue.js des résultats
            await asyncio.sleep(4)

            # Vérifier erreur
            page_text = (await page.inner_text("body")).lower()
            logger.info(f"[histovec] Texte page ({len(page_text)} chars): {page_text[:500]}")
            error_keywords = ["aucun résultat", "aucune donnée", "non trouvé", "incorrect", "invalide", "erreur"]
            for kw in error_keywords:
                if kw in page_text:
                    logger.warning(f"[histovec] Erreur page: '{kw}' trouvé")
                    return None

            # Forcer le mode screen (les SPAs Vue.js masquent souvent le contenu en print CSS)
            await page.emulate_media(media="screen")

            # Screenshot de debug pour voir l'état de la page
            try:
                screenshot = await page.screenshot(full_page=True)
                logger.info(f"[histovec] Screenshot debug: {len(screenshot)} bytes")
            except Exception:
                pass

            logger.info("[histovec] Génération du PDF...")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=False,
                margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
            )
            logger.info(f"[histovec] PDF généré ({len(pdf_bytes)} bytes)")
            return pdf_bytes

        except Exception as e:
            logger.error(f"[histovec] Erreur Playwright: {e}", exc_info=True)
            return None
        finally:
            await browser.close()


async def _fill_by_label(page: Page, keywords: list[str], value: str) -> bool:
    """Tente de remplir un champ par label, placeholder, id ou aria-label."""
    for kw in keywords:
        strategies = [
            lambda k=kw: page.get_by_label(k, exact=False),
            lambda k=kw: page.get_by_placeholder(k, exact=False),
            lambda k=kw: page.locator(f'input[id*="{k}"]'),
            lambda k=kw: page.locator(f'input[name*="{k}"]'),
            lambda k=kw: page.locator(f'input[aria-label*="{k}"]'),
        ]
        for strategy in strategies:
            try:
                loc = strategy()
                if await loc.count() > 0:
                    await loc.first.fill(value)
                    logger.info(f"[histovec] Champ '{kw}' rempli avec '{value}'")
                    return True
            except Exception:
                continue
    return False


async def _fill_by_position(page: Page, index: int, value: str) -> bool:
    """Remplit le Nème input visible comme fallback."""
    try:
        inputs = page.locator("input:visible")
        count = await inputs.count()
        if index < count:
            await inputs.nth(index).fill(value)
            logger.info(f"[histovec] Fallback: input[{index}] rempli avec '{value}'")
            return True
    except Exception as e:
        logger.warning(f"[histovec] Fallback input[{index}] échoué: {e}")
    return False


async def _submit(page: Page) -> bool:
    """Tente de soumettre le formulaire."""
    selectors = [
        'button[type="submit"]',
        'button:has-text("Accéder")',
        'button:has-text("Consulter")',
        'button:has-text("Valider")',
        'button:has-text("Rechercher")',
        'input[type="submit"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                logger.info(f"[histovec] Submit via '{sel}'")
                return True
        except Exception:
            continue
    return False
