"""Constants for the OBD Multi integration."""

DOMAIN = "obd_multi"

CONF_TRANSPORT = "transport"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_BT_ADDRESS = "bt_address"
CONF_BLE_ADDRESS = "ble_address"
CONF_BLE_UUID_WRITE = "ble_uuid_write"
CONF_BLE_UUID_NOTIFY = "ble_uuid_notify"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_HEADER = "header"
CONF_CUSTOM_PID_CSV = "custom_pid_csv"

TRANSPORT_WIFI = "wifi"
TRANSPORT_BT_CLASSIC = "bt_classic"
TRANSPORT_BLE = "ble"

DEFAULT_WIFI_PORT = 35000
DEFAULT_SCAN_INTERVAL = 10

# Standard SAE J1979 Mode 01 PIDs.
# formula uses variables A,B,C,D (successive response data bytes as ints 0-255)
# bytes = expected number of data bytes in the response (post PID echo)
STANDARD_PIDS = {
    "ENGINE_LOAD": {
        "mode": "01", "pid": "04", "bytes": 1,
        "formula": "A / 2.55", "unit": "%", "name": "Engine Load",
        "device_class": None, "state_class": "measurement",
    },
    "COOLANT_TEMP": {
        "mode": "01", "pid": "05", "bytes": 1,
        "formula": "A - 40", "unit": "°C", "name": "Coolant Temperature",
        "device_class": "temperature", "state_class": "measurement",
    },
    "SHORT_FUEL_TRIM_1": {
        "mode": "01", "pid": "06", "bytes": 1,
        "formula": "(A - 128) * 100 / 128", "unit": "%", "name": "Short Fuel Trim Bank 1",
        "device_class": None, "state_class": "measurement",
    },
    "LONG_FUEL_TRIM_1": {
        "mode": "01", "pid": "07", "bytes": 1,
        "formula": "(A - 128) * 100 / 128", "unit": "%", "name": "Long Fuel Trim Bank 1",
        "device_class": None, "state_class": "measurement",
    },
    "INTAKE_MAP": {
        "mode": "01", "pid": "0B", "bytes": 1,
        "formula": "A", "unit": "kPa", "name": "Intake Manifold Pressure",
        "device_class": "pressure", "state_class": "measurement",
    },
    "ENGINE_SPEED": {
        "mode": "01", "pid": "0C", "bytes": 2,
        "formula": "((A * 256) + B) / 4", "unit": "rpm", "name": "Engine RPM",
        "device_class": None, "state_class": "measurement",
    },
    "VEHICLE_SPEED": {
        "mode": "01", "pid": "0D", "bytes": 1,
        "formula": "A", "unit": "km/h", "name": "Vehicle Speed",
        "device_class": "speed", "state_class": "measurement",
    },
    "TIMING_ADVANCE": {
        "mode": "01", "pid": "0E", "bytes": 1,
        "formula": "(A / 2) - 64", "unit": "°", "name": "Timing Advance",
        "device_class": None, "state_class": "measurement",
    },
    "INTAKE_TEMP": {
        "mode": "01", "pid": "0F", "bytes": 1,
        "formula": "A - 40", "unit": "°C", "name": "Intake Air Temperature",
        "device_class": "temperature", "state_class": "measurement",
    },
    "MAF": {
        "mode": "01", "pid": "10", "bytes": 2,
        "formula": "((A * 256) + B) / 100", "unit": "g/s", "name": "Mass Air Flow",
        "device_class": None, "state_class": "measurement",
    },
    "THROTTLE_POS": {
        "mode": "01", "pid": "11", "bytes": 1,
        "formula": "A / 2.55", "unit": "%", "name": "Throttle Position",
        "device_class": None, "state_class": "measurement",
    },
    "O2_B1S1_VOLTAGE": {
        "mode": "01", "pid": "14", "bytes": 2,
        "formula": "A / 200", "unit": "V", "name": "O2 Sensor B1S1 Voltage",
        "device_class": "voltage", "state_class": "measurement",
    },
    "RUNTIME": {
        "mode": "01", "pid": "1F", "bytes": 2,
        "formula": "(A * 256) + B", "unit": "s", "name": "Engine Run Time",
        "device_class": "duration", "state_class": "measurement",
    },
    "DISTANCE_WITH_MIL": {
        "mode": "01", "pid": "21", "bytes": 2,
        "formula": "(A * 256) + B", "unit": "km", "name": "Distance with MIL On",
        "device_class": None, "state_class": "total_increasing",
    },
    "FUEL_RAIL_PRESSURE": {
        "mode": "01", "pid": "23", "bytes": 2,
        "formula": "((A * 256) + B) * 10", "unit": "kPa", "name": "Fuel Rail Pressure",
        "device_class": "pressure", "state_class": "measurement",
    },
    "FUEL_LEVEL": {
        "mode": "01", "pid": "2F", "bytes": 1,
        "formula": "A / 2.55", "unit": "%", "name": "Fuel Level",
        "device_class": "battery", "state_class": "measurement",
    },
    "BAROMETRIC_PRESSURE": {
        "mode": "01", "pid": "33", "bytes": 1,
        "formula": "A", "unit": "kPa", "name": "Barometric Pressure",
        "device_class": "pressure", "state_class": "measurement",
    },
    "CATALYST_TEMP_B1S1": {
        "mode": "01", "pid": "3C", "bytes": 2,
        "formula": "((A * 256) + B) / 10 - 40", "unit": "°C", "name": "Catalyst Temperature B1S1",
        "device_class": "temperature", "state_class": "measurement",
    },
    "CONTROL_MODULE_VOLTAGE": {
        "mode": "01", "pid": "42", "bytes": 2,
        "formula": "((A * 256) + B) / 1000", "unit": "V", "name": "Control Module Voltage",
        "device_class": "voltage", "state_class": "measurement",
    },
    "ABSOLUTE_LOAD": {
        "mode": "01", "pid": "43", "bytes": 2,
        "formula": "((A * 256) + B) / 2.55", "unit": "%", "name": "Absolute Load Value",
        "device_class": None, "state_class": "measurement",
    },
    "AMBIENT_AIR_TEMP": {
        "mode": "01", "pid": "46", "bytes": 1,
        "formula": "A - 40", "unit": "°C", "name": "Ambient Air Temperature",
        "device_class": "temperature", "state_class": "measurement",
    },
    "OIL_TEMP": {
        "mode": "01", "pid": "5C", "bytes": 1,
        "formula": "A - 40", "unit": "°C", "name": "Engine Oil Temperature",
        "device_class": "temperature", "state_class": "measurement",
    },
    "FUEL_RATE": {
        "mode": "01", "pid": "5E", "bytes": 2,
        "formula": "((A * 256) + B) / 20", "unit": "L/h", "name": "Fuel Rate",
        "device_class": None, "state_class": "measurement",
    },
}
