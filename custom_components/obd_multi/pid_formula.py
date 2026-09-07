"""Safe formula evaluation for OBD PID responses, and CSV import for custom PIDs.

Custom PID CSV format (header row required), one row per PID:

    name,mode,pid,bytes,formula,unit,min,max,header

    name    - friendly name, e.g. "Transmission Temp"
    mode    - OBD service mode, e.g. 01 or 22
    pid     - PID hex string, e.g. 0C  (or full multi-byte PID for mode 22, e.g. 1F9C)
    bytes   - number of data bytes expected in the response after the PID echo
    formula - expression using A,B,C,D,E,F,G,H for successive response bytes
              e.g. "((A*256)+B)/10-40"
    unit    - display unit, e.g. °C, %, rpm, V
    min     - optional expected minimum (informational)
    max     - optional expected maximum (informational)
    header  - optional CAN header override (e.g. 7E4) for multi-ECU vehicles,
              blank to use the default/broadcast header

This mirrors the shape of Torque's user-defined-PID concept (short formula +
raw request bytes) without assuming Torque's exact proprietary file format.
"""

from __future__ import annotations

import ast
import csv
import io
import operator as op
from dataclasses import dataclass, field

_ALLOWED_BINOPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.FloorDiv: op.floordiv,
}
_ALLOWED_UNARYOPS = {ast.USub: op.neg, ast.UAdd: op.pos}
_ALLOWED_NAMES = set("ABCDEFGH")


class FormulaError(ValueError):
    """Raised when a formula is unsafe or invalid."""


def _eval_node(node, values: dict):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, values)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise FormulaError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise FormulaError(f"Unknown variable: {node.id}")
        return values.get(node.id, 0)
    if isinstance(node, ast.BinOp):
        fn = _ALLOWED_BINOPS.get(type(node.op))
        if fn is None:
            raise FormulaError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(_eval_node(node.left, values), _eval_node(node.right, values))
    if isinstance(node, ast.UnaryOp):
        fn = _ALLOWED_UNARYOPS.get(type(node.op))
        if fn is None:
            raise FormulaError(f"Unsupported unary operator: {type(node.op).__name__}")
        return fn(_eval_node(node.operand, values))
    raise FormulaError(f"Unsupported expression: {type(node).__name__}")


def compile_formula(formula: str):
    """Validate a formula up front; raises FormulaError if unsafe/invalid."""
    tree = ast.parse(formula, mode="eval")
    # Walk once to validate without real byte values.
    _eval_node(tree, {c: 1 for c in _ALLOWED_NAMES})
    return tree


def eval_formula(tree, data_bytes: list[int]) -> float:
    values = {chr(ord("A") + i): b for i, b in enumerate(data_bytes)}
    return _eval_node(tree, values)


@dataclass
class PidDefinition:
    key: str
    name: str
    mode: str
    pid: str
    n_bytes: int
    formula: str | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    header: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    # Bit-packed linear-scale decode (used for OBDb-imported signals, which are
    # specified as bit offset/length + mul/add rather than a free-form formula).
    bit_offset: int | None = None
    bit_length: int | None = None
    mul: float | None = None
    add_offset: float | None = None
    _compiled: object = field(default=None, repr=False, compare=False)

    def compiled(self):
        if self.formula is None:
            return None
        if self._compiled is None:
            self._compiled = compile_formula(self.formula)
        return self._compiled

    def decode(self, data_bytes: list[int]) -> float:
        if self.formula is not None:
            return eval_formula(self.compiled(), data_bytes)
        if self.bit_offset is not None and self.bit_length is not None:
            raw_int = 0
            for b in data_bytes:
                raw_int = (raw_int << 8) | b
            total_bits = len(data_bytes) * 8
            shift = total_bits - self.bit_offset - self.bit_length
            if shift < 0:
                raise FormulaError(
                    f"{self.key}: bit_offset/bit_length ({self.bit_offset}/{self.bit_length}) "
                    f"exceeds {total_bits}-bit payload"
                )
            mask = (1 << self.bit_length) - 1
            value = (raw_int >> shift) & mask
            value = value * (self.mul if self.mul is not None else 1)
            value += self.add_offset if self.add_offset is not None else 0
            return value
        raise FormulaError(f"{self.key}: no formula or bit-decode fields set")

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "mode": self.mode, "pid": self.pid,
            "n_bytes": self.n_bytes, "formula": self.formula, "unit": self.unit,
            "device_class": self.device_class, "state_class": self.state_class,
            "header": self.header, "min_value": self.min_value, "max_value": self.max_value,
            "bit_offset": self.bit_offset, "bit_length": self.bit_length,
            "mul": self.mul, "add_offset": self.add_offset,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PidDefinition":
        return cls(**data)


def parse_custom_pid_csv(csv_text: str) -> list[PidDefinition]:
    """Parse a custom-PID CSV (as described in the module docstring)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"name", "mode", "pid", "bytes", "formula"}
    if reader.fieldnames is None or not required.issubset(
        {f.strip().lower() for f in reader.fieldnames}
    ):
        raise FormulaError(
            f"CSV must have at least these columns: {', '.join(sorted(required))}"
        )

    defs: list[PidDefinition] = []
    for i, row in enumerate(reader):
        row = {k.strip().lower(): (v.strip() if v else v) for k, v in row.items()}
        try:
            n_bytes = int(row["bytes"])
        except (TypeError, ValueError):
            raise FormulaError(f"Row {i + 1}: 'bytes' must be an integer")

        pid_def = PidDefinition(
            key=f"custom_{row['pid'].lower()}_{i}",
            name=row["name"],
            mode=row["mode"].zfill(2),
            pid=row["pid"].upper(),
            n_bytes=n_bytes,
            formula=row["formula"],
            unit=row.get("unit") or None,
            header=row.get("header") or None,
            min_value=float(row["min"]) if row.get("min") else None,
            max_value=float(row["max"]) if row.get("max") else None,
        )
        # Validates the formula immediately so bad rows fail at import time,
        # not at first poll.
        pid_def.compiled()
        defs.append(pid_def)
    return defs
