import asyncio
import base64
import hashlib
import logging
import re
import unicodedata
import uuid as uuid_lib
from datetime import datetime, timedelta
from urllib.parse import quote

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

HISTOVEC_URL = "https://histovec.interieur.gouv.fr/histovec/"
HISTOVEC_PUBLIC_API = "https://histovec.interieur.gouv.fr/public/v1"
_HISTOVEC_LOGIN = "histovec_frontend"
_HISTOVEC_PWD = "rpupxm1e8PN7GnQKav"


def _format_immat_siv(immat: str) -> str:
    """Formate l'immatriculation en format SIV (AA-123-BB)."""
    clean = immat.upper().replace(" ", "").replace("-", "")
    import re
    m = re.match(r'^([A-Z]{2})(\d{3})([A-Z]{2})$', clean)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return clean  # retourner tel quel si format non reconnu


async def get_histovec_pdf(nom: str, prenom: str, formule: str, immatriculation: str) -> bytes | None:
    """Retourne un screenshot PNG du rapport Histovec.

    Utilise l'API JSON directe /public/v1/report_by_data (bypass Cloudflare).
    Fallback : Playwright si l'API échoue.
    """
    immat_siv = _format_immat_siv(immatriculation)
    formule_clean = formule.upper().replace(" ", "")

    # prenom vide : l'API exige min 1 char
    prenom_api = prenom.strip() if prenom and prenom.strip() else " "

    # ── Stratégie 1 : API directe + téléchargement CSA officiel ──────────
    logger.info(f"[histovec] API directe — immat={immat_siv}")
    try:
        result = await _call_api_and_get_csa(nom, prenom_api, formule_clean, immat_siv)
        if result is not None:
            return result
        else:
            logger.warning("[histovec] Véhicule non trouvé ou données incorrectes")
            return None
    except Exception as e:
        logger.warning(f"[histovec] API directe échouée ({e}) — fallback Playwright")

    # ── Stratégie 2 : Playwright ──────────────────────────────────────────
    return await _get_via_playwright(nom, prenom_api, formule_clean, immat_siv)


# ─── API directe ─────────────────────────────────────────────────────────────

async def _get_jwt() -> str | None:
    """Récupère le JWT token depuis l'API Histovec."""
    async with AsyncSession(impersonate="chrome120") as s:
        r = await s.post(
            f"{HISTOVEC_PUBLIC_API}/get_token",
            json={"login": _HISTOVEC_LOGIN, "password": _HISTOVEC_PWD},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
    if r.status_code == 200:
        token = r.json().get("access_token")
        logger.info(f"[histovec-api] JWT obtenu: {token[:30]}...")
        return token
    logger.warning(f"[histovec-api] get_token échoué: {r.status_code}")
    return None


def _compute_holder_id(nom: str, prenom: str, immat: str, formule: str) -> str:
    """Calcule le holderId Histovec (SHA-256 base64) comme le fait le frontend Vue.js."""
    current_month = (datetime.now() - timedelta(days=7)).strftime("%Y%m")
    raw_id = nom.upper() + prenom.upper() + immat + formule + current_month
    # normalizeIdvAsDataPreparation: NFD → remove accents → lowercase → keep only a-z0-9
    no_accent = unicodedata.normalize("NFD", raw_id[:510])
    no_accent = "".join(c for c in no_accent if unicodedata.category(c) != "Mn")
    normalized = re.sub(r"[^0-9a-z]", "", no_accent.lower())
    return base64.b64encode(hashlib.sha256(normalized.encode()).digest()).decode()


async def _call_api_and_get_csa(nom: str, prenom: str, formule: str, immatriculation: str) -> bytes | None:
    """Appel API Histovec + téléchargement du CSA officiel (PDF).

    Flow :
    1. GET JWT via /get_token
    2. POST /report_by_data/{userId} → vérifie les données
    3. GET /get_csa/{userId}/{holderId} → télécharge le PDF officiel
    """
    token = await _get_jwt()
    if not token:
        raise Exception("Impossible d'obtenir le JWT Histovec")

    user_id = str(uuid_lib.uuid4())
    holder_id = _compute_holder_id(nom, prenom, immatriculation, formule)

    payload = {
        "nom": nom.upper(),
        "prenom": prenom,
        "numeroFormule": formule,
        "immat": immatriculation,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://histovec.interieur.gouv.fr",
        "Referer": "https://histovec.interieur.gouv.fr/histovec/",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    async with AsyncSession(impersonate="chrome120") as s:
        logger.info(f"[histovec-api] POST report_by_data — userId={user_id} holderId={holder_id[:20]}…")
        r = await s.post(
            f"{HISTOVEC_PUBLIC_API}/report_by_data/{user_id}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        logger.info(f"[histovec-api] report_by_data → HTTP {r.status_code}")
        logger.info(f"[histovec-api] report_by_data body[:500] = {r.text[:500]}")

        if r.status_code == 404:
            logger.warning(f"[histovec-api] Véhicule non trouvé: {r.text[:300]}")
            return None
        if r.status_code != 200:
            logger.warning(f"[histovec-api] Erreur {r.status_code}: {r.text[:300]}")
            raise Exception(f"HTTP {r.status_code}")

        # Vérifier que la réponse contient des données véhicule réelles
        # Structure : { "hubimmat": { "vehicule": {...}, "clefAcheteur": "uuid", ... } }
        try:
            resp_json = r.json()
        except Exception:
            resp_json = {}
        hubimmat = resp_json.get("hubimmat") or {}
        vehicule_data = hubimmat.get("vehicule") or {}
        clef_acheteur = hubimmat.get("clefAcheteur")

        if not vehicule_data:
            logger.warning("[histovec-api] report_by_data: aucune donnée véhicule → données incorrectes ou véhicule absent")
            return None

        if not clef_acheteur:
            logger.warning("[histovec-api] Pas de clefAcheteur dans la réponse — fallback HTML")
            return await _render_html_report(resp_json, nom, immatriculation)

        # Télécharger le rapport complet (immat visible) via /get_printable
        logger.info(f"[histovec-api] GET get_printable/{user_id}/{clef_acheteur}")
        r_rapport = await s.get(
            f"{HISTOVEC_PUBLIC_API}/get_printable/{user_id}/{clef_acheteur}",
            headers={**headers, "Accept": "application/pdf,*/*"},
            timeout=30,
        )
        logger.info(
            f"[histovec-api] get_printable → HTTP {r_rapport.status_code} "
            f"ct={r_rapport.headers.get('content-type', '?')} "
            f"size={len(r_rapport.content)} "
            f"first4={r_rapport.content[:4]!r}"
        )

        if r_rapport.status_code == 200 and r_rapport.content[:4] == b"%PDF":
            logger.info("[histovec-api] Rapport PDF complet obtenu !")
            return r_rapport.content

        logger.warning(
            f"[histovec-api] get_printable pas un PDF → body[:300]={r_rapport.content[:300]!r}"
        )
        logger.warning(f"[histovec-api] Rapport non disponible ({r_rapport.status_code}) — fallback HTML")
        return await _render_html_report(resp_json, nom, immatriculation)


# ─── Rendu HTML ──────────────────────────────────────────────────────────────

def _get(d, *keys, default="-"):
    """Navigue dans un dict imbriqué, retourne default si absent."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d if d not in ("", {}, []) else default


async def _render_html_report(report: dict, nom: str, immatriculation: str) -> bytes | None:
    """Génère un rapport HTML à partir des données JSON et prend un screenshot."""
    vehicule = report.get("vehicule") or {}
    ct_data = report.get("controles_techniques") or {}
    ct_historique = ct_data.get("historique") or []
    historique_vehicule = vehicule.get("historique") or []
    sinistres = vehicule.get("sinistres") or []

    # Contrôles techniques
    ct_rows = ""
    for ct in ct_historique[:15]:
        date = ct.get("date", "-")
        result = ct.get("resultat_label") or ct.get("resultat", "-")
        km = ct.get("kilometre_declaratif")
        km_str = f"{km:,}".replace(",", " ") + " km" if isinstance(km, int) else "-"
        centre = _get(ct, "centre", "libelle")
        is_ok = "favorable" in str(result).lower() or "favorable" in str(ct.get("resultat", "")).lower()
        badge_class = "ok" if is_ok else "nok"
        ct_rows += f"""
        <tr>
          <td>{date}</td>
          <td><span class="badge {badge_class}">{result}</span></td>
          <td>{km_str}</td>
          <td>{centre}</td>
        </tr>"""

    if not ct_rows:
        ct_rows = "<tr><td colspan='4' class='empty'>Aucun historique de contrôle technique disponible</td></tr>"

    # Sinistres
    sinistre_block = ""
    if sinistres:
        sinistre_block = f"""
        <div class="alert-block">
          ⚠ Ce véhicule a <strong>{len(sinistres)} sinistre(s) déclaré(s)</strong> dans la base Histovec.
        </div>"""

    # Historique propriétaires
    histo_rows = ""
    for h in historique_vehicule[:10]:
        date = h.get("date", "-")
        type_op = h.get("type", "-")
        histo_rows += f"<tr><td>{date}</td><td>{type_op}</td></tr>"

    histo_section = ""
    if histo_rows:
        histo_section = f"""
        <div class="card">
          <h2>Historique du véhicule</h2>
          <table>
            <thead><tr><th>Date</th><th>Opération</th></tr></thead>
            <tbody>{histo_rows}</tbody>
          </table>
        </div>"""

    marque = _get(vehicule, "marque", "libelle")
    modele = _get(vehicule, "modele", "libelle")
    couleur = _get(vehicule, "couleur", "libelle")
    date_1ere = _get(vehicule, "date_premiere_immatriculation")
    vin = _get(vehicule, "vin")
    energie = _get(vehicule, "energie", "libelle")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Segoe UI", Arial, sans-serif; background: #f0f2f5; color: #1a1a2e; padding: 20px; }}
  .header {{ background: #003189; color: white; padding: 24px; border-radius: 10px; margin-bottom: 20px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }}
  .header .sub {{ font-size: 13px; opacity: 0.8; margin-top: 4px; }}
  .card {{ background: white; border-radius: 10px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .card h2 {{ font-size: 15px; font-weight: 700; color: #003189; border-bottom: 2px solid #e8eef8; padding-bottom: 10px; margin-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .field label {{ font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; display: block; margin-bottom: 3px; }}
  .field span {{ font-size: 14px; font-weight: 600; color: #1a1a2e; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f0f4ff; padding: 10px 8px; text-align: left; font-size: 12px; color: #555; font-weight: 600; }}
  td {{ padding: 9px 8px; border-bottom: 1px solid #f0f0f0; color: #333; }}
  td.empty {{ text-align: center; color: #999; font-style: italic; padding: 20px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 700; }}
  .badge.ok {{ background: #d4edda; color: #155724; }}
  .badge.nok {{ background: #f8d7da; color: #721c24; }}
  .alert-block {{ background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 6px; padding: 14px 16px; margin-bottom: 16px; color: #856404; font-size: 14px; }}
  .footer {{ text-align: center; font-size: 11px; color: #aaa; margin-top: 20px; padding: 10px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Rapport HistoVec — {immatriculation}</h1>
  <div class="sub">Ministère de l'Intérieur · Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}</div>
</div>

{sinistre_block}

<div class="card">
  <h2>Caractéristiques du véhicule</h2>
  <div class="grid">
    <div class="field"><label>Immatriculation</label><span>{immatriculation}</span></div>
    <div class="field"><label>Marque</label><span>{marque}</span></div>
    <div class="field"><label>Modèle</label><span>{modele}</span></div>
    <div class="field"><label>Couleur</label><span>{couleur}</span></div>
    <div class="field"><label>Date 1ère immat.</label><span>{date_1ere}</span></div>
    <div class="field"><label>Énergie</label><span>{energie}</span></div>
    <div class="field"><label>Titulaire</label><span>{nom.upper()}</span></div>
    <div class="field"><label>VIN</label><span style="font-size:12px;font-family:monospace">{vin}</span></div>
  </div>
</div>

<div class="card">
  <h2>Historique des Contrôles Techniques</h2>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Résultat</th>
        <th>Km déclaré</th>
        <th>Centre</th>
      </tr>
    </thead>
    <tbody>
      {ct_rows}
    </tbody>
  </table>
</div>

{histo_section}

<div class="footer">
  Source officielle : histovec.interieur.gouv.fr — Données Ministère de l'Intérieur
</div>
</body>
</html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page(viewport={"width": 900, "height": 1200})
        await page.set_content(html, wait_until="domcontentloaded")
        await asyncio.sleep(0.5)
        screenshot = await page.screenshot(full_page=True, type="png")
        await browser.close()

    logger.info(f"[histovec] Screenshot HTML: {len(screenshot)} bytes")
    return screenshot


# ─── Fallback Playwright ──────────────────────────────────────────────────────

async def _get_via_playwright(nom: str, prenom: str, formule: str, immatriculation: str) -> bytes | None:
    """Accès via navigateur Playwright (fallback si l'API directe est bloquée)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
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
            logger.info(f"[histovec-pw] Ouverture de {HISTOVEC_URL}")
            await page.goto(HISTOVEC_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)

            # Log des inputs visibles
            inputs = await page.locator("input:visible").all()
            logger.info(f"[histovec-pw] {len(inputs)} input(s)")
            for i, inp in enumerate(inputs):
                attrs = await inp.evaluate(
                    "(el) => ({ id: el.id, name: el.name, placeholder: el.placeholder, type: el.type })"
                )
                logger.info(f"[histovec-pw] input[{i}]: {attrs}")

            # Clic bouton Propriétaire si présent
            for btn_text in ["Propriétaire", "propriétaire", "Vendeur", "Accéder"]:
                btn = page.get_by_text(btn_text, exact=False).first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        await asyncio.sleep(2)
                        logger.info(f"[histovec-pw] Clic '{btn_text}'")
                        break
                    except Exception:
                        pass

            await asyncio.sleep(2)

            # Remplissage
            for keywords, value in [
                (["immatriculation", "immat", "plaque", "SIV", "numero_immatriculation"], immatriculation),
                (["formule", "numéro de formule", "numero_formule"], formule),
                (["nom", "titulaire"], nom.upper()),
                (["prénom", "prenom"], prenom),
            ]:
                filled = await _fill_by_label(page, keywords, value)
                if not filled:
                    idx = [["immatriculation", "immat", "plaque", "SIV", "numero_immatriculation"],
                           ["formule", "numéro de formule", "numero_formule"],
                           ["nom", "titulaire"],
                           ["prénom", "prenom"]].index(keywords)
                    await _fill_by_position(page, idx, value)
                await asyncio.sleep(0.3)

            # Soumission
            submitted = await _submit(page)
            if not submitted:
                await page.keyboard.press("Enter")

            await asyncio.sleep(8)

            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(3)

            page_text = (await page.inner_text("body")).lower()
            logger.info(f"[histovec-pw] Page text ({len(page_text)} chars): {page_text[:400]}")

            for kw in ["aucun résultat", "non trouvé", "incorrect", "invalide"]:
                if kw in page_text:
                    logger.warning(f"[histovec-pw] Erreur: '{kw}'")
                    return None

            screenshot = await page.screenshot(full_page=True, type="png")
            logger.info(f"[histovec-pw] Screenshot: {len(screenshot)} bytes")
            return screenshot

        except Exception as e:
            logger.error(f"[histovec-pw] Erreur: {e}", exc_info=True)
            return None
        finally:
            await browser.close()


async def _fill_by_label(page, keywords: list[str], value: str) -> bool:
    for kw in keywords:
        for strategy in [
            lambda k=kw: page.get_by_label(k, exact=False),
            lambda k=kw: page.get_by_placeholder(k, exact=False),
            lambda k=kw: page.locator(f'input[id*="{k}"]'),
            lambda k=kw: page.locator(f'input[name*="{k}"]'),
            lambda k=kw: page.locator(f'input[aria-label*="{k}"]'),
        ]:
            try:
                loc = strategy()
                if await loc.count() > 0:
                    await loc.first.fill(value)
                    logger.info(f"[histovec-pw] Champ '{kw}' = '{value}'")
                    return True
            except Exception:
                continue
    return False


async def _fill_by_position(page, index: int, value: str) -> bool:
    try:
        inputs = page.locator("input:visible")
        if index < await inputs.count():
            await inputs.nth(index).fill(value)
            logger.info(f"[histovec-pw] Fallback input[{index}] = '{value}'")
            return True
    except Exception as e:
        logger.warning(f"[histovec-pw] input[{index}] échoué: {e}")
    return False


async def _submit(page) -> bool:
    for sel in [
        'button[type="submit"]',
        'button:has-text("Accéder")',
        'button:has-text("Consulter")',
        'button:has-text("Valider")',
        'button:has-text("Rechercher")',
        'input[type="submit"]',
    ]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                logger.info(f"[histovec-pw] Submit via '{sel}'")
                return True
        except Exception:
            continue
    return False
