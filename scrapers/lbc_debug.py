"""Debug LBC API response to find correct headers and payload structure."""
import asyncio
import json
from curl_cffi.requests import AsyncSession

API_URL = "https://api.leboncoin.fr/finder/search"

PAYLOAD = {
    "filters": {
        "category": {"id": "2"},
        "enums": {"u_car_brand": ["RENAULT"]}
    },
    "limit": 1,
    "limit_alu": 1,
    "offset": 0,
    "pivot": "0,0,0",
}

async def run():
    async with AsyncSession(impersonate="chrome120") as session:
        # Sans api_key
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "fr-FR,fr;q=0.9",
            "content-type": "application/json",
            "origin": "https://www.leboncoin.fr",
            "referer": "https://www.leboncoin.fr/recherche?category=2",
        }
        resp = await session.post(API_URL, json=PAYLOAD, headers=headers, timeout=15)
        print(f"Status: {resp.status_code}")
        print(f"Headers response: {dict(resp.headers)}")
        try:
            data = resp.json()
            print(f"Keys: {list(data.keys())}")
            aggs = data.get("aggregations", {})
            print(f"Aggregation keys: {list(aggs.keys())}")
            models = aggs.get("u_car_model", {})
            print(f"Models RENAULT ({len(models)}): {list(models.keys())[:15]}")
        except Exception as e:
            print(f"Erreur JSON: {e}")
            print(f"Body (500 chars): {resp.text[:500]}")

asyncio.run(run())
