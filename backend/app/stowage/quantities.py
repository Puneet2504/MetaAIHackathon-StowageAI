"""Cargo quantities: tonnes, barrels and m³ to the volume a parcel takes up in the tanks. No AI here.

Uses every condition the order gives (temperature, density and the temperature it was measured at,
API gravity, barrels, tonnes in air or in vacuum). Anything missing is filled in and reported:
  - density at 15 °C: the order, else API gravity, else the grade library
  - cargo temperature: the order, else the load port's average sea temperature, else a product average
  - "mt" without a basis is taken as tonnes in air (the usual basis on product bills of lading)

Standards (refined products):
  - ASTM D1250 / API MPMS 11.1 Table 54B (1980 constants): volume correction factor to 15 °C
  - Table 53B: density at 15 °C from a density measured at another temperature
  - Table 56: weight in air = volume at 15 °C x (density at 15 °C - 0.0011)
Port and product temperatures are typical demo values; use the cargo documents for real plans.
"""
from __future__ import annotations

import difflib
import math
import re

BBL_M3 = 0.158987          # one barrel in m³ (at 60 °F, taken as 15 °C)
AIR_BUOYANCY = 0.0011      # t/m³ subtracted for weight in air (Table 56)

# Annual average sea surface temperature at the port, °C (typical values, rounded).
PORT_SEA_TEMP_C = {
    "rotterdam": 12, "europoort": 12, "amsterdam": 12, "ijmuiden": 12, "antwerp": 12, "antwerpen": 12, "ghent": 12,
    "vlissingen": 12, "flushing": 12, "dunkirk": 12, "dunkerque": 12, "le havre": 13, "rouen": 13, "fawley": 13,
    "southampton": 13, "thames": 12, "london": 12, "immingham": 11, "teesport": 10, "grangemouth": 10, "pembroke": 12,
    "milford haven": 12, "liverpool": 11, "stanlow": 11, "dublin": 11, "cork": 12, "whitegate": 12, "hamburg": 11,
    "brunsbuttel": 11, "wilhelmshaven": 11, "mongstad": 9, "bergen": 9, "slagen": 9, "oslo": 9, "brofjorden": 9,
    "lysekil": 9, "gothenburg": 9, "goteborg": 9, "kalundborg": 10, "fredericia": 10, "copenhagen": 10,
    "nynashamn": 8, "stockholm": 8, "gdansk": 9, "gdynia": 9, "klaipeda": 9, "ventspils": 8, "riga": 8,
    "tallinn": 7, "muuga": 7, "helsinki": 7, "porvoo": 7, "skoldvik": 7, "naantali": 8, "primorsk": 6,
    "ust-luga": 6, "st petersburg": 6, "saint-nazaire": 14, "donges": 14, "la rochelle": 15, "bordeaux": 15,
    "bilbao": 16, "santander": 16, "gijon": 15, "a coruna": 15, "la coruna": 15, "ferrol": 15, "porto": 16,
    "leixoes": 16, "lisbon": 17, "lisboa": 17, "sines": 17, "huelva": 19, "cadiz": 19, "algeciras": 18,
    "gibraltar": 18, "tanger med": 18, "tangier": 18, "cartagena": 19, "escombreras": 19, "valencia": 19,
    "castellon": 19, "tarragona": 18, "barcelona": 18, "lavera": 17, "fos": 17, "marseille": 17, "genoa": 18,
    "genova": 18, "la spezia": 18, "livorno": 18, "naples": 19, "napoli": 19, "milazzo": 20, "augusta": 20,
    "sarroch": 19, "cagliari": 19, "trieste": 16, "venice": 16, "venezia": 16, "ancona": 17, "marsaxlokk": 20,
    "malta": 20, "valletta": 20, "piraeus": 20, "athens": 20, "agioi theodoroi": 20, "thessaloniki": 18,
    "istanbul": 16, "haifa": 23, "ashdod": 23, "alexandria": 22, "port said": 23, "arzew": 19, "skikda": 20,
    "algiers": 19, "yanbu": 27, "jeddah": 28, "ras tanura": 27, "jubail": 27, "mina al ahmadi": 26, "kuwait": 26,
    "ruwais": 28, "jebel ali": 28, "dubai": 28, "fujairah": 28, "sohar": 28, "sikka": 27, "jamnagar": 27,
    "vadinar": 27, "mumbai": 28, "kochi": 29, "cochin": 29, "colombo": 28, "singapore": 29, "port dickson": 29,
    "new york": 14, "linden": 14, "bayonne": 14, "philadelphia": 14, "boston": 11, "halifax": 9,
    "port everglades": 26, "tampa": 26, "new orleans": 24, "houston": 25, "galveston": 25, "texas city": 25,
    "corpus christi": 25, "lake charles": 24, "las palmas": 20, "dakar": 24, "abidjan": 28, "lome": 28,
    "lagos": 28, "tema": 28,
}

# Typical loading temperature by grade when neither the order nor the port gives one, °C.
PRODUCT_TEMP_C = {"ULSD": 25, "JET": 20, "GASOLINE": 20, "NAPHTHA": 20}
DEFAULT_TEMP_C = 20


def _alpha15(rho15: float) -> float:
    """Thermal expansion coefficient at 15 °C (Table 54B, 1980 constants); rho15 in kg/m³."""
    if rho15 < 770.5:        # gasolines and naphthas
        return 346.4228 / rho15 ** 2 + 0.4388 / rho15
    if rho15 < 787.5:        # transition zone
        return -0.00336312 + 2680.3206 / rho15 ** 2
    if rho15 < 838.5:        # jet fuels and kerosene
        return 594.5418 / rho15 ** 2
    return 186.9696 / rho15 ** 2 + 0.4862 / rho15  # gas oils, diesel, fuel oils


def vcf(rho15_t_m3: float, temp_c: float) -> float:
    """Volume correction factor: volume at 15 °C = volume at temp_c x VCF (Table 54B)."""
    a = _alpha15(rho15_t_m3 * 1000)
    dt = temp_c - 15
    return math.exp(-a * dt * (1 + 0.8 * a * dt))


def rho15_from_observed(rho_t: float, temp_c: float) -> float:
    """Density at 15 °C from a density measured at temp_c (Table 53B), both in t/m³."""
    rho15 = rho_t
    for _ in range(20):  # the correction depends on rho15 itself, so iterate
        nxt = rho_t / vcf(rho15, temp_c)
        if abs(nxt - rho15) < 1e-7:
            break
        rho15 = nxt
    return rho15


def rho15_from_api(api: float) -> float:
    """Density at 15 °C in t/m³ from API gravity (60 °F taken as 15 °C)."""
    return 141.5 / (api + 131.5) * 0.999016


def _norm(name: str) -> str:
    s = name.lower()
    s = re.split(r"[,(/]", s)[0]
    s = re.sub(r"\b(port of|port|harbou?r|terminal|refinery)\b", " ", s)
    s = s.translate(str.maketrans("åäöøéèêüñç", "aaooeeeunc"))
    return re.sub(r"[^a-z0-9 -]+", " ", s).strip()


def port_temperature(port: str | None) -> tuple[float, str] | None:
    """Average sea temperature at a port and the port name it matched, or None if the port isn't known."""
    if not port:
        return None
    key = _norm(port)
    if key in PORT_SEA_TEMP_C:
        return PORT_SEA_TEMP_C[key], key.title()
    close = difflib.get_close_matches(key, list(PORT_SEA_TEMP_C), n=1, cutoff=0.85)
    return (PORT_SEA_TEMP_C[close[0]], close[0].title()) if close else None


def convert(parcel, grade_id: str, library_density: float) -> dict:
    """How much room a parcel takes in the tanks, from every condition the order gives.

    `parcel` is a schemas.Parcel; `library_density` is the grade's density at 15 °C (t/m³) when the order has none.
    Returns figures plus where each input came from, and the assumptions made.
    """
    assumed: list[str] = []

    # Density at 15 °C.
    if parcel.density_t_m3:
        d = parcel.density_t_m3 / 1000 if parcel.density_t_m3 > 2 else parcel.density_t_m3  # 845 kg/m³ -> 0.845
        ref = parcel.density_temp_c
        if ref is not None and abs(ref - 15) > 0.05:
            rho15, density_source = rho15_from_observed(d, ref), f"order ({d:.4f} at {ref:g} °C, corrected to 15 °C)"
        else:
            rho15, density_source = d, "order"
    elif parcel.api:
        rho15, density_source = rho15_from_api(parcel.api), f"order (API {parcel.api:g})"
    else:
        rho15, density_source = library_density, "typical for the grade"
        assumed.append(f"density {rho15:.3f} t/m³ at 15 °C is typical for the grade, not from the cargo documents")

    # Cargo temperature.
    if parcel.temp_c is not None:
        temp, temp_source = parcel.temp_c, "order"
    elif hit := port_temperature(parcel.load_port):
        temp, temp_source = hit[0], f"{hit[1]} average"
        assumed.append(f"cargo temperature {temp:g} °C is the average at {hit[1]}")
    else:
        temp = PRODUCT_TEMP_C.get(grade_id, DEFAULT_TEMP_C)
        temp_source = "typical for the grade"
        assumed.append(f"cargo temperature {temp:g} °C is typical for the grade")

    basis = parcel.mass_basis or "air"
    wcf = rho15 - AIR_BUOYANCY
    if parcel.quantity_m3:
        v15, ordered = parcel.quantity_m3, f"{parcel.quantity_m3:,.0f} m³"
        assumed.append("m³ in the order taken as volume at 15 °C")
    elif parcel.quantity_bbl:
        v15, ordered = parcel.quantity_bbl * BBL_M3, f"{parcel.quantity_bbl:,.0f} bbl"
    elif parcel.quantity_t:
        v15 = parcel.quantity_t / (wcf if basis == "air" else rho15)
        ordered = f"{parcel.quantity_t:,.0f} t in {basis}"
        if not parcel.mass_basis:
            assumed.append("tonnes taken as weight in air")
    else:
        return {}

    factor = vcf(rho15, temp)
    tov = v15 / factor  # what the cargo occupies at its own temperature
    tonnes = parcel.quantity_t if parcel.quantity_t else v15 * (wcf if basis == "air" else rho15)
    return {
        "ordered": ordered, "basis": basis,
        "temp_c": round(temp, 1), "temp_source": temp_source,
        "density15": round(rho15, 4), "density_source": density_source,
        "density_at_temp": round(rho15 * factor, 4), "vcf": round(factor, 5),
        "volume15_m3": round(v15, 1), "volume_m3": round(tov, 1),
        "tonnes": round(tonnes, 1), "tonnes_air": round(v15 * wcf, 1), "tonnes_vacuum": round(v15 * rho15, 1),
        "assumed": assumed,
    }
