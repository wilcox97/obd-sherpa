"""Fetch and parse OBDb (github.com/OBDb) vehicle signalsets.

OBDb is a community-maintained, per-make/model set of GitHub repos (e.g.
OBDb/Chevrolet-Bolt-EV, OBDb/Ford-Mustang). Each repo holds:

    signalsets/v3/default.json          - baseline, used for any year without
                                           a more specific override file
    signalsets/v3/<year-range>.json     - e.g. "2012-2020.json", overrides
                                           default.json for those years

Each file is a list of "command" blocks:

    {
      "hdr": "7E0",
      "cmd": {"22": "038F"},
      "signals": [
        {"id": "...", "name": "...",
         "fmt": {"len": 16, "mul": 0.1998, "add": 0, "unit": "volts",
                 "min": -40, "max": 5}}
      ]
    }

`fmt.len` is in BITS, not bytes. Where a command has multiple signals with no
explicit bit offset, this importer assumes sequential packing in list order
(signal 0 starts at bit 0, signal 1 starts where signal 0 ends, etc.) - the
common convention for this kind of format. If a signal DOES specify a "bix"
(bit index) field, that's used directly instead of the assumption.

This module only fetches and parses - it doesn't know about Home Assistant
config entries at all, so it's easy to test standalone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import aiohttp

from .pid_formula import PidDefinition

_LOGGER = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com/OBDb"


class ObdbError(Exception):
    """Raised on network/parse failure talking to OBDb."""


@dataclass
class ObdbRepoMatch:
    repo_name: str
    description: str | None = None


async def search_obdb_repo(session: aiohttp.ClientSession, query: str) -> list[ObdbRepoMatch]:
    """Search the OBDb GitHub org for repos matching a free-text make/model query."""
    url = f"{GITHUB_API}/search/repositories"
    params = {"q": f"org:OBDb {query}", "per_page": "10"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise ObdbError(f"GitHub search returned HTTP {resp.status}")
            data = await resp.json()
    except aiohttp.ClientError as err:
        raise ObdbError(f"Network error searching OBDb: {err}") from err

    return [
        ObdbRepoMatch(repo_name=item["name"], description=item.get("description"))
        for item in data.get("items", [])
    ]


async def _list_signalset_files(session: aiohttp.ClientSession, repo_name: str) -> list[str]:
    url = f"{GITHUB_API}/repos/OBDb/{repo_name}/contents/signalsets/v3"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            raise ObdbError(f"Could not list signalsets for {repo_name}: HTTP {resp.status}")
        data = await resp.json()
    return [item["name"] for item in data if item["name"].endswith(".json")]


def _year_range_matches(filename: str, year: int) -> bool:
    stem = filename.removesuffix(".json")
    if "-" in stem:
        try:
            lo, hi = stem.split("-")
            return int(lo) <= year <= int(hi)
        except ValueError:
            return False
    else:
        try:
            return int(stem) == year
        except ValueError:
            return False


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> list | dict:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            raise ObdbError(f"Could not fetch {url}: HTTP {resp.status}")
        text = await resp.text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise ObdbError(f"Malformed JSON at {url}: {err}") from err


async def fetch_signalset(
    session: aiohttp.ClientSession, repo_name: str, year: int | None = None
) -> list[dict]:
    """Fetch default.json, merged with a year-specific override file if one matches."""
    default_url = f"{RAW_BASE}/{repo_name}/main/signalsets/v3/default.json"
    commands = await _fetch_json(session, default_url)
    if isinstance(commands, dict):
        commands = commands.get("commands", [])

    if year is not None:
        try:
            filenames = await _list_signalset_files(session, repo_name)
        except ObdbError:
            filenames = []
        for filename in filenames:
            if filename == "default.json":
                continue
            if _year_range_matches(filename, year):
                override_url = f"{RAW_BASE}/{repo_name}/main/signalsets/v3/{filename}"
                override = await _fetch_json(session, override_url)
                if isinstance(override, dict):
                    override = override.get("commands", [])
                commands = override  # override file replaces, per OBDb's own semantics
                break

    return commands


def parse_signalset_to_pid_defs(commands: list[dict], repo_name: str) -> list[PidDefinition]:
    """Convert a fetched signalset's command blocks into PidDefinition objects."""
    pid_defs: list[PidDefinition] = []
    for cmd_idx, command in enumerate(commands):
        header = command.get("hdr")
        cmd_map = command.get("cmd", {})
        if not cmd_map:
            continue
        mode, pid_hex = next(iter(cmd_map.items()))
        signals = command.get("signals", [])
        if not signals:
            continue

        # Response payload length in bytes: derive from the widest bit extent
        # among this command's signals (mode+pid echo bytes are handled
        # separately by elm327.py's byte-extraction logic, same as custom CSV PIDs).
        max_bit = max(
            (sig.get("fmt", {}).get("bix", running_bit_offset(signals, i)) + sig["fmt"]["len"])
            for i, sig in enumerate(signals)
        )
        n_bytes = (max_bit + 7) // 8

        for i, signal in enumerate(signals):
            fmt = signal.get("fmt", {})
            bit_len = fmt.get("len")
            if bit_len is None:
                continue
            bit_offset = fmt.get("bix", running_bit_offset(signals, i))
            pid_defs.append(
                PidDefinition(
                    key=f"obdb_{repo_name.lower()}_{cmd_idx}_{i}",
                    name=signal.get("name", signal.get("id", f"{repo_name} signal {i}")),
                    mode=str(mode).zfill(2),
                    pid=str(pid_hex).upper(),
                    n_bytes=n_bytes,
                    header=header,
                    unit=fmt.get("unit"),
                    min_value=fmt.get("min"),
                    max_value=fmt.get("max"),
                    bit_offset=bit_offset,
                    bit_length=bit_len,
                    mul=fmt.get("mul", 1),
                    add_offset=fmt.get("add", 0),
                    state_class="measurement",
                )
            )
    return pid_defs


def running_bit_offset(signals: list[dict], index: int) -> int:
    """Sequential-packing assumption for signals with no explicit bit index (bix)."""
    offset = 0
    for sig in signals[:index]:
        offset += sig.get("fmt", {}).get("len", 0)
    return offset
