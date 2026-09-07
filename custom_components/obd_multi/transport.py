"""Transport-layer abstraction for talking AT-command ELM327 over WiFi, BT classic, or BLE."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

_LOGGER = logging.getLogger(__name__)

PROMPT = b">"


class ElmTransportError(Exception):
    """Raised on connect/read/write failure."""


class ElmTransport(ABC):
    """Common interface: send an AT/OBD command string, get the raw reply back."""

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def write_command(self, command: str) -> str:
        """Write a command (without CR) and return the decoded reply up to the '>' prompt."""

    @abstractmethod
    async def close(self) -> None:
        ...


class WifiTransport(ElmTransport):
    """Raw TCP socket to a WiFi ELM327 (most clones default to port 35000)."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=self._timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise ElmTransportError(f"WiFi connect failed: {err}") from err

    async def write_command(self, command: str) -> str:
        if not self._writer or not self._reader:
            raise ElmTransportError("Not connected")
        self._writer.write((command + "\r").encode())
        await self._writer.drain()
        try:
            data = await asyncio.wait_for(
                self._reader.readuntil(PROMPT), timeout=self._timeout
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
            raise ElmTransportError(f"WiFi read timeout: {err}") from err
        return data.decode(errors="ignore")

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


class BtClassicTransport(ElmTransport):
    """RFCOMM/SPP socket to a paired classic-Bluetooth ELM327.

    Uses pybluez2 (a maintained fork of PyBluez) for a raw RFCOMM socket so no
    system-level `rfcomm bind` step is required. Requires BlueZ + host Bluetooth
    access to be available to the HA process (true on HAOS/Supervised, may not
    be on some container-only setups).
    """

    DEFAULT_PINS = ("1234", "0000")

    @staticmethod
    async def discover(timeout: float = 8.0) -> dict[str, str]:
        """Scan for nearby classic-BT devices, returns {address: name}."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, BtClassicTransport._discover_sync, timeout)

    @staticmethod
    def _discover_sync(timeout: float) -> dict[str, str]:
        try:
            import bluetooth
        except ImportError:
            return {}
        try:
            found = bluetooth.discover_devices(duration=int(timeout), lookup_names=True)
        except OSError as err:
            _LOGGER.debug("Classic BT discovery failed: %s", err)
            return {}
        return {addr: name for addr, name in found}

    @staticmethod
    async def try_pair(address: str) -> bool:
        """Best-effort automatic pairing using common default PINs.

        Many cheap ELM327 SPP dongles use "Just Works"/no real auth and will
        accept an RFCOMM connect without a formal OS pairing step at all, so
        this is tried first; only if that fails do we attempt bluetoothctl
        pairing with the two common default PINs.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, BtClassicTransport._try_pair_sync, address)

    @staticmethod
    def _try_pair_sync(address: str) -> bool:
        import subprocess

        try:
            import bluetooth

            sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            sock.settimeout(4.0)
            sock.connect((address, 1))
            sock.close()
            return True  # no pairing needed at all
        except Exception:  # noqa: BLE001
            pass

        for pin in BtClassicTransport.DEFAULT_PINS:
            try:
                subprocess.run(
                    ["bluetoothctl", "pair", address],
                    input=f"{pin}\n",
                    text=True,
                    timeout=10,
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["bluetoothctl", "trust", address],
                    timeout=5,
                    capture_output=True,
                    check=False,
                )
                import bluetooth as bt2

                sock = bt2.BluetoothSocket(bt2.RFCOMM)
                sock.settimeout(4.0)
                sock.connect((address, 1))
                sock.close()
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def __init__(self, address: str, port: int = 1, timeout: float = 5.0):
        self._address = address
        self._port = port
        self._timeout = timeout
        self._sock = None

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> None:
        try:
            import bluetooth  # pybluez2 exposes the classic `bluetooth` module name
        except ImportError as err:
            raise ElmTransportError(
                "pybluez2 not available - classic Bluetooth transport requires it"
            ) from err
        try:
            self._sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            self._sock.settimeout(self._timeout)
            self._sock.connect((self._address, self._port))
        except OSError as err:
            raise ElmTransportError(f"Bluetooth connect failed: {err}") from err

    async def write_command(self, command: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._write_command_sync, command)

    def _write_command_sync(self, command: str) -> str:
        if not self._sock:
            raise ElmTransportError("Not connected")
        self._sock.send((command + "\r").encode())
        buf = b""
        while PROMPT not in buf:
            chunk = self._sock.recv(256)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="ignore")

    async def close(self) -> None:
        if self._sock:
            self._sock.close()


class BleTransport(ElmTransport):
    """GATT notify/write pair to a BLE ELM327 clone, via Home Assistant's bleak client.

    Every vendor uses different service/characteristic UUIDs for what is
    otherwise the same AT-command protocol, so uuid_write/uuid_notify are
    supplied by the config flow's discovery/probe step rather than assumed.
    """

    def __init__(self, ble_device, uuid_write: str, uuid_notify: str, timeout: float = 5.0):
        self._ble_device = ble_device
        self._uuid_write = uuid_write
        self._uuid_notify = uuid_notify
        self._timeout = timeout
        self._client = None
        self._buffer = bytearray()
        self._event = None

    async def connect(self) -> None:
        from bleak import BleakClient

        self._event = asyncio.Event()
        self._client = BleakClient(self._ble_device)
        try:
            await self._client.connect(timeout=self._timeout)
            await self._client.start_notify(self._uuid_notify, self._notify_handler)
        except Exception as err:  # noqa: BLE001
            raise ElmTransportError(f"BLE connect failed: {err}") from err

    def _notify_handler(self, _handle, data: bytearray) -> None:
        self._buffer.extend(data)
        if PROMPT in self._buffer:
            self._event.set()

    async def write_command(self, command: str) -> str:
        if not self._client:
            raise ElmTransportError("Not connected")
        self._buffer.clear()
        self._event.clear()
        try:
            await self._client.write_gatt_char(
                self._uuid_write, (command + "\r").encode()
            )
            await asyncio.wait_for(self._event.wait(), timeout=self._timeout)
        except (asyncio.TimeoutError, Exception) as err:  # noqa: BLE001
            raise ElmTransportError(f"BLE write/read failed: {err}") from err
        return bytes(self._buffer).decode(errors="ignore")

    async def close(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
