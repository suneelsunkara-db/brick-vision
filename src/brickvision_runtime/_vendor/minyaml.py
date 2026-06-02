"""Tiny YAML loader (subset).

Supports the YAML subset BrickVision IR/SKILL.yaml uses:
  - Block mappings (`key: value`)
  - Block sequences (`- item`) including mappings
  - Block scalar `|` (literal) with indent stripping
  - Inline scalars: strings (quoted/unquoted), int, float, bool, null
  - `# ...` line comments
  - JSON-flow values (e.g. `[1, 2]` and `{a: 1}`) for tojson-emitted lines

This loader covers BrickVision's spec shape; it is NOT a general YAML 1.2
implementation. Production replaces it with `yaml.safe_load` (PyYAML) once
the dependency is available.
"""

from __future__ import annotations

import json
import re
from typing import Any


def safe_load(text: str) -> Any:
    """Parse a small YAML document (BrickVision-shaped) into Python primitives."""
    lines = _strip_comments(text.splitlines())
    parser = _Parser(lines)
    return parser.parse_block(indent=0)


def _strip_comments(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = _strip_line_comment(line)
        out.append(stripped)
    while out and not out[-1].strip():
        out.pop()
    return out


def _strip_line_comment(line: str) -> str:
    in_str: str | None = None
    for i, ch in enumerate(line):
        if in_str:
            if ch == "\\":
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "#":
                return line[:i].rstrip()
    return line


_SCALAR_INT = re.compile(r"^-?\d+$")
_SCALAR_FLOAT = re.compile(r"^-?\d+\.\d+([eE][+-]?\d+)?$")


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s == "null" or s == "~":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        try:
            return json.loads(s) if s.startswith('"') else s[1:-1]
        except json.JSONDecodeError:
            return s[1:-1]
    if (s.startswith("[") and s.endswith("]")) or (
        s.startswith("{") and s.endswith("}")
    ):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    if _SCALAR_INT.match(s):
        return int(s)
    if _SCALAR_FLOAT.match(s):
        return float(s)
    return s


class _Parser:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.i = 0

    def _peek(self) -> tuple[int, str] | None:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "":
                self.i += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            return indent, line[indent:]
        return None

    def _consume(self) -> str:
        line = self.lines[self.i]
        self.i += 1
        return line

    def parse_block(self, indent: int) -> Any:
        head = self._peek()
        if head is None:
            return None
        head_indent, head_body = head
        if head_indent < indent:
            return None
        if head_body.startswith("- "):
            return self._parse_sequence(head_indent)
        return self._parse_mapping(head_indent)

    def _parse_mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            peek = self._peek()
            if peek is None:
                break
            cur_indent, body = peek
            if cur_indent < indent:
                break
            if cur_indent > indent:
                break
            if body.startswith("- "):
                break
            self._consume()
            if ":" not in body:
                continue
            key, _, rest = body.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "|":
                result[key] = self._parse_block_literal(indent + 2)
            elif rest == "":
                value = self.parse_block(indent + 2)
                result[key] = value if value is not None else None
            else:
                result[key] = _scalar(rest)
        return result

    def _parse_sequence(self, indent: int) -> list[Any]:
        result: list[Any] = []
        while True:
            peek = self._peek()
            if peek is None:
                break
            cur_indent, body = peek
            if cur_indent != indent or not body.startswith("- "):
                break
            self._consume()
            item_body = body[2:]
            if ":" in item_body and not item_body.startswith(("[", "{", '"', "'")):
                key, _, rest = item_body.partition(":")
                first_pair = (key.strip(), rest.strip())
                rest_map = self._parse_mapping(indent + 2)
                if first_pair[1] == "":
                    inline_value: Any = self.parse_block(indent + 4)
                else:
                    inline_value = _scalar(first_pair[1])
                rest_map = {first_pair[0]: inline_value, **rest_map}
                result.append(rest_map)
            else:
                result.append(_scalar(item_body))
        return result

    def _parse_block_literal(self, indent: int) -> str:
        out: list[str] = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "":
                out.append("")
                self.i += 1
                continue
            cur_indent = len(line) - len(line.lstrip(" "))
            if cur_indent < indent:
                break
            out.append(line[indent:])
            self.i += 1
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n"


__all__ = ["safe_load"]
