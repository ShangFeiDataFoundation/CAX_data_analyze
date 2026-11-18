"""STEP 文件头部元数据的轻量文本解析（不依赖 OCP，仅读取 ISO-10303-21 头段）。

STEP 头段样例：
    HEADER;
    FILE_DESCRIPTION((''),'2;1');
    FILE_NAME('part_name','2023-01-01T00:00:00',('author'),('org'),
              'preprocessor','system','authorization');
    FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
    ENDSEC;
"""

from __future__ import annotations

import re
from typing import Any


_NAME_RE = re.compile(r"FILE_NAME\s*\(\s*'([^']*)'\s*,\s*'([^']*)'", re.S)
_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", re.S)
_DESC_RE = re.compile(r"FILE_DESCRIPTION\s*\(\s*\(\s*(.*?)\)\s*,\s*'([^']*)'", re.S)
_SI_UNIT_RE = re.compile(r"SI_UNIT\s*\(\s*([^)]*)\)", re.S)
_CONV_UNIT_RE = re.compile(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", re.S)


def parse_step_header(text: str) -> dict[str, Any]:
    """返回 STEP 头段元数据字典（含单位/版本尝试性推断）。"""
    meta: dict[str, Any] = {
        "name": None,
        "timestamp": None,
        "schema": None,
        "description": None,
        "implementation_level": None,
        "preprocessor": None,
        "organization": None,
        "author": None,
        "units": None,
    }

    m = _NAME_RE.search(text)
    if m:
        meta["name"] = m.group(1) or None
        meta["timestamp"] = m.group(2) or None

    m = _SCHEMA_RE.search(text)
    if m:
        meta["schema"] = m.group(1).strip() or None

    m = _DESC_RE.search(text)
    if m:
        meta["description"] = (m.group(1) or "").strip("'\" \t") or None

    # 实现级别：ISO-10303-21;
    impl = re.search(r"ISO-10303-21\s*;", text)
    if impl:
        meta["implementation_level"] = "ISO-10303-21"

    meta["units"] = _detect_length_unit(text)

    return meta


def _detect_length_unit(text: str) -> str | None:
    """从 STEP 数据段解析长度单位（SI_UNIT / CONVERSION_BASED_UNIT）。"""
    low = text.lower()

    # SI_UNIT 前缀（长度单位），如 SI_UNIT(.MILLI.,.METRE.)
    for m in _SI_UNIT_RE.finditer(text):
        body = m.group(1).replace(" ", "")
        lowered = body.lower()
        if "milli" in lowered and "metre" in lowered:
            return "MILLIMETRE"
        if "metre" in lowered and "milli" not in lowered and "micro" not in lowered:
            return "METRE"
        if "micro" in lowered and "metre" in lowered:
            return "MICROMETRE"
        if "milli" in lowered and "inch" in lowered:  # 少见
            return "INCH"

    for m in _CONV_UNIT_RE.finditer(text):
        name = m.group(1).strip().lower()
        mapping = {
            "inch": "INCH",
            "millimetre": "MILLIMETRE",
            "millimeter": "MILLIMETRE",
            "centimetre": "CENTIMETRE",
            "metre": "METRE",
            "meter": "METRE",
        }
        if name in mapping:
            return mapping[name]

    # 兜底：常见命名关键字
    for unit, names in (
        ("MILLIMETRE", ("millimetre", "millimeter", "milli")),
        ("METRE", ("metre", "meter")),
        ("INCH", ("inch",)),
    ):
        if any(n in low for n in names):
            return unit
    return None
