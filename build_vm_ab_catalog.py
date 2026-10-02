"""Construit le catalogue VM Auto Business (vm_ab_catalog.json) à partir des finitions/versions récupérées.

Entrée : lbc_finitions_v2.json  { "MARQUE_Modèle": { finitions, versions: [{label, min_year, max_year, fuel, count}], total_ads } }
Sortie : vm_ab_catalog.json      { "Marque": { "Modèle": [{ "v": label, "de": année, "a": année, "c": carburant, "n": annonces }] } }

Les marques sont renommées proprement (MERCEDES-BENZ → Mercedes, MG/MG MOTOR → MG…) ;
les modèles sans aucune version sont ignorés.
"""

import json
import os
import re

HERE = os.path.dirname(__file__)

BRAND_NAMES = {
    "ABARTH": "Abarth", "AIWAYS": "Aiways", "ALFA ROMEO": "Alfa Romeo", "ALPINE": "Alpine", "AUDI": "Audi",
    "BMW": "BMW", "BYD": "BYD", "CITROEN": "Citroën", "CUPRA": "Cupra", "DACIA": "Dacia", "DS": "DS",
    "FIAT": "Fiat", "FORD": "Ford", "HONDA": "Honda", "HYUNDAI": "Hyundai", "JAGUAR": "Jaguar", "JEEP": "Jeep",
    "KIA": "Kia", "LAND-ROVER": "Land Rover", "LEAPMOTOR": "Leapmotor", "LEXUS": "Lexus", "LYNK&CO": "Lynk & Co",
    "MAZDA": "Mazda", "MERCEDES-BENZ": "Mercedes", "MG/MG MOTOR": "MG", "MINI": "Mini", "MITSUBISHI": "Mitsubishi",
    "NISSAN": "Nissan", "OPEL": "Opel", "PEUGEOT": "Peugeot", "POLESTAR": "Polestar", "PORSCHE": "Porsche",
    "RENAULT": "Renault", "SEAT": "Seat", "SKODA": "Skoda", "SMART": "Smart", "SUBARU": "Subaru", "SUZUKI": "Suzuki",
    "TESLA": "Tesla", "TOYOTA": "Toyota", "VOLKSWAGEN": "Volkswagen", "VOLVO": "Volvo",
}


# Le carburant noté dans les données est parfois faux (« Polo 1.0 TSI » classée diesel) :
# quand le nom de la version l'indique clairement, c'est lui qui fait foi
_FUEL_PATTERNS = [
    ("electrique", r"\bkwh\b|\b(e-tron|electric|électrique|ev)\b|\bbev\b"),
    ("hybride", r"hybrid|hybride|\bphev\b|\bhev\b|\bmhev\b|e-tech|\b\d{3} ?e\b|\bplug-in\b|\be-hdi\b"),
    ("diesel", r"\b(tdi|hdi|bluehdi|dci|cdi|crdi|d4d|d-4d|jtd|jtdm|multijet|tdci|ecoblue|cdti|dtec|i-dtec|skyactiv-d|d)\b|\b\d{2,3}d\b|\bd\d\b|\bsdv\d|\btd\d|bluetec|\bdiesel\b"),
    ("essence", r"\b(tsi|tfsi|fsi|tce|puretech|vti|thp|ecoboost|mpi|gdi|t-gdi|tgdi|vvt-i|skyactiv-g|essence|turbo)\b|\b\d{2,3}i\b"),
]


def infer_fuel(label: str, fuel: str) -> str:
    low = label.lower()
    for name, pattern in _FUEL_PATTERNS:
        if re.search(pattern, low):
            return name
    return fuel


def main():
    raw = json.load(open(os.path.join(HERE, "lbc_finitions_v2.json")))
    out: dict[str, dict[str, list]] = {}
    for key, data in raw.items():
        brand_raw, _, model = key.partition("_")
        versions = [
            {"v": v["label"], "de": v["min_year"], "a": v["max_year"], "c": infer_fuel(v["label"], v["fuel"]), "n": v.get("count", 0)}
            for v in data.get("versions", [])
            if v.get("label") and v.get("min_year")
        ]
        if not versions or model == "Autre":
            continue
        brand = BRAND_NAMES.get(brand_raw, brand_raw.title())
        out.setdefault(brand, {})[model] = sorted(versions, key=lambda x: -x["n"])
    path = os.path.join(HERE, "vm_ab_catalog.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    n_models = sum(len(m) for m in out.values())
    n_versions = sum(len(v) for m in out.values() for v in m.values())
    print(f"Catalogue VM Auto Business : {len(out)} marques, {n_models} modèles, {n_versions} versions → {path}")


if __name__ == "__main__":
    main()
