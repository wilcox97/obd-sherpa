# OBD Multi (WiFi / Bluetooth Classic / BLE)

A single Home Assistant custom integration that talks the standard ELM327
AT-command protocol over three transports, with SAE J1979 Mode 01 PIDs
built in and a CSV-based custom-PID system (Torque-style: name + request
bytes + formula) for anything vehicle-specific.

## Install

1. Copy `custom_components/obd_multi/` into your HA `config/custom_components/`.
2. Restart Home Assistant.
3. Settings > Devices & Services > Add Integration > "OBD Multi".

If your Bluetooth Classic adapter needs it, also install `pybluez2` on the
HA host (`pip install pybluez2`) - the manifest lists it as a requirement,
but on HAOS this only works if the Supervisor/container has raw Bluetooth
socket access, which isn't guaranteed on every install. WiFi and BLE do not
have this constraint.

## Pairing / discovery by transport

- **WiFi**: no pairing. Just host:IP and port (usually 35000). Setup opens a
  raw TCP socket and sends `ATZ`/`ATE0` to confirm the adapter answers.
- **Bluetooth Classic (SPP)**: pair the dongle in your OS Bluetooth settings
  first (PIN is usually `1234` or `0000`). This integration only does the
  RFCOMM *connect*, not the pairing handshake - by the time you add it here,
  the OS should already show it as paired.
- **BLE**: no pairing step exists in BLE the way it does for Classic. Setup
  lists nearby BLE advertisements HA's own Bluetooth integration has already
  seen with an OBD/ELM/Veepeak/Vgate-like name, you pick one, and the flow
  tries a couple of known GATT UUID pairs (Veepeak/HM-10/LeLink-style are
  covered) automatically. If none match - some clones use vendor-unique
  UUIDs - you'll be asked to supply the write/notify characteristic UUIDs
  yourself (find them with a BLE scanner app like nRF Connect).

## Custom PIDs

During setup (or later via the `obd_multi.reload_custom_pids` service) you
can supply a CSV like:

```csv
name,mode,pid,bytes,formula,unit,min,max,header
Transmission Temp,22,1F9C,2,"((A*256)+B)/10-40",°C,-40,215,7E1
Trans Fluid Life,22,1F45,1,A,%,0,100,
```

Columns:

| column  | meaning                                                              |
|---------|-----------------------------------------------------------------------|
| name    | Friendly name shown in HA                                             |
| mode    | OBD service mode (`01` standard, `22` manufacturer-enhanced, etc.)    |
| pid     | Hex PID string (can be multi-byte for mode 22, e.g. `1F9C`)          |
| bytes   | Number of data bytes expected after the PID echo                     |
| formula | Expression using `A,B,C,D...` for successive data bytes              |
| unit    | Display unit (optional)                                               |
| min/max | Informational only, not enforced                                      |
| header  | CAN header override for multi-ECU vehicles (optional, e.g. `7E1`)    |

Formulas are parsed with Python's `ast` module and restricted to numeric
literals, `A`-`H` variables, and `+ - * / // % **` - no function calls or
attribute access, so a bad or malicious CSV can't execute arbitrary code.

Standard Mode 01 PIDs (RPM, coolant temp, speed, throttle, fuel level,
MAF, fuel trims, O2 voltage, control module voltage, etc.) are always
active and don't need to be listed in your CSV.

## Known limitations (v0.1)

- Mode 22 PIDs are vehicle-specific by nature; there's no OBDb/WiCAN
  auto-lookup here yet - you supply your own CSV per vehicle.
- The custom-PID text box in the config flow UI is a plain text field, not
  a file upload; paste CSV contents directly, or use the `reload_custom_pids`
  service with a `csv:` block for anything long.
- BLE UUID auto-probe only tries two common clone patterns; wider vendor
  coverage would mean expanding `_KNOWN_BLE_UUID_PAIRS` in `config_flow.py`
  as adapters get tested.
