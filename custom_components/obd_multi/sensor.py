"""Sensor platform for OBD Multi - one entity per configured PID."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ObdCoordinator
from .pid_formula import PidDefinition


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    stored = hass.data[DOMAIN][entry.entry_id]
    coordinator: ObdCoordinator = stored["coordinator"]

    entities = [ObdPidSensor(coordinator, entry, pid_def) for pid_def in coordinator.pid_defs]
    entities.append(ObdVoltageSensor(coordinator, entry))
    async_add_entities(entities)


class ObdPidSensor(CoordinatorEntity[ObdCoordinator], SensorEntity):
    def __init__(self, coordinator: ObdCoordinator, entry: ConfigEntry, pid_def: PidDefinition):
        super().__init__(coordinator)
        self._pid_def = pid_def
        self._attr_unique_id = f"{entry.entry_id}_{pid_def.key}"
        self._attr_name = pid_def.name
        self._attr_native_unit_of_measurement = pid_def.unit
        self._attr_device_class = pid_def.device_class
        self._attr_state_class = pid_def.state_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="OBD Adapter", manufacturer="Generic ELM327"
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._pid_def.key)


class ObdVoltageSensor(CoordinatorEntity[ObdCoordinator], SensorEntity):
    """Adapter-reported 12V battery voltage, used internally for the polling gate."""

    _attr_device_class = "voltage"
    _attr_native_unit_of_measurement = "V"
    _attr_state_class = "measurement"

    def __init__(self, coordinator: ObdCoordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_voltage"
        self._attr_name = "12V Battery Voltage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="OBD Adapter", manufacturer="Generic ELM327"
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("_voltage")
