"""ELM327 AT-command protocol handling: init sequence, PID queries, response parsing."""

from __future__ import annotations

import logging
import re

from .pid_formula import PidDefinition
from .transport import ElmTransport, ElmTransportError

_LOGGER = logging.getLogger(__name__)

INIT_COMMANDS = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP0"]

_HEX_LINE_RE = re.compile(r"^[0-9A-Fa-f ]+$")
_ISO_TP_FRAME_INDEX_RE = re.compile(r"^([0-9A-Fa-f]):")


class Elm327Client:
    """Wraps a transport with ELM327 protocol semantics."""

    def __init__(self, transport: ElmTransport):
        self._transport = transport
        self._current_header: str | None = None

    async def connect_and_init(self) -> None:
        await self._transport.connect()
        for cmd in INIT_COMMANDS:
            try:
                await self._transport.write_command(cmd)
            except ElmTransportError:
                _LOGGER.debug("Init command %s failed/ignored", cmd)

    async def close(self) -> None:
        await self._transport.close()

    async def read_battery_voltage(self) -> float | None:
        try:
            reply = await self._transport.write_command("ATRV")
        except ElmTransportError:
            return None
        match = re.search(r"([\d.]+)\s*V", reply)
        return float(match.group(1)) if match else None

    async def read_vin(self) -> str | None:
        """Query Mode 09 PID 02 (VIN) and reassemble the multi-frame ISO-TP reply.

        Cheap ELM327 clones vary in how they format multi-line responses; this
        handles the common "N: xx xx xx.." consecutive-frame style used when
        line formatting (ATL0) is on. Falls back to None on anything unusual
        rather than guessing wrong.
        """
        try:
            reply = await self._transport.write_command("0902")
        except ElmTransportError:
            return None

        lines = [ln.strip() for ln in reply.replace(">", "").splitlines() if ln.strip()]
        hex_bytes: list[int] = []
        for line in lines:
            content = line
            m = _ISO_TP_FRAME_INDEX_RE.match(line)
            if m:
                content = line[m.end() :].strip()
            if "NODATA" in content.upper() or "ERROR" in content.upper():
                continue
            for tok in content.split():
                try:
                    hex_bytes.append(int(tok, 16))
                except ValueError:
                    pass

        # Strip leading response/PID-echo bytes (49 02 01) if present, then
        # decode the remainder as ASCII, dropping non-printable padding.
        if len(hex_bytes) >= 3 and hex_bytes[0] == 0x49 and hex_bytes[1] == 0x02:
            hex_bytes = hex_bytes[3:]
        try:
            vin = bytes(b for b in hex_bytes if 0x20 <= b <= 0x7E).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            return None
        vin = vin.strip()
        return vin if len(vin) >= 11 else None  # real VINs are 17 chars; be lenient on garbage clones

    async def _set_header(self, header: str | None) -> None:
        if header and header != self._current_header:
            await self._transport.write_command(f"ATSH{header}")
            self._current_header = header
        elif not header and self._current_header is not None:
            await self._transport.write_command("ATSH7DF")
            self._current_header = None

    async def query_pid(self, pid_def: PidDefinition) -> float | None:
        """Send a mode+pid request and decode the response using the PID's formula."""
        try:
            await self._set_header(pid_def.header)
            reply = await self._transport.write_command(f"{pid_def.mode}{pid_def.pid}")
        except ElmTransportError as err:
            _LOGGER.debug("Query failed for %s: %s", pid_def.key, err)
            return None

        data_bytes = self._extract_data_bytes(reply, pid_def)
        if data_bytes is None:
            return None
        try:
            return pid_def.decode(data_bytes)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Decode failed for %s: %s", pid_def.key, err)
            return None

    def _extract_data_bytes(self, reply: str, pid_def: PidDefinition) -> list[int] | None:
        lines = [ln.strip() for ln in reply.replace(">", "").splitlines() if ln.strip()]
        for line in lines:
            compact = line.replace(" ", "")
            if "NODATA" in compact.upper() or "ERROR" in compact.upper():
                continue
            if not _HEX_LINE_RE.match(line):
                continue
            byte_strs = line.split()
            if len(byte_strs) < 2:
                continue
            # Expected echo: [response_mode] [pid] [data...]
            # response mode = request mode + 0x40 for mode 01; mode 22 echoes 62.
            try:
                all_bytes = [int(b, 16) for b in byte_strs]
            except ValueError:
                continue
            # Find the PID byte(s) in the echo and take what follows.
            pid_bytes = [int(pid_def.pid[i : i + 2], 16) for i in range(0, len(pid_def.pid), 2)]
            for start in range(len(all_bytes) - len(pid_bytes)):
                if all_bytes[start : start + len(pid_bytes)] == pid_bytes:
                    data = all_bytes[start + len(pid_bytes) :]
                    if len(data) >= pid_def.n_bytes:
                        return data[: pid_def.n_bytes]
            # Fallback: assume [mode_echo, pid_echo, data...] for single-byte PIDs
            if len(all_bytes) >= 2 + pid_def.n_bytes:
                return all_bytes[2 : 2 + pid_def.n_bytes]
        return None
