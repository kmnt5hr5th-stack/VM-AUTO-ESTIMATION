import asyncio
import base64
import json
import logging
import re
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

logger = logging.getLogger(__name__)

HISTOVEC_URL = "https://histovec.interieur.gouv.fr/histovec/"


async def ocr_carte_grise(photo_url: str, api_key: str) -> dict:
    """Appelle Claude API pour extraire nom, prenom, formule depuis la photo de carte grise."""
    async with httpx.AsyncClient(timeout=40) as client:
        img_resp = await client.get(photo_url)
        img_resp.raise_for_status()
        image_b64 = base64.standard_b64encode(img_resp.content).decode()
        content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0]

        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": content_type, "data": image_b64},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Sur cette carte grise française, extrais exactement :\n"
                                "1. Numéro de formule : code en bas à droite du document (format AAAA XX NNNNN, ex: 2013FK00001)\n"
                                "2. Nom du premier titulaire (champ C.4.1)\n"
                                "3. Prénom du premier titulaire (champ C.4.2)\n\n"
                                "Réponds UNIQUEMENT en JSON: "
                                '{"formule": "...", "nom": "...", "prenom": "..."}\n'
                                "Si un champ n'est pas lisible, mets null."
                            ),
                        },
                    ],
                }],
            },
        )
        response.raise_for_status()
        text = response.json()["content"][0]["text"].strip()
        match = re.search(r'\{[^}]+\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"formule": None, "nom": None, "prenom": None}


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
            logger.info("Ouverture de Histovec...")
            await page.goto(HISTOVEC_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Normaliser l'immatriculation (retirer tirets/espaces si besoin)
            immat_clean = immatriculation.replace("-", "").replace(" ", "").upper()

            # Remplir le formulaire — plusieurs stratégies de sélecteurs
            await _fill_field(page, ["immat", "immatriculation", "plaque"], immat_clean)
            await asyncio.sleep(0.5)
            await _fill_field(page, ["formule", "numero_formule", "num_formule"], formule.replace(" ", "").upper())
            await asyncio.sleep(0.5)
            await _fill_field(page, ["nom"], nom.upper())
            await asyncio.sleep(0.5)
            if prenom:
                await _fill_field(page, ["prenom"], prenom)
                await asyncio.sleep(0.5)

            logger.info("Soumission du formulaire Histovec...")
            await _submit_form(page)
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle", timeout=20000)

            # Vérifier qu'on a bien un résultat (pas une erreur)
            page_text = await page.inner_text("body")
            if "aucun résultat" in page_text.lower() or "aucune donnée" in page_text.lower():
                logger.warning("Histovec : aucun résultat trouvé pour ce véhicule")
                return None

            logger.info("Génération du PDF depuis la page résultat...")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
            )
            return pdf_bytes

        except Exception as e:
            logger.error(f"Erreur Playwright Histovec: {e}")
            return None
        finally:
            await browser.close()


async def _fill_field(page, keywords: list[str], value: str):
    """Tente de remplir un champ en cherchant par id, name, placeholder ou aria-label."""
    for kw in keywords:
        selectors = [
            f'input[id*="{kw}"]',
            f'input[name*="{kw}"]',
            f'input[placeholder*="{kw}"]',
            f'input[aria-label*="{kw}"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill(value)
                    logger.info(f"Champ '{kw}' rempli avec '{value}' via '{sel}'")
                    return
            except Exception:
                continue
    logger.warning(f"Champ non trouvé pour keywords={keywords}")


async def _submit_form(page):
    """Soumet le formulaire Histovec."""
    selectors = [
        'button[type="submit"]',
        'button:has-text("Accéder")',
        'button:has-text("Consulter")',
        'button:has-text("Valider")',
        'input[type="submit"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                return
        except Exception:
            continue
    # Fallback : Enter sur le dernier champ rempli
    await page.keyboard.press("Enter")
