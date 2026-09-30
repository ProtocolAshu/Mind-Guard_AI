"""Robust extraction of a single JSON object from model text (fences, prose around it)."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
MAX_SCAN = 40_000


def _balanced_objects(text: str) -> list[str]:
    out: list[str] = []
    start = text.find("{")
    while start != -1 and len(out) < 5:
        depth, in_str, esc = 0, False, False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            break
        out.append(text[start : end + 1])
        start = text.find("{", end + 1)
    return out


def extract_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    text = text[:MAX_SCAN]
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + _balanced_objects(text)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
