"""Config flow for OBD Multi.

Pairing/discovery summary:
  - WiFi: no pairing, just host:port. We open a socket and send ATZ to verify.
  - BT Classic (SPP): device must already be OS-paired (Settings > Bluetooth,
    or `bluetoothctl pair <mac>`) before this flow can connect - we don't
    do the pairing handshake ourselves, only the RFCOMM connect + AT probe.
  - BLE: no pairing needed. We list nearby BLE advertisements HA's Bluetooth
    integration has already seen, you pick one, and we attempt to auto-probe
    its GATT services for a notify+write characteristic pair. If that fails
    (uncommon UUID scheme) you can enter the UUIDs manually.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BLE_ADDRESS,
    CONF_BLE_UUID_NOTIFY,
    CONF_BLE_UUID_WRITE,
    CONF_BT_ADDRESS,
    CONF_CUSTOM_PID_CSV,
    CONF_CUSTOM_PIN,
    CONF_HOST,
    CONF_OBDB_PIDS,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WIFI_PORT,
    DOMAIN,
    TRANSPORT_BLE,
    TRANSPORT_BT_CLASSIC,
    TRANSPORT_WIFI,
)
from .elm327 import Elm327Client
from .pid_formula import FormulaError, parse_custom_pid_csv
from .storage import async_get_auto_accept, async_set_auto_accept
from .transport import BleTransport, BtClassicTransport, ElmTransportError, WifiTransport

_LOGGER = logging.getLogger(__name__)

def _vin_last4(vin: str | None) -> str | None:
    return vin[-4:] if vin and len(vin) >= 4 else None


async def _vehicle_title_and_vin(client: Elm327Client, fallback: str) -> tuple[str, str | None]:
    """Interim friendly name (last-4-of-VIN if available) + raw VIN.

    This is a placeholder title only: if auto-detection later resolves a
    Year/Make/Model via NHTSA, _try_auto_detect_vehicle overwrites self._title
    with something like "2020 Chevrolet Bolt EV (...4567)" instead.
    """
    vin = await client.read_vin()
    last4 = _vin_last4(vin)
    title = f"OBD (...{last4})" if last4 else fallback
    return title, vin


# Common BLE GATT UUID pairs used by cheap ELM327 clones (Veepeak/HM-10/LeLink-style).
_KNOWN_BLE_UUID_PAIRS = [
    ("0000fff2-0000-1000-8000-00805f9b34fb", "0000fff1-0000-1000-8000-00805f9b34fb"),  # FFF0 service
    ("0000ffe1-0000-1000-8000-00805f9b34fb", "0000ffe1-0000-1000-8000-00805f9b34fb"),  # HM-10 style, single char
]


class ObdMultiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._ble_candidates: dict[str, Any] = {}
        self._pending_ble_address: str | None = None
        self._title: str = "OBD Sherpa"
        self._vin: str | None = None
        self._auto_detect_attempted: bool = False
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> FlowResult:
        """Auto-triggered by HA's Bluetooth integration when a matching device is seen
        (see manifest.json's `bluetooth` matchers). Handles BLE devices; also fired
        for classic-BT devices HA has otherwise learned about via the same stack."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, discovery_info.address, connectable=True
        )
        if ble_device is not None:
            for uuid_write, uuid_notify in _KNOWN_BLE_UUID_PAIRS:
                transport = BleTransport(ble_device, uuid_write, uuid_notify)
                try:
                    client = Elm327Client(transport)
                    await client.connect_and_init()
                    self._title, self._vin = await _vehicle_title_and_vin(client, discovery_info.name or discovery_info.address)
                    await client.close()
                except ElmTransportError:
                    continue
                self._data = {
                    CONF_TRANSPORT: TRANSPORT_BLE,
                    CONF_BLE_ADDRESS: discovery_info.address,
                    CONF_BLE_UUID_WRITE: uuid_write,
                    CONF_BLE_UUID_NOTIFY: uuid_notify,
                }
                return await self._complete_discovery()

        # Not connectable as BLE (or UUIDs unknown) - try it as classic SPP with
        # default-PIN auto-pairing before giving up on this discovery.
        paired = await BtClassicTransport.try_pair(discovery_info.address)
        if paired:
            transport = BtClassicTransport(discovery_info.address)
            try:
                client = Elm327Client(transport)
                await client.connect_and_init()
                self._title, self._vin = await _vehicle_title_and_vin(client, discovery_info.name or discovery_info.address)
                await client.close()
            except ElmTransportError:
                return self.async_abort(reason="cannot_connect")
            self._data = {CONF_TRANSPORT: TRANSPORT_BT_CLASSIC, CONF_BT_ADDRESS: discovery_info.address}
            return await self._complete_discovery()

        return self.async_abort(reason="cannot_connect")

    async def _complete_discovery(self) -> FlowResult:
        """After a successful discovery probe: either finish hands-off (if the
        auto-accept setting is on) or fall back to the normal confirm card."""
        if await async_get_auto_accept(self.hass):
            await self._try_auto_detect_vehicle()
            return await self.async_step_finish_setup()
        self.context["title_placeholders"] = {"name": self._title}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return await self.async_step_custom_pids()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._title},
        )

    async def async_step_user(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=[TRANSPORT_WIFI, TRANSPORT_BT_CLASSIC, TRANSPORT_BLE, "settings"],
        )

    async def async_step_settings(self, user_input=None) -> FlowResult:
        current = await async_get_auto_accept(self.hass)
        if user_input is not None:
            await async_set_auto_accept(self.hass, user_input["auto_accept_discovered"])
            return self.async_abort(reason="settings_saved")
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {vol.Required("auto_accept_discovered", default=current): bool}
            ),
        )

    # ---------------- WiFi ----------------

    async def async_step_wifi(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            transport = WifiTransport(user_input[CONF_HOST], user_input[CONF_PORT])
            try:
                client = Elm327Client(transport)
                await client.connect_and_init()
                self._title, self._vin = await _vehicle_title_and_vin(client, f"OBD (WiFi {user_input[CONF_HOST]})")
                await client.close()
            except ElmTransportError as err:
                _LOGGER.debug("WiFi probe failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                self._data = {
                    CONF_TRANSPORT: TRANSPORT_WIFI,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                }
                return await self.async_step_custom_pids()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_WIFI_PORT): int,
            }
        )
        return self.async_show_form(step_id="wifi", data_schema=schema, errors=errors)

    # ---------------- Bluetooth Classic ----------------

    async def async_step_bt_classic(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            custom_pin = user_input.get(CONF_CUSTOM_PIN, "").strip()
            paired = await BtClassicTransport.try_pair(
                user_input[CONF_BT_ADDRESS], custom_pins=[custom_pin] if custom_pin else None
            )
            if not paired:
                errors["base"] = "cannot_connect"
            else:
                transport = BtClassicTransport(user_input[CONF_BT_ADDRESS])
                try:
                    client = Elm327Client(transport)
                    await client.connect_and_init()
                    self._title, self._vin = await _vehicle_title_and_vin(client, f"OBD (BT {user_input[CONF_BT_ADDRESS]})")
                    await client.close()
                except ElmTransportError as err:
                    _LOGGER.debug("BT classic probe failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    self._data = {
                        CONF_TRANSPORT: TRANSPORT_BT_CLASSIC,
                        CONF_BT_ADDRESS: user_input[CONF_BT_ADDRESS],
                    }
                    return await self.async_step_custom_pids()

        schema = vol.Schema(
            {
                vol.Required(CONF_BT_ADDRESS): str,
                vol.Optional(CONF_CUSTOM_PIN, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="bt_classic",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "hint": "Enter the adapter's MAC address (e.g. AA:BB:CC:DD:EE:FF). "
                "We'll try connecting with no PIN first, then any custom PIN you "
                "enter below (e.g. an OBDLink MX/MX+ set to a non-default PIN via "
                "the OBDLink app), then the common defaults 1234/0000."
            },
        )

    # ---------------- BLE ----------------

    async def async_step_ble(self, user_input=None) -> FlowResult:
        self._ble_candidates = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
            if info.name and any(k in info.name.upper() for k in ("OBD", "ELM", "VEEPEAK", "VGATE", "ICAR"))
        }
        if not self._ble_candidates:
            return self.async_show_form(
                step_id="ble_manual",
                data_schema=vol.Schema({vol.Required(CONF_BLE_ADDRESS): str}),
                errors={"base": "no_devices_found"},
            )

        options = {addr: f"{info.name} ({addr})" for addr, info in self._ble_candidates.items()}
        schema = vol.Schema({vol.Required(CONF_BLE_ADDRESS): vol.In(options)})
        if user_input is not None:
            return await self._probe_and_finish_ble(user_input[CONF_BLE_ADDRESS])
        return self.async_show_form(step_id="ble", data_schema=schema)

    async def async_step_ble_manual(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return await self._probe_and_finish_ble(user_input[CONF_BLE_ADDRESS])
        return self.async_show_form(
            step_id="ble_manual", data_schema=vol.Schema({vol.Required(CONF_BLE_ADDRESS): str})
        )

    async def _probe_and_finish_ble(self, address: str) -> FlowResult:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return self.async_show_form(
                step_id="ble_manual",
                data_schema=vol.Schema({vol.Required(CONF_BLE_ADDRESS): str}),
                errors={"base": "device_not_found"},
            )

        for uuid_write, uuid_notify in _KNOWN_BLE_UUID_PAIRS:
            transport = BleTransport(ble_device, uuid_write, uuid_notify)
            try:
                client = Elm327Client(transport)
                await client.connect_and_init()
                self._title, self._vin = await _vehicle_title_and_vin(client, f"OBD (BLE {address})")
                await client.close()
            except ElmTransportError:
                continue
            self._data = {
                CONF_TRANSPORT: TRANSPORT_BLE,
                CONF_BLE_ADDRESS: address,
                CONF_BLE_UUID_WRITE: uuid_write,
                CONF_BLE_UUID_NOTIFY: uuid_notify,
            }
            return await self.async_step_custom_pids()

        # None of the known UUID pairs worked - ask for them manually.
        self._pending_ble_address = address
        return self.async_show_form(
            step_id="ble_uuids",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BLE_UUID_WRITE): str,
                    vol.Required(CONF_BLE_UUID_NOTIFY): str,
                }
            ),
            description_placeholders={"address": address},
            errors={"base": "cannot_connect"},
        )

    async def async_step_ble_uuids(self, user_input=None) -> FlowResult:
        address = self._pending_ble_address
        if user_input is not None and address:
            ble_device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
            transport = BleTransport(
                ble_device, user_input[CONF_BLE_UUID_WRITE], user_input[CONF_BLE_UUID_NOTIFY]
            )
            try:
                client = Elm327Client(transport)
                await client.connect_and_init()
                self._title, self._vin = await _vehicle_title_and_vin(client, f"OBD (BLE {address})")
                await client.close()
            except ElmTransportError:
                return self.async_show_form(
                    step_id="ble_uuids",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_BLE_UUID_WRITE): str,
                            vol.Required(CONF_BLE_UUID_NOTIFY): str,
                        }
                    ),
                    errors={"base": "cannot_connect"},
                )
            self._data = {
                CONF_TRANSPORT: TRANSPORT_BLE,
                CONF_BLE_ADDRESS: address,
                CONF_BLE_UUID_WRITE: user_input[CONF_BLE_UUID_WRITE],
                CONF_BLE_UUID_NOTIFY: user_input[CONF_BLE_UUID_NOTIFY],
            }
            return await self.async_step_custom_pids()
        return self.async_abort(reason="missing_address")

    # ---------------- Custom PIDs (optional) ----------------

    async def async_step_custom_pids(self, user_input=None) -> FlowResult:
        """Try VIN -> NHTSA -> OBDb auto-detection once, then show the CSV/OBDb/skip menu.

        Auto-detection only ever ADDS vehicle-specific PIDs on top of the
        standard set that's already active; if the VIN can't be decoded, or
        NHTSA's Make/Model doesn't match anything in OBDb, it silently falls
        back to standard PIDs only - same as if you'd chosen "skip" by hand.
        """
        if not self._auto_detect_attempted:
            self._auto_detect_attempted = True
            detected = await self._try_auto_detect_vehicle()
            if detected and not await async_get_auto_accept(self.hass):
                return self.async_show_form(
                    step_id="auto_vehicle_detected",
                    data_schema=vol.Schema({}),
                    description_placeholders=detected,
                )

        return self.async_show_menu(
            step_id="custom_pids",
            menu_options=["custom_pids_csv", "obdb_import", "finish_setup"],
        )

    async def _try_auto_detect_vehicle(self) -> dict[str, str] | None:
        if not self._vin:
            return None
        from . import obdb_importer, vin_decoder

        session = async_get_clientsession(self.hass)
        decoded = await vin_decoder.decode_vin(session, self._vin)
        if decoded is None:
            _LOGGER.debug("NHTSA could not decode VIN %s", self._vin)
            return None

        query = f"{decoded.make} {decoded.model}"
        try:
            matches = await obdb_importer.search_obdb_repo(session, query)
        except obdb_importer.ObdbError as err:
            _LOGGER.debug("OBDb search failed for %s: %s", query, err)
            return None
        if not matches:
            _LOGGER.debug("No OBDb repo match for %s", query)
            return None

        repo = matches[0].repo_name
        try:
            commands = await obdb_importer.fetch_signalset(session, repo, decoded.year)
            pid_defs = obdb_importer.parse_signalset_to_pid_defs(commands, repo)
        except obdb_importer.ObdbError as err:
            _LOGGER.debug("OBDb fetch/parse failed for %s: %s", repo, err)
            return None
        if not pid_defs:
            return None

        self._data[CONF_OBDB_PIDS] = json.dumps([p.to_dict() for p in pid_defs])
        year_str = str(decoded.year) if decoded.year else "unknown year"
        last4 = _vin_last4(self._vin)
        title_parts = [p for p in (year_str if decoded.year else None, decoded.make, decoded.model) if p]
        self._title = " ".join(title_parts) + (f" (...{last4})" if last4 else "")
        return {
            "make": decoded.make,
            "model": decoded.model,
            "year": year_str,
            "repo": repo,
            "count": str(len(pid_defs)),
        }

    async def async_step_auto_vehicle_detected(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_show_menu(
                step_id="custom_pids",
                menu_options=["custom_pids_csv", "obdb_import", "finish_setup"],
            )
        return self.async_show_form(step_id="auto_vehicle_detected", data_schema=vol.Schema({}))

    async def async_step_finish_setup(self, user_input=None) -> FlowResult:
        self._data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        return self.async_create_entry(title=self._title, data=self._data)

    async def async_step_custom_pids_csv(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            csv_text = user_input.get(CONF_CUSTOM_PID_CSV, "").strip()
            if csv_text:
                try:
                    parse_custom_pid_csv(csv_text)
                except FormulaError as err:
                    errors["base"] = "invalid_csv"
                    return self.async_show_form(
                        step_id="custom_pids_csv",
                        data_schema=vol.Schema(
                            {vol.Optional(CONF_CUSTOM_PID_CSV, default=csv_text): str}
                        ),
                        errors=errors,
                        description_placeholders={"error_detail": str(err)},
                    )
            self._data[CONF_CUSTOM_PID_CSV] = csv_text
            return await self.async_step_finish_setup()

        return self.async_show_form(
            step_id="custom_pids_csv",
            data_schema=vol.Schema({vol.Optional(CONF_CUSTOM_PID_CSV, default=""): str}),
            errors=errors,
        )

    # ---------------- OBDb import (optional) ----------------

    async def async_step_obdb_import(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            from . import obdb_importer

            repo = user_input["obdb_repo"].strip()
            year_str = user_input.get("obdb_year", "").strip()
            year = int(year_str) if year_str.isdigit() else None

            session = async_get_clientsession(self.hass)
            try:
                commands = await obdb_importer.fetch_signalset(session, repo, year)
                pid_defs = obdb_importer.parse_signalset_to_pid_defs(commands, repo)
            except obdb_importer.ObdbError as err:
                _LOGGER.debug("OBDb import failed for %s: %s", repo, err)
                errors["base"] = "obdb_fetch_failed"
            else:
                if not pid_defs:
                    errors["base"] = "obdb_no_signals"
                else:
                    existing_json = self._data.get(CONF_OBDB_PIDS)
                    existing = json.loads(existing_json) if existing_json else []
                    existing.extend(p.to_dict() for p in pid_defs)
                    self._data[CONF_OBDB_PIDS] = json.dumps(existing)
                    return await self.async_step_finish_setup()

        return self.async_show_form(
            step_id="obdb_import",
            data_schema=vol.Schema(
                {
                    vol.Required("obdb_repo"): str,
                    vol.Optional("obdb_year", default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "hint": "Repo name exactly as it appears at github.com/OBDb "
                "(e.g. Chevrolet-Bolt-EV, Ford-Mustang). Year is optional - "
                "only needed if that vehicle has model-year-specific overrides."
            },
        )
