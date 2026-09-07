"""Small persistent settings store, independent of any config entry.

Used for the "auto-accept discovered devices" toggle - this needs to exist
and be readable even before the user has added their first vehicle, so it
can't live on a config entry the way per-vehicle settings do.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}_settings"


def _get_store(hass: HomeAssistant) -> Store:
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_get_auto_accept(hass: HomeAssistant) -> bool:
    data = await _get_store(hass).async_load()
    return bool(data and data.get("auto_accept_discovered"))


async def async_set_auto_accept(hass: HomeAssistant, value: bool) -> None:
    await _get_store(hass).async_save({"auto_accept_discovered": value})
