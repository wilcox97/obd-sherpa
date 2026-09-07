"""Decode a VIN to Make/Model/Year using NHTSA's free public vPIC API.

No API key required. Endpoint and response shape are NHTSA's well-established
public vPIC decoder; this module fails soft (returns None) on anything
unexpected rather than raising, since VIN decoding is a "nice to have" that
should never block getting standard PIDs working.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvalues/{vin}?format=json"


@dataclass
class DecodedVehicle:
    make: str
    model: str
    year: int | None


async def decode_vin(session: aiohttp.ClientSession, vin: str) -> DecodedVehicle | None:
    if not vin or len(vin) < 11:
        return None
    url = NHTSA_URL.format(vin=vin)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                _LOGGER.debug("NHTSA VIN decode HTTP %s for %s", resp.status, vin)
                return None
            data = await resp.json()
    except (aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("NHTSA VIN decode failed for %s: %s", vin, err)
        return None

    results = data.get("Results") or []
    if not results:
        return None
    result = results[0]

    make = (result.get("Make") or "").strip()
    model = (result.get("Model") or "").strip()
    if not make or not model:
        return None

    year_str = (result.get("ModelYear") or "").strip()
    year = int(year_str) if year_str.isdigit() else None

    return DecodedVehicle(make=make, model=model, year=year)
