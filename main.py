import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()  # Charge .env en local ; les variables Railway ont priorité en prod

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

import os
from scrapers.leboncoin import LeboncoinScraper
from scrapers.lacentrale import LaCentraleScraper
from scrapers.autoscout24 import AutoScout24Scraper
from scrapers.paruvendu import ParuVenduScraper
from utils.calculator import calculate_estimation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="VM Auto Estimation API",
    description="API de rachat de véhicules d'occasion — VM Auto Business (Seine-et-Marne)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Remplace * par ton domaine Lovable en prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EstimationRequest(BaseModel):
    marque: str = Field(..., example="Peugeot")
    modele: str = Field(..., example="308")
    annee: int = Field(..., ge=1990, le=2025, example=2020)
    kilometrage: int = Field(..., ge=0, le=500000, example=80000)
    finition: Optional[str] = Field(None, example="S-Line")
    motorisation: Optional[str] = Field(None, example="1.2 PureTech 130")
    boite: Optional[str] = Field(None, example="mecanique")
    carburant: Optional[str] = Field(None, example="diesel")
    type_vehicule: Optional[str] = Field(None, example="utilitaire")  # "voiture" ou "utilitaire"


@app.get("/")
async def root():
    return {"status": "ok", "service": "VM Auto Estimation API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/estimation")
async def estimation(req: EstimationRequest):
    logger.info(f"Demande reçue : {req.marque} {req.modele} {req.annee} {req.kilometrage} km")

    scrapers = [
        LeboncoinScraper(),
        LaCentraleScraper(),
        AutoScout24Scraper(),
        # ParuVenduScraper(),  # désactivé : filtres km ignorés → prix incorrects
    ]

    tasks = [
        s.get_prices(req.marque, req.modele, req.annee, req.kilometrage,
                     finition=req.finition, carburant=req.carburant,
                     boite=req.boite, motorisation=req.motorisation,
                     type_vehicule=req.type_vehicule)
        for s in scrapers
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_prices: list[int] = []
    sources_detail: dict = {}

    for scraper, result in zip(scrapers, results):
        if isinstance(result, Exception):
            logger.error(f"[{scraper.name}] Erreur : {result}")
            sources_detail[scraper.name] = {"annonces": 0, "erreur": str(result)}
        else:
            logger.info(f"[{scraper.name}] {len(result)} prix récupérés")
            sources_detail[scraper.name] = {"annonces": len(result)}
            all_prices.extend(result)

    if not all_prices:
        raise HTTPException(
            status_code=404,
            detail="Aucune annonce trouvée pour ce véhicule. Vérifiez la marque et le modèle.",
        )

    calc = calculate_estimation(all_prices, req.marque, req.modele, req.motorisation, req.finition, req.boite)

    return {
        "vehicule": {
            "marque": req.marque.upper(),
            "modele": req.modele.upper(),
            "annee": req.annee,
            "kilometrage": req.kilometrage,
            "finition": req.finition or None,
            "motorisation": req.motorisation or None,
            "boite": req.boite or None,
            "carburant": req.carburant or None,
            "type_vehicule": req.type_vehicule or None,
        },
        "marche": {
            "nb_annonces": calc["nb_annonces"],
            "prix_moyen": calc["prix_moyen"],
            "prix_median": calc["prix_median"],
            "fourchette_basse": calc["fourchette_basse"],
            "fourchette_haute": calc["fourchette_haute"],
        },
        "estimation_rachat": {
            "prix_suggere": calc["prix_rachat"],
            "methode": calc["methode"],
        },
        "sources": sources_detail,
    }
