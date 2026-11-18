"""IGES 头段元数据解析（不依赖 OCP）。

IGES 是 80 列定长文本：第 73 列为分节标识（S/G/D/P/T）。
全局节（G）以 Hollerith 字符串给出参数，前两个参数分别是参数分隔符与记录分隔符，
随后依次为产品标识、文件名、原生系统、预处理器、整数字长……模型比例（第 13 个）、
单位标志（第 14 个）与单位名（第 15 个）。
"""

from __future__ import annotations

import re
from typing import Any

_HOLLERITH = re.compile(r"(\d+)H")

# IGES 全局节第 14 参数（单位标志）的标准取值
_UNIT_FLAGS = {
    1: "INCH",
    2: "MILLIMETRE",
    4: "FOOT",
    5: "MILE",
    6: "METRE",
    7: "KILOMETRE",
    8: "MIL",
    9: "MICROMETRE",
    10: "CENTIMETRE",
    11: "MICROINCH",
}

_UNIT_NAMES = {
    "in": "INCH", "inch": "INCH", "inches": "INCH",
    "mm": "MILLIMETRE", "millimeter": "MILLIMETRE", "millimetre": "MILLIMETRE",
    "cm": "CENTIMETRE", "centimeter": "CENTIMETRE", "centimetre": "CENTIMETRE",
    "m": "METRE", "meter": "METRE", "metre": "METRE",
    "ft": "FOOT", "foot": "FOOT", "feet": "FOOT",
    "um": "MICROMETRE", "micron": "MICROMETRE", "micrometer": "MICROMETRE",
}


def _sections(text: str) -> dict[str, str]:
    """按第 73 列的分节字母把 80 列记录归并为 S/G 两节。"""
    out: dict[str, list[str]] = {"S": [], "G": []}
    for line in text.splitlines():
        if len(line) < 73:
            continue
        letter = line[72:73].upper()
        if letter in out:
            out[letter].append(line[:72])
    return {k: "".join(v) for k, v in out.items()}


def _read_hollerith(text: str, pos: int) -> tuple[str | None, int]:
    m = _HOLLERITH.match(text, pos)
    if not m:
        return None, pos
    n = int(m.group(1))
    start = m.end()
    return text[start:start + n], start + n


def _split_params(body: str, delimiter: str = ",") -> list[str]:
    """按分隔符切分全局节参数；Hollerith 串内部的分隔符不切。"""
    params: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(body):
        m = _HOLLERITH.match(body, i)
        if m:
            n = int(m.group(1))
            token = body[i:m.end() + n]
            buf.append(token)
            i = m.end() + n
            continue
        ch = body[i]
        if ch == delimiter:
            params.append("".join(buf).strip())
            buf = []
        elif ch in (";",):
            # 记录分隔符之后不再有参数
            break
        else:
            buf.append(ch)
        i += 1
    if buf:
        params.append("".join(buf).strip())
    return params


def _decode_param(raw: str | None) -> str | None:
    """把 `6HASSEMB` 这类 Hollerith 参数还原为普通字符串。"""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    value, _ = _read_hollerith(raw, 0)
    if value is not None:
        return value.strip() or None
    return raw.strip("'\" ") or None


def parse_iges_header(text: str) -> dict[str, Any]:
    """解析 IGES 头段，返回与 STEP 头段同构的元数据字典。"""
    sec = _sections(text)
    start = sec.get("S", "").strip()
    glob = sec.get("G", "")

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
        "scale": None,
    }

    m = re.search(r"IGES\s*(5\.\d)", start, re.I)
    if m:
        meta["schema"] = f"IGES {m.group(1)}"
        meta["implementation_level"] = "IGES"

    if not glob:
        return meta

    # 前两个参数是分隔符与记录分隔符，例如 "1H,,1H;"
    delim, pos = _read_hollerith(glob, 0)
    term, pos2 = _read_hollerith(glob, pos)
    delimiter = delim or ","
    rest = glob[pos2:]
    params = _split_params(rest, delimiter)

    def param(idx: int) -> str | None:
        return params[idx] if idx < len(params) else None

    # 参数序号（1 起）：4 产品标识、5 文件名、6 原生系统、7 预处理器、13 比例、14 单位、15 单位名
    meta["name"] = _decode_param(param(3))
    filename = _decode_param(param(4))
    meta["source_filename"] = filename
    meta["preprocessor"] = _decode_param(param(6))
    meta["description"] = _decode_param(param(2))

    scale_raw = (param(12) or "").strip()
    try:
        meta["scale"] = float(scale_raw) if scale_raw else None
    except ValueError:
        meta["scale"] = None

    flag_raw = (param(13) or "").strip()
    unit_name = _decode_param(param(14))
    units = None
    try:
        units = _UNIT_FLAGS.get(int(flag_raw))
    except (TypeError, ValueError):
        units = None
    if units is None and unit_name:
        units = _UNIT_NAMES.get(unit_name.strip().lower())
    meta["units"] = units
    meta["unit_name"] = unit_name

    stamp = _decode_param(param(17))
    if stamp and re.search(r"\d{2,4}", stamp):
        meta["timestamp"] = stamp

    return meta
