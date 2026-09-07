"""Polling coordinator for OBD Multi."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .elm327 import Elm327Client
from .pid_formula import PidDefinition

_LOGGER = logging.getLogger(__name__)

VOLTAGE_ON_THRESHOLD = 13.0
VOLTAGE_OFF_GRACE_INTERVAL = timedelta(minutes=5)


class ObdCoordinator(DataUpdateCoordinator):
    """Fetches all configured standard + custom PIDs on each update cycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: Elm327Client,
        pid_defs: list[PidDefinition],
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="obd_multi",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.pid_defs = pid_defs
        self._fast_interval = timedelta(seconds=scan_interval)

    def set_pid_defs(self, pid_defs: list[PidDefinition]) -> None:
        """Replace the active PID list (e.g. after a custom-PID CSV reload)."""
        self.pid_defs = pid_defs

    async def _async_update_data(self) -> dict[str, float | None]:
        try:
            voltage = await self.client.read_battery_voltage()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Could not read from adapter: {err}") from err

        # Voltage gate: if the car is clearly off, poll less aggressively to
        # avoid draining the 12V battery, and skip the query round entirely.
        if voltage is not None and voltage < VOLTAGE_ON_THRESHOLD:
            self.update_interval = VOLTAGE_OFF_GRACE_INTERVAL
            return {"_voltage": voltage}

        self.update_interval = self._fast_interval

        results: dict[str, float | None] = {"_voltage": voltage}
        for pid_def in self.pid_defs:
            results[pid_def.key] = await self.client.query_pid(pid_def)
        return results
