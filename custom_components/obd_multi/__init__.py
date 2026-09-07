"""OBD Multi - WiFi / Bluetooth Classic / BLE ELM327 integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_BLE_ADDRESS,
    CONF_BLE_UUID_NOTIFY,
    CONF_BLE_UUID_WRITE,
    CONF_BT_ADDRESS,
    CONF_CUSTOM_PID_CSV,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    STANDARD_PIDS,
    TRANSPORT_BLE,
    TRANSPORT_BT_CLASSIC,
    TRANSPORT_WIFI,
)
from .coordinator import ObdCoordinator
from .elm327 import Elm327Client
from .pid_formula import PidDefinition, parse_custom_pid_csv
from .transport import BleTransport, BtClassicTransport, WifiTransport

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]

SERVICE_RELOAD_CUSTOM_PIDS = "reload_custom_pids"
SERVICE_RELOAD_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("csv"): cv.string,
    }
)


def _build_standard_pid_defs() -> list[PidDefinition]:
    return [
        PidDefinition(
            key=key,
            name=info["name"],
            mode=info["mode"],
            pid=info["pid"],
            n_bytes=info["bytes"],
            formula=info["formula"],
            unit=info["unit"],
            device_class=info["device_class"],
            state_class=info["state_class"],
        )
        for key, info in STANDARD_PIDS.items()
    ]


def _build_transport(hass: HomeAssistant, entry: ConfigEntry):
    data = entry.data
    transport_type = data[CONF_TRANSPORT]
    if transport_type == TRANSPORT_WIFI:
        return WifiTransport(data[CONF_HOST], data[CONF_PORT])
    if transport_type == TRANSPORT_BT_CLASSIC:
        return BtClassicTransport(data[CONF_BT_ADDRESS])
    if transport_type == TRANSPORT_BLE:
        ble_device = bluetooth.async_ble_device_from_address(
            hass, data[CONF_BLE_ADDRESS], connectable=True
        )
        if ble_device is None:
            raise RuntimeError(f"BLE device {data[CONF_BLE_ADDRESS]} not currently visible")
        return BleTransport(ble_device, data[CONF_BLE_UUID_WRITE], data[CONF_BLE_UUID_NOTIFY])
    raise RuntimeError(f"Unknown transport type: {transport_type}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    transport = _build_transport(hass, entry)
    client = Elm327Client(transport)
    await client.connect_and_init()

    pid_defs = _build_standard_pid_defs()
    csv_text = entry.data.get(CONF_CUSTOM_PID_CSV, "")
    if csv_text:
        pid_defs.extend(parse_custom_pid_csv(csv_text))

    coordinator = ObdCoordinator(
        hass, client, pid_defs, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_reload_custom_pids(call: ServiceCall) -> None:
        target_entry_id = call.data["entry_id"]
        stored = hass.data[DOMAIN].get(target_entry_id)
        if not stored:
            _LOGGER.error("No OBD Multi entry loaded with id %s", target_entry_id)
            return
        new_defs = _build_standard_pid_defs() + parse_custom_pid_csv(call.data["csv"])
        stored["coordinator"].set_pid_defs(new_defs)
        await stored["coordinator"].async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD_CUSTOM_PIDS, _handle_reload_custom_pids, schema=SERVICE_RELOAD_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        stored = hass.data[DOMAIN].pop(entry.entry_id, None)
        if stored:
            await stored["client"].close()
    return unloaded
