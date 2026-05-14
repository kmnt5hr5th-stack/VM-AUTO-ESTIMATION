"""Test rapide de l'API mobile LeBonCoin depuis la machine locale."""
import asyncio, uuid, random
from curl_cffi.requests import AsyncSession

API_URL = "https://api.leboncoin.fr/finder/search"
HOMEPAGE = "https://www.leboncoin.fr/"

async def test():
    ua = f"LBC;iOS;26.2;iPhone;phone;{str(uuid.uuid4()).upper()};wifi;101.45.0"
    headers = {
        "User-Agent": ua,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    payload = {
        "filters": {
            "category": {"id": "2"},
            "enums": {"ad_type": ["offer"]},
            "keywords": {"text": "Peugeot 308"},
            "ranges": {
                "regdate": {"min": 2019, "max": 2021},
                "mileage": {"min": 40000, "max": 80000},
            },
        },
        "limit": 35,
        "limit_alu": 3,
        "offset": 0,
        "disable_total": True,
        "extend": True,
        "listing_source": "direct-search",
    }

    async with AsyncSession(impersonate="safari_ios") as s:
        print("→ GET homepage pour cookies...")
        r1 = await s.get(HOMEPAGE, headers=headers, timeout=15)
        print(f"  Homepage: {r1.status_code}")

        print("→ POST API recherche...")
        r2 = await s.post(API_URL, json=payload, headers=headers, timeout=30)
        print(f"  API status: {r2.status_code}")

        if r2.status_code == 200:
            data = r2.json()
            ads = data.get("ads", [])
            print(f"  Annonces: {len(ads)}")
            prix = []
            for ad in ads[:5]:
                p = ad.get("price", [])
                if isinstance(p, list) and p:
                    prix.append(p[0])
            print(f"  Premiers prix: {prix}")
        elif r2.status_code == 403:
            print("  ❌ DataDome bloque cette IP (403)")
        else:
            print(f"  ❌ Erreur: {r2.text[:200]}")

asyncio.run(test())
