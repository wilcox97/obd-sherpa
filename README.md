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

## OBDb import (vehicle-specific PIDs from the community database)

During setup, instead of (or alongside) a CSV, you can pick "Import from
OBDb" and give a repo name exactly as it appears at github.com/OBDb, e.g.
`Chevrolet-Bolt-EV` or `Ford-Mustang`. This fetches that vehicle's real
`signalsets/v3/default.json` (plus a model-year override file if you give a
year and one exists), and adds every signal in it as a PID sensor.

This is intentionally *not* a static "top N vehicles" list baked into the
integration - OBDb is a living, community-maintained catalog covering
everything from Bolts to Mustangs to oddball trims, and importing live means
you get whatever's been documented as of today rather than a snapshot that
goes stale. If your exact vehicle isn't there yet, OBDb takes requests
(github.com/OBDb).

Caveat: OBDb signals are specified as bit offset + bit length + linear scale,
not whole-byte formulas. When a command block has more than one signal and no
explicit bit index, this importer assumes they're packed sequentially in the
order listed - the common convention, but not something I can verify against
every vehicle without hardware in hand. Cross-check a couple of imported
values against your dash/OEM app the first time you use a new vehicle.

## Automatic vehicle detection (VIN -> NHTSA -> OBDb)

After connecting to an adapter (any transport), setup queries Mode 09 PID 02
for the VIN, decodes it via NHTSA's free public vPIC API (no key needed) to
get Make/Model/Year, then searches OBDb for a matching repo and imports its
PIDs automatically - no typing a repo name required. If any step fails (VIN
not readable, NHTSA can't decode it, or no OBDb repo exists for that
make/model), it fails silently and falls back to standard PIDs only - you
can still add PIDs manually later via the CSV or OBDb-import menu options,
or by re-running setup once you know a specific repo name.

Live-tested against the real services: a Bolt EV VIN correctly resolved to
`OBDb/Chevrolet-Bolt-EV` and imported 107 real PIDs (HV battery temp, charger
AC voltage/current, etc.). A vehicle with no OBDb coverage (tested against a
Mercury Mariner) correctly found zero matches and fell through to standard
PIDs with no error.


The setup form for BT Classic has an optional custom PIN field. Order of
attempts: (1) raw RFCOMM connect with no PIN at all - many cheap SPP dongles
don't enforce auth; (2) your custom PIN, if you entered one - this is for
adapters like the OBDLink MX/MX+ that support setting a non-default
Bluetooth PIN via the OBDLink app's Bluetooth settings; (3) the common
defaults `1234` and `0000`.

Auto-discovery (`async_step_bluetooth`) does the same thing but only tries
the hardcoded defaults, since there's no custom PIN to supply in that flow -
if your adapter uses a custom PIN, add it manually via the BT Classic option
in the main menu instead of waiting for discovery.


- Mode 22 PIDs are vehicle-specific by nature; there's no OBDb/WiCAN
  auto-lookup here yet - you supply your own CSV per vehicle.
- The custom-PID text box in the config flow UI is a plain text field, not
  a file upload; paste CSV contents directly, or use the `reload_custom_pids`
  service with a `csv:` block for anything long.
- BLE UUID auto-probe only tries two common clone patterns; wider vendor
  coverage would mean expanding `_KNOWN_BLE_UUID_PAIRS` in `config_flow.py`
  as adapters get tested.
