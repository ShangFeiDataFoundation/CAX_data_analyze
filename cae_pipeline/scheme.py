"""Fluent 设置段（Scheme 文本）解析。

Fluent CFF 的 ``.cas.h5`` 把求解设置以 Scheme 序列化文本存在 ``/settings/*``，
形如::

    (39 (17 fluid fluid_top 1)(
      (material . water-liquid)
      (sources? . #f)
      (physical-time-step-expr "4e-09")))

本模块提供最小可用的 S 表达式读取工具：分词、解析、关联表(dotted pair)转字典、
节点遍历。不做求值，只做结构化提取。
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Sequence


class Sym(str):
    """Scheme 符号（与字符串字面量区分）。"""

    __slots__ = ()


class Vector(list):
    """``#(...)`` 向量，语义上等同列表。"""


_TOKEN_RE = re.compile(
    r"""
    (?P<lparen>\() |
    (?P<rparen>\)) |
    (?P<vector>\#\() |
    (?P<string>"(?:[^"\\]|\\.)*") |
    (?P<atom>[^\s()"]+)
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(text)]


def _atom(tok: str) -> Any:
    if tok.startswith('"'):
        return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if tok == "#t":
        return True
    if tok == "#f":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return Sym(tok)


def parse(text: str) -> list[Any]:
    """解析文本，返回全部顶层表达式。"""
    tokens = tokenize(text)
    pos = 0

    def parse_expr() -> Any:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("S 表达式意外结束")
        tok = tokens[pos]
        pos += 1
        if tok in ("(", "#("):
            out: list[Any] = Vector() if tok == "#(" else []
            while pos < len(tokens) and tokens[pos] != ")":
                out.append(parse_expr())
            if pos >= len(tokens):
                raise ValueError("缺少右括号")
            pos += 1  # 跳过右括号
            return out
        if tok == ")":
            raise ValueError("多余的右括号")
        return _atom(tok)

    forms: list[Any] = []
    while pos < len(tokens):
        forms.append(parse_expr())
    return forms


def clean_name(key: Any) -> str:
    return str(key).lstrip(":").strip()


def _looks_like_alist(node: Sequence[Any]) -> bool:
    if not node:
        return False
    for item in node:
        if not (isinstance(item, list) and item and isinstance(item[0], (Sym, str))):
            return False
    return True


def sexp_to_dict(node: Any) -> dict[str, Any]:
    """把关联表 (k . v) / (k v) / (k) 转成字典；非关联表原样返回列表。"""
    if not isinstance(node, list):
        return {}
    out: dict[str, Any] = {}
    for item in node:
        if not isinstance(item, list) or not item:
            continue
        key = clean_name(item[0])
        if len(item) >= 3 and isinstance(item[1], Sym) and item[1] == ".":
            value = item[2]
        elif len(item) >= 3:
            value = item[1:]
        elif len(item) == 2:
            value = item[1]
        else:
            value = None
        if isinstance(value, list) and value and isinstance(value[0], list) and _looks_like_alist(value):
            value = sexp_to_dict(value)
        out[key] = value
    return out


def walk(node: Any) -> Iterator[Any]:
    """深度优先遍历所有子节点（含自身）。"""
    yield node
    if isinstance(node, list):
        for child in node:
            yield from walk(child)


def _pair_value(sub: list) -> tuple[bool, Any]:
    if not sub or not isinstance(sub[0], (Sym, str)):
        return False, None
    if len(sub) >= 3 and isinstance(sub[1], Sym) and sub[1] == ".":
        return True, sub[2]
    if len(sub) == 2:
        return True, sub[1]
    return False, None


def find_key(node: Any, key: str, default: Any = None) -> Any:
    """在任意深度查找第一个 ``(key . value)`` 的值。"""
    key = clean_name(key)
    for sub in walk(node):
        if isinstance(sub, list) and sub and clean_name(sub[0]) == key:
            ok, value = _pair_value(sub)
            if ok:
                return value
    return default


def find_all_key(node: Any, key: str) -> list[Any]:
    key = clean_name(key)
    out: list[Any] = []
    for sub in walk(node):
        if isinstance(sub, list) and sub and clean_name(sub[0]) == key:
            ok, value = _pair_value(sub)
            if ok:
                out.append(value)
    return out


def thread_blocks(text: str) -> list[dict[str, Any]]:
    """解析 ``Thread Variables``：返回每个 (zone, phase) 设置块。

    每块形如 ``(39 (17 fluid fluid_top 1)( (material . water-liquid) ... ))``。
    """
    blocks: list[dict[str, Any]] = []
    for form in parse(text):
        if not isinstance(form, list) or len(form) < 3:
            continue
        header = form[1]
        if not (isinstance(header, list) and len(header) == 4):
            continue
        zone_id, kind, name, phase = header
        body = sexp_to_dict(form[2]) if isinstance(form[2], list) else {}
        blocks.append(
            {
                "section": form[0],
                "zone_id": zone_id,
                "kind": clean_name(kind),
                "name": clean_name(name),
                "phase": phase,
                "settings": body,
            }
        )
    return blocks
